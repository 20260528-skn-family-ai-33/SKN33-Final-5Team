import {DatabaseSync} from 'node:sqlite';
import {createCipheriv,createDecipheriv,randomBytes,randomUUID} from 'node:crypto';
import {mkdirSync,readFileSync,writeFileSync,chmodSync} from 'node:fs';
import {join} from 'node:path';
import {clone,validateFlow,type Flow,type Json,type Execution} from '../lib/workflow';
import type {CredentialInput,CredentialPublic,CredentialSecret,Provider} from './contracts';

const providers=new Set<Provider>(['openai','slack','bearer','basic','header','jwt']);
/** Own local SQLite database. Credentials are encrypted with a separate, private key. */
export class Store {
 private db:DatabaseSync;
 private key:Buffer;
 constructor(directory:string) {
  mkdirSync(directory,{recursive:true,mode:0o700});
  const keyPath=join(directory,'credential.key');
  try {writeFileSync(keyPath,randomBytes(32),{flag:'wx',mode:0o600});}catch(error:any){if(error.code!=='EEXIST')throw error;}
  chmodSync(keyPath,0o600);this.key=readFileSync(keyPath);
  if(this.key.length!==32)throw new Error('연결 정보 보호 키가 올바르지 않아요. 기존 데이터 폴더를 확인해주세요.');
  this.db=new DatabaseSync(join(directory,'easyflow.sqlite'));
  this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
   CREATE TABLE IF NOT EXISTS assistant_sessions(id TEXT PRIMARY KEY,json TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS flows(id TEXT PRIMARY KEY,json TEXT NOT NULL,updated_at TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS credentials(id TEXT PRIMARY KEY,name TEXT NOT NULL,provider TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,encrypted TEXT NOT NULL,service TEXT);
   CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,workflow_id TEXT NOT NULL,json TEXT NOT NULL,flow TEXT NOT NULL,input TEXT NOT NULL,dedupe_key TEXT UNIQUE,started_at TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS webhook_responses(execution_id TEXT PRIMARY KEY,body BLOB NOT NULL,mime TEXT NOT NULL,file_name TEXT NOT NULL,FOREIGN KEY(execution_id) REFERENCES executions(id) ON DELETE CASCADE);
   CREATE INDEX IF NOT EXISTS execution_workflow ON executions(workflow_id,started_at);`);
  if(!this.db.prepare('PRAGMA table_info(credentials)').all().some(row=>row.name==='service'))this.db.exec('ALTER TABLE credentials ADD COLUMN service TEXT');
  if(!this.db.prepare('PRAGMA table_info(credentials)').all().some(row=>row.name==='auth_type'))this.db.exec('ALTER TABLE credentials ADD COLUMN auth_type TEXT');
 }
 createAssistantSession(context?:Flow){const s={id:randomUUID(),revision:0,conversation:[],result:null,context:context?validateFlow(clone(context)):null};this.db.prepare('INSERT INTO assistant_sessions(id,json) VALUES(?,?)').run(s.id,JSON.stringify(s));return s;}
 getAssistantSession(id:string):any {const r=this.db.prepare('SELECT json FROM assistant_sessions WHERE id=?').get(id);if(!r)throw new Error('대화를 찾지 못했어요.');return JSON.parse(String(r.json));}
 saveAssistantSession(value:any,revision:number){const prior=this.getAssistantSession(value.id);if(prior.revision!==revision)throw new Error('대화가 변경됐어요. 다시 열고 확인해주세요.');value.revision=revision+1;const update=this.db.prepare('UPDATE assistant_sessions SET json=? WHERE id=? AND json=?').run(JSON.stringify(value),value.id,JSON.stringify(prior));if(!update.changes)throw new Error('대화가 변경됐어요.');return value;}
 listFlows():Flow[] {return this.db.prepare('SELECT json FROM flows ORDER BY updated_at DESC').all().map(row=>JSON.parse(String(row.json)));}
 getFlow(id:string):Flow|undefined {const row=this.db.prepare('SELECT json FROM flows WHERE id=?').get(id);return row?JSON.parse(String(row.json)):undefined;}
 saveFlow(value:Flow):Flow {const flow={...validateFlow(clone(value)),updatedAt:new Date().toISOString()};this.db.prepare('INSERT INTO flows(id,json,updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET json=excluded.json,updated_at=excluded.updated_at').run(flow.id,JSON.stringify(flow),flow.updatedAt);return flow;}
 deleteFlow(id:string):boolean {return this.db.prepare('DELETE FROM flows WHERE id=?').run(id).changes>0;}
 private public(row:Record<string,any>):CredentialPublic {return {id:String(row.id),name:String(row.name),provider:row.provider as Provider,...(row.service?{service:String(row.service)}:{}),...(row.auth_type==='oauth2'?{authType:'oauth2' as const}:{}),createdAt:String(row.created_at),updatedAt:String(row.updated_at)};}
 listCredentials():CredentialPublic[] {return this.db.prepare('SELECT id,name,provider,service,auth_type,created_at,updated_at FROM credentials ORDER BY updated_at DESC').all().map(row=>this.public(row));}
 getCredential(id:string):CredentialSecret {
  const row=this.db.prepare('SELECT * FROM credentials WHERE id=?').get(id);
  if(!row)throw new Error('선택한 연결 정보를 찾을 수 없어요. 다시 연결해주세요.');
  try {const box=JSON.parse(String(row.encrypted));const decipher=createDecipheriv('aes-256-gcm',this.key,Buffer.from(box.iv,'base64'));decipher.setAuthTag(Buffer.from(box.tag,'base64'));const data=JSON.parse(Buffer.concat([decipher.update(Buffer.from(box.value,'base64')),decipher.final()]).toString('utf8'));return {...this.public(row),data};}
  catch {throw new Error('저장한 연결 정보를 열 수 없어요. 보호 키와 데이터 폴더를 확인해주세요.');}
 }
 saveCredential(input:CredentialInput):CredentialPublic {
  if(!input||typeof input.name!=='string'||!input.name.trim()||!providers.has(input.provider)||!input.data||typeof input.data!=='object'||Array.isArray(input.data))throw new Error('연결 이름, 종류, 필요한 정보를 입력해주세요.');
  const id=input.id||randomUUID(),existing=this.db.prepare('SELECT created_at FROM credentials WHERE id=?').get(id),now=new Date().toISOString(),createdAt=existing?String(existing.created_at):now;
  const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',this.key,iv),value=Buffer.concat([cipher.update(JSON.stringify(input.data),'utf8'),cipher.final()]);
  const encrypted=JSON.stringify({iv:iv.toString('base64'),tag:cipher.getAuthTag().toString('base64'),value:value.toString('base64')});
  this.db.prepare('INSERT INTO credentials(id,name,provider,created_at,updated_at,encrypted,service,auth_type) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,provider=excluded.provider,updated_at=excluded.updated_at,encrypted=excluded.encrypted,service=excluded.service,auth_type=excluded.auth_type').run(id,input.name.trim(),input.provider,createdAt,now,encrypted,typeof input.data.service==='string'?input.data.service:null,input.data.authType==='oauth2'?'oauth2':null);
  return {id,name:input.name.trim(),provider:input.provider,...(typeof input.data.service==='string'?{service:input.data.service}:{}),...(input.data.authType==='oauth2'?{authType:'oauth2' as const}:{}),createdAt,updatedAt:now};
 }
 deleteCredential(id:string):boolean {return this.db.prepare('DELETE FROM credentials WHERE id=?').run(id).changes>0;}
 listExecutions():Execution[] {return this.db.prepare('SELECT json FROM executions ORDER BY started_at DESC LIMIT 500').all().map(row=>JSON.parse(String(row.json)));}
 listPendingExecutions():Execution[] {return this.db.prepare("SELECT json FROM executions WHERE json_extract(json,'$.status') IN ('queued','running','waiting') ORDER BY started_at ASC").all().map(row=>JSON.parse(String(row.json)));}
 getExecution(id:string):Execution|undefined {const row=this.db.prepare('SELECT json FROM executions WHERE id=?').get(id);return row?JSON.parse(String(row.json)):undefined;}
 saveWebhookResponse(id:string,bytes:Buffer,mime:string,fileName:string):void {if(bytes.length>48*1024*1024)throw new Error('저장할 Webhook 응답 크기 제한을 넘었어요.');this.db.prepare('INSERT INTO webhook_responses(execution_id,body,mime,file_name) VALUES(?,?,?,?) ON CONFLICT(execution_id) DO NOTHING').run(id,bytes,mime,fileName);}
 getWebhookResponse(id:string):{bytes:Buffer;mime:string;fileName:string}|undefined {const row=this.db.prepare('SELECT body,mime,file_name FROM webhook_responses WHERE execution_id=?').get(id);return row?{bytes:Buffer.from(row.body as Uint8Array),mime:String(row.mime),fileName:String(row.file_name)}:undefined;}
 reserveExecution(execution:Execution,flow:Flow,input:Json[],dedupeKey?:string):boolean {
  const result=this.db.prepare('INSERT OR IGNORE INTO executions(id,workflow_id,json,flow,input,dedupe_key,started_at) VALUES(?,?,?,?,?,?,?)').run(execution.id,flow.id,JSON.stringify(execution),JSON.stringify(flow),JSON.stringify(input),dedupeKey??null,execution.startedAt);
  return result.changes>0;
 }
 saveExecution(execution:Execution):void {const result=this.db.prepare('UPDATE executions SET json=? WHERE id=?').run(JSON.stringify(execution),execution.id);if(!result.changes)throw new Error('저장할 실행 기록을 찾을 수 없어요.');}
 getExecutionSnapshot(id:string):{flow:Flow;input:Json[]}|undefined {const row=this.db.prepare('SELECT flow,input FROM executions WHERE id=?').get(id);return row?{flow:JSON.parse(String(row.flow)),input:JSON.parse(String(row.input))}:undefined;}
 close():void {this.db.close();this.key.fill(0);}
}
