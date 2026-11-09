import {DatabaseSync} from 'node:sqlite';
import {mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import type {AuthConfig} from './auth-config';
import {postgresTls,type AuthUser,type AuthUsers} from './auth-users';
import {AuthError} from './cognito-auth';

export type TeamRole='admin'|'editor'|'viewer';
export type TeamSummary={id:string;name:string;role:TeamRole;created_at:string};
type Row=Record<string,any>;
type Transaction={query:(sql:string,values?:any[])=>Promise<Row[]>;authorize:(id:string)=>Promise<void>;lock:(id:string)=>Promise<void>};
const uuid=/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
export function teamId(value:unknown){if(typeof value!=='string'||!uuid.test(value))throw new AuthError(400,'팀 또는 회원 ID를 확인해주세요.');return value.toLowerCase();}
function role(value:unknown):TeamRole{if(typeof value!=='string'||!['admin','editor','viewer'].includes(value))throw new AuthError(400,'팀 권한을 확인해주세요.');return value as TeamRole;}
function email(value:unknown){const normalized=typeof value==='string'?value.trim().toLowerCase():'';if(!normalized||normalized.length>254||!/^[^\s@]+@[^\s@]+\.[^\s@]+$/u.test(normalized)||/[\p{C}]/u.test(normalized))throw new AuthError(400,'초대할 이메일 주소를 확인해주세요.');return normalized;}
function name(value:unknown){const result=typeof value==='string'?value.normalize('NFC').trim():'';if(typeof value!=='string'||!result||[...result].length>80||/[\p{C}\u2028\u2029]/u.test(value))throw new AuthError(400,'팀 이름을 한 줄로 1~80자 입력해주세요.');return result;}
const schema=`CREATE TABLE IF NOT EXISTS teams(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS team_members(team_id TEXT NOT NULL REFERENCES teams(id),user_id TEXT NOT NULL,email TEXT NOT NULL,name TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')),joined_at TEXT NOT NULL,PRIMARY KEY(team_id,user_id));
 CREATE INDEX IF NOT EXISTS team_members_user ON team_members(user_id);
 CREATE TABLE IF NOT EXISTS team_invitations(id TEXT PRIMARY KEY,team_id TEXT NOT NULL REFERENCES teams(id),email TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')),status TEXT NOT NULL CHECK(status IN ('pending','accepted','declined','cancelled','expired')),
 invited_by TEXT NOT NULL,inviter_name TEXT NOT NULL,inviter_email TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,responded_at TEXT);
 CREATE UNIQUE INDEX IF NOT EXISTS team_pending_invitation ON team_invitations(team_id,email) WHERE status='pending';
 CREATE INDEX IF NOT EXISTS team_invitations_email ON team_invitations(email,status);`;

/** Central membership store. It never reads credentials or sends invitation emails. */
export class TeamService {
 private sqlite?:DatabaseSync;private pool?:pg.Pool;private queue:Promise<unknown>=Promise.resolve();
 constructor(directory:string,private users:AuthUsers,config?:AuthConfig,private now:()=>number=Date.now){
  if(config?.userStore==='postgres'){
   this.pool=new pg.Pool({connectionString:config.databaseUrl,max:4,connectionTimeoutMillis:5000,ssl:postgresTls(config)});
   this.pool.on('error',()=>{});
  }else{mkdirSync(directory,{recursive:true,mode:0o700});this.sqlite=new DatabaseSync(join(directory,'teams.sqlite'));this.sqlite.exec('PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;'+schema);}
 }
 private async transaction<T>(user:AuthUser,action:(tx:Transaction)=>Promise<T>):Promise<T>{
  if(user.status!=='active')throw new AuthError(403,'사용할 수 없는 회원입니다.');
  teamId(user.id);const actorEmail=email(user.email);
  if(this.pool){
   const client=await this.pool.connect();
   try{
    await client.query('BEGIN');
    await client.query("SELECT set_config('a4b.user_id',$1,true),set_config('a4b.user_email',$2,true),set_config('a4b.team_id','',true)",[user.id,actorEmail]);
    const tx:Transaction={query:async(sql,values=[])=>{let i=0;return (await client.query(sql.replace(/\b(teams|team_members|team_invitations)\b/g,'a4b_builder.$1').replace(/\?/g,()=>'$'+(++i)),values)).rows;},
     authorize:async id=>{await client.query("SELECT set_config('a4b.team_id',$1,true)",[id]);},
     lock:async id=>{await client.query('SELECT pg_advisory_xact_lock(hashtext($1))',['a4b.team:'+id]);}};
    const result=await action(tx);await client.query('COMMIT');return result;
   }catch(error){await client.query('ROLLBACK').catch(()=>{});throw error;}finally{client.release();}
  }
  const run=async()=>{
   this.sqlite!.exec('BEGIN IMMEDIATE');
   const tx:Transaction={query:async(sql,values=[])=>{
    const statement=this.sqlite!.prepare(sql);return /^\s*(SELECT|WITH)/i.test(sql)?statement.all(...values):((statement.run(...values)),[]);
   },authorize:async()=>{},lock:async()=>{}};
   try{const result=await action(tx);this.sqlite!.exec('COMMIT');return result;}catch(error){this.sqlite!.exec('ROLLBACK');throw error;}
  };
  const result=this.queue.then(run,run);this.queue=result.catch(()=>{});return result;
 }
 private stamp(){return new Date(this.now()).toISOString();}
 private async require(tx:Transaction,user:AuthUser,id:string,write=false,admin=false):Promise<TeamSummary>{
  const [membership]=await tx.query('SELECT role FROM team_members WHERE team_id=? AND user_id=?',[id,user.id]);
  if(!membership)throw new AuthError(404,'접근할 수 있는 팀을 찾을 수 없습니다.');
  if(admin&&membership.role!=='admin'||write&&membership.role==='viewer')throw new AuthError(403,admin?'팀 관리자만 변경할 수 있습니다.':'자료를 변경할 권한이 없습니다.');
  await tx.authorize(id);
  const [team]=await tx.query('SELECT id,name,created_at FROM teams WHERE id=?',[id]);
  if(!team)throw new AuthError(404,'팀을 찾을 수 없습니다.');
  return {...team,role:membership.role} as TeamSummary;
 }
 async requireTeam(user:AuthUser,id:string,write=false){id=teamId(id);return this.transaction(user,tx=>this.require(tx,user,id,write));}
 async list(user:AuthUser){return this.transaction(user,async tx=>(await tx.query('SELECT t.id,t.name,t.created_at,m.role FROM teams t JOIN team_members m ON m.team_id=t.id WHERE m.user_id=? ORDER BY t.created_at DESC,t.id',[user.id])) as TeamSummary[]);}
 async create(user:AuthUser,value:unknown){const title=name(value);return this.transaction(user,async tx=>{
  const id=randomUUID(),created_at=this.stamp();await tx.authorize(id);
  await tx.query('INSERT INTO teams(id,name,created_by,created_at) VALUES(?,?,?,?)',[id,title,user.id,created_at]);
  await tx.query('INSERT INTO team_members(team_id,user_id,email,name,role,joined_at) VALUES(?,?,?,?,?,?)',[id,user.id,email(user.email),user.name,'admin',created_at]);
  return {id,name:title,created_at,role:'admin' as const};
 });}
 private invitation(row:Row){return {id:row.id,team_id:row.team_id,...(row.team_name?{team_name:row.team_name}:{}),email:row.email,role:row.role,status:row.status,created_at:row.created_at,expires_at:row.expires_at,
  inviter:{id:row.invited_by,name:row.inviter_name,email:row.inviter_email}};}
 async received(user:AuthUser){return this.transaction(user,async tx=>{
  // The invitation carries the team name for an invitee who is not a member yet.
  return (await tx.query("SELECT i.*,t.name AS team_name FROM team_invitations i JOIN teams t ON t.id=i.team_id WHERE i.email=? AND i.status='pending' AND i.expires_at>? ORDER BY i.created_at DESC",[email(user.email),this.stamp()])).map(row=>this.invitation(row));
 });}
 async members(user:AuthUser,id:string){id=teamId(id);const result=await this.transaction(user,async tx=>{
  const team=await this.require(tx,user,id);
  const members=await tx.query('SELECT user_id,email,name,role,joined_at FROM team_members WHERE team_id=? ORDER BY joined_at,user_id',[id]);
  const invitations=team.role==='admin'?(await tx.query("SELECT * FROM team_invitations WHERE team_id=? AND status='pending' AND expires_at>? ORDER BY created_at DESC",[id,this.stamp()])).map(row=>this.invitation(row)):[];
  return {members,invitations};
 });
 const members=await Promise.all(result.members.map(async member=>{
  const profile=await this.users.get(member.user_id);
  return {id:member.user_id,user_id:member.user_id,email:profile?.email||member.email,name:profile?.name||member.name,role:member.role as TeamRole,joined_at:member.joined_at};
 }));return {members,invitations:result.invitations};}
 async invitations(user:AuthUser,id:string){id=teamId(id);return this.transaction(user,async tx=>{
  await this.require(tx,user,id,false,true);return (await tx.query("SELECT * FROM team_invitations WHERE team_id=? AND status='pending' AND expires_at>? ORDER BY created_at DESC",[id,this.stamp()])).map(row=>this.invitation(row));
 });}
 async invite(user:AuthUser,id:string,value:unknown,permission:unknown){id=teamId(id);const target=email(value),assigned=role(permission);return this.transaction(user,async tx=>{
  await tx.lock(id);await this.require(tx,user,id,false,true);
  if(target===email(user.email)||(await tx.query('SELECT 1 FROM team_members WHERE team_id=? AND email=?',[id,target])).length)throw new AuthError(409,'이미 팀에 참여한 회원입니다.');
  const stamp=this.stamp();await tx.query("UPDATE team_invitations SET status='expired',responded_at=? WHERE team_id=? AND email=? AND status='pending' AND expires_at<=?",[stamp,id,target,stamp]);
  if((await tx.query("SELECT 1 FROM team_invitations WHERE team_id=? AND email=? AND status='pending'",[id,target])).length)throw new AuthError(409,'이 이메일에 보낸 초대가 이미 있습니다.');
  const invitation={id:randomUUID(),team_id:id,email:target,role:assigned,status:'pending',invited_by:user.id,inviter_name:user.name,inviter_email:email(user.email),created_at:stamp,expires_at:new Date(this.now()+7*86400000).toISOString()};
  await tx.query('INSERT INTO team_invitations(id,team_id,email,role,status,invited_by,inviter_name,inviter_email,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)',Object.values(invitation));
  return this.invitation(invitation);
 });}
 async respond(user:AuthUser,id:string,accept:boolean){id=teamId(id);return this.transaction(user,async tx=>{
  let [invitation]=await tx.query('SELECT * FROM team_invitations WHERE id=? AND email=?',[id,email(user.email)]);
  if(!invitation)throw new AuthError(404,'받은 초대를 찾을 수 없습니다.');
  await tx.lock(invitation.team_id);
  [invitation]=await tx.query('SELECT * FROM team_invitations WHERE id=? AND email=?',[id,email(user.email)]);
  if(invitation.status!=='pending'||invitation.expires_at<=this.stamp())throw new AuthError(409,'만료되었거나 이미 처리된 초대입니다.');
  await tx.authorize(invitation.team_id);
  if(accept){
   if((await tx.query('SELECT 1 FROM team_members WHERE team_id=? AND user_id=?',[invitation.team_id,user.id])).length)throw new AuthError(409,'이미 팀에 참여하고 있습니다.');
   await tx.query('INSERT INTO team_members(team_id,user_id,email,name,role,joined_at) VALUES(?,?,?,?,?,?)',[invitation.team_id,user.id,email(user.email),user.name,invitation.role,this.stamp()]);
  }
  await tx.query('UPDATE team_invitations SET status=?,responded_at=? WHERE id=?',[accept?'accepted':'declined',this.stamp(),id]);
  return accept?{team:await this.require(tx,user,invitation.team_id)}:{declined:true};
 });}
 async cancel(user:AuthUser,id:string,invitationId:string){id=teamId(id);invitationId=teamId(invitationId);return this.transaction(user,async tx=>{
  await tx.lock(id);await this.require(tx,user,id,false,true);
  const [invitation]=await tx.query('SELECT status FROM team_invitations WHERE id=? AND team_id=?',[invitationId,id]);
  if(!invitation||invitation.status!=='pending')throw new AuthError(404,'취소할 초대를 찾을 수 없습니다.');
  await tx.query("UPDATE team_invitations SET status='cancelled',responded_at=? WHERE id=?",[this.stamp(),invitationId]);return {cancelled:true};
 });}
 async changeMember(user:AuthUser,id:string,userId:string,permission?:unknown){id=teamId(id);userId=teamId(userId);const assigned=permission===undefined?undefined:role(permission);return this.transaction(user,async tx=>{
  await tx.lock(id);await this.require(tx,user,id,false,true);
  const [member]=await tx.query('SELECT role FROM team_members WHERE team_id=? AND user_id=?',[id,userId]);
  if(!member)throw new AuthError(404,'팀 회원을 찾을 수 없습니다.');
  if(member.role==='admin'&&assigned!=='admin'&&(await tx.query("SELECT user_id FROM team_members WHERE team_id=? AND role='admin'",[id])).length<=1)throw new AuthError(409,'마지막 관리자는 삭제하거나 권한을 낮출 수 없습니다.');
  if(assigned)await tx.query('UPDATE team_members SET role=? WHERE team_id=? AND user_id=?',[assigned,id,userId]);
  else await tx.query('DELETE FROM team_members WHERE team_id=? AND user_id=?',[id,userId]);
  return assigned?{member:{id:userId,user_id:userId,role:assigned}}:{removed:true};
 });}
 async close(){await this.queue;this.sqlite?.close();await this.pool?.end();}
}
