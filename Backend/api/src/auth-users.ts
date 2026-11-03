import {DatabaseSync} from 'node:sqlite';
import {mkdirSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {checkServerIdentity,type ConnectionOptions,type PeerCertificate} from 'node:tls';
import pg from 'pg';
import type {AuthConfig} from './auth-config';

export type AuthUser={id:string;email:string;name:string;role:'admin'|'member';status:'active'|'disabled'};
export type AuthProfile={provider:string;subject:string;email:string;name:string};
export interface AuthUsers {
 provision(profile:AuthProfile):Promise<AuthUser>;
 get(id:string):Promise<AuthUser|undefined>;
 close():Promise<void>;
}
const columns='id,email,name,role,status';
export function postgresTls(config:Pick<AuthConfig,'databaseCaFile'|'databaseServerName'>):ConnectionOptions {
 const expected=config.databaseServerName;
 return {rejectUnauthorized:true,...(config.databaseCaFile?{ca:readFileSync(config.databaseCaFile,'utf8')}:{}),...(expected?{
  servername:expected,
  // pg sets SNI from its TCP host after merging SSL options. A tunnel's TCP
  // hostname must still be checked against the actual RDS certificate identity.
  checkServerIdentity:(_host:string,certificate:PeerCertificate)=>checkServerIdentity(expected,certificate),
 }:{})};
}
/** Uses the existing RDS a4b.users table; never associates accounts by email alone. */
export class PostgresAuthUsers implements AuthUsers {
 private pool:pg.Pool;
 constructor(config:AuthConfig){
  this.pool=new pg.Pool({connectionString:config.databaseUrl,max:5,connectionTimeoutMillis:5000,
   ssl:postgresTls(config)});
  // Pool errors must not print connection strings or terminate the app.
  this.pool.on('error',()=>console.error('회원 데이터베이스 연결이 종료되었습니다.'));
 }
 async provision(p:AuthProfile){
  const result=await this.pool.query<AuthUser>(`INSERT INTO a4b.users(email,name,auth_provider,auth_subject) VALUES($1,$2,$3,$4)
   ON CONFLICT(auth_provider,auth_subject) DO UPDATE SET email=EXCLUDED.email,name=EXCLUDED.name RETURNING ${columns}`,[p.email,p.name,p.provider,p.subject]);
  return result.rows[0];
 }
 async get(id:string){return (await this.pool.query<AuthUser>(`SELECT ${columns} FROM a4b.users WHERE id=$1`,[id])).rows[0];}
 async close(){await this.pool.end();}
}
/** Explicit local-development option; production always uses PostgreSQL with TLS. */
export class SqliteAuthUsers implements AuthUsers {
 private db:DatabaseSync;
 constructor(directory:string){
  mkdirSync(directory,{recursive:true,mode:0o700});this.db=new DatabaseSync(join(directory,'auth-users.sqlite'));
  this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
   CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,name TEXT NOT NULL,
   auth_provider TEXT NOT NULL,auth_subject TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'member',status TEXT NOT NULL DEFAULT 'active',
   UNIQUE(auth_provider,auth_subject));`);
 }
 async provision(p:AuthProfile){
  this.db.prepare(`INSERT INTO users(id,email,name,auth_provider,auth_subject) VALUES(?,?,?,?,?)
   ON CONFLICT(auth_provider,auth_subject) DO UPDATE SET email=excluded.email,name=excluded.name`).run(randomUUID(),p.email,p.name,p.provider,p.subject);
  return this.db.prepare(`SELECT ${columns} FROM users WHERE auth_provider=? AND auth_subject=?`).get(p.provider,p.subject) as AuthUser;
 }
 async get(id:string){return this.db.prepare(`SELECT ${columns} FROM users WHERE id=?`).get(id) as AuthUser|undefined;}
 async close(){this.db.close();}
}
