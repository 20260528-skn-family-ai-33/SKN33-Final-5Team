import {MemorySaver} from '@langchain/langgraph';
import {DatabaseSync} from 'node:sqlite';
import {serialize,deserialize} from 'node:v8';
import {mkdirSync} from 'node:fs';
import {dirname} from 'node:path';

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
