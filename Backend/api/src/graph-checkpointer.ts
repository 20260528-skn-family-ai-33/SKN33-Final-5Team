import {MemorySaver} from '@langchain/langgraph';
import {DatabaseSync} from 'node:sqlite';
import {serialize,deserialize} from 'node:v8';
import {mkdirSync} from 'node:fs';
import {dirname} from 'node:path';
import type pg from 'pg';

/** Durable local adapter for the pinned MemorySaver. Single server process only. */
export class SqliteCheckpointSaver extends MemorySaver {
 private db:DatabaseSync;
 constructor(path:string){
  super();mkdirSync(dirname(path),{recursive:true,mode:0o700});this.db=new DatabaseSync(path);
  this.db.exec('PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; CREATE TABLE IF NOT EXISTS agent_checkpoints(thread_id TEXT PRIMARY KEY,payload BLOB NOT NULL)');
  for(const row of this.db.prepare('SELECT thread_id,payload FROM agent_checkpoints').all()){
   const state=deserialize(Buffer.from(row.payload as Uint8Array));this.storage[String(row.thread_id)]=state.storage;Object.assign(this.writes,state.writes);
  }
 }
 private persist(id:string){
  const writes=Object.fromEntries(Object.entries(this.writes).filter(([key])=>JSON.parse(key)[0]===id));
  this.db.prepare('INSERT INTO agent_checkpoints(thread_id,payload) VALUES(?,?) ON CONFLICT(thread_id) DO UPDATE SET payload=excluded.payload').run(id,serialize({storage:this.storage[id],writes}));
 }
 override async put(...args:Parameters<MemorySaver['put']>):ReturnType<MemorySaver['put']>{const config=await super.put(...args);this.persist(String(config.configurable?.thread_id));return config;}
 override async putWrites(...args:Parameters<MemorySaver['putWrites']>):ReturnType<MemorySaver['putWrites']>{await super.putWrites(...args);this.persist(String(args[0].configurable?.thread_id));}
 override async deleteThread(id:string){await super.deleteThread(id);this.db.prepare('DELETE FROM agent_checkpoints WHERE thread_id=?').run(id);}
 close(){this.db.close();}
}

/**
 * Same in-memory MemorySaver, persisted to a4b_builder.agent_checkpoints in RDS. Rows are
 * filtered by workspace_id in SQL and again by row-level security on a4b.workspace_id.
 * Loading is asynchronous, so every read and write first waits for it.
 */
export class PostgresCheckpointSaver extends MemorySaver {
 private loading?:Promise<void>;
 private pendingWrites=new Map<string,Promise<void>>();
 constructor(private pool:Pick<pg.Pool,'connect'>,private workspaceId:string){
  super();this.ready().catch(()=>{});
 }
 /** A failed load is forgotten, so the next request retries instead of failing forever. */
 private ready(){
  this.loading??=this.load().catch(error=>{this.loading=undefined;throw error;});
  return this.loading;
 }
 private async query(sql:string,params:unknown[]){
  const client=await this.pool.connect();
  try{
   await client.query('BEGIN');
   await client.query("SELECT set_config('a4b.workspace_id',$1,true)",[this.workspaceId]);
   const result=await client.query(sql,params);await client.query('COMMIT');return result;
  }catch(error){await client.query('ROLLBACK').catch(()=>{});throw error;}
  finally{client.release();}
 }
 private async load(){
  const {rows}=await this.query('SELECT thread_id,payload FROM a4b_builder.agent_checkpoints WHERE workspace_id=$1',[this.workspaceId]);
  for(const row of rows){const state=deserialize(row.payload as Buffer);this.storage[String(row.thread_id)]=state.storage;Object.assign(this.writes,state.writes);}
 }
 private persist(id:string){
  // Snapshot now; writes for one thread are applied in call order.
  const writes=Object.fromEntries(Object.entries(this.writes).filter(([key])=>JSON.parse(key)[0]===id));
  const payload=serialize({storage:this.storage[id],writes});
  const previous=this.pendingWrites.get(id)||Promise.resolve();
  const next=previous.catch(()=>{}).then(()=>this.query('INSERT INTO a4b_builder.agent_checkpoints(workspace_id,thread_id,payload) VALUES($1,$2,$3) ON CONFLICT(workspace_id,thread_id) DO UPDATE SET payload=excluded.payload,updated_at=now()',[this.workspaceId,id,payload])).then(()=>{});
  this.pendingWrites.set(id,next);
  void next.finally(()=>{if(this.pendingWrites.get(id)===next)this.pendingWrites.delete(id);}).catch(()=>{});
  return next;
 }
 override async getTuple(...args:Parameters<MemorySaver['getTuple']>):ReturnType<MemorySaver['getTuple']>{await this.ready();return super.getTuple(...args);}
 override async *list(...args:Parameters<MemorySaver['list']>):ReturnType<MemorySaver['list']>{await this.ready();yield* super.list(...args);}
 override async put(...args:Parameters<MemorySaver['put']>):ReturnType<MemorySaver['put']>{await this.ready();const config=await super.put(...args);await this.persist(String(config.configurable?.thread_id));return config;}
 override async putWrites(...args:Parameters<MemorySaver['putWrites']>):ReturnType<MemorySaver['putWrites']>{await this.ready();await super.putWrites(...args);await this.persist(String(args[0].configurable?.thread_id));}
 override async deleteThread(id:string){
  await this.ready();await super.deleteThread(id);await this.pendingWrites.get(id)?.catch(()=>{});
  await this.query('DELETE FROM a4b_builder.agent_checkpoints WHERE workspace_id=$1 AND thread_id=$2',[this.workspaceId,id]);
 }
 async close(){await Promise.allSettled(this.pendingWrites.values());}
}
