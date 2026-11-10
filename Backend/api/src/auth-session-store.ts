import {createCipheriv,createDecipheriv,randomBytes} from 'node:crypto';
import {chmodSync,existsSync,mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';

export type AuthSession={access:string;refresh:string;subject:string;expiresAt:number;userId:string;endsAt:number};
export type AuthLogin={binding:string;verifier:string;nonce:string;returnTo:string;endsAt:number};
type States={session:AuthSession;login:AuthLogin};
type Kind=keyof States;
export type AuthState<K extends Kind>={revision:string;value:States[K]};
export interface AuthSessionStore {
 get<K extends Kind>(kind:K,key:string):AuthState<K>|undefined;
 take<K extends Kind>(kind:K,key:string):AuthState<K>|undefined;
 create<K extends Kind>(kind:K,key:string,value:States[K]):boolean;
 replace<K extends Kind>(kind:K,key:string,revision:string,value:States[K]):boolean;
 delete(kind:Kind,key:string,revision?:string):void;
 prune(now:number):void;
 count(kind:Kind):number;
 close():void;
}
const revision=()=>randomBytes(32).toString('base64url');
const clone=<T>(value:T):T=>structuredClone(value);
/** Isolated default for tests and explicitly constructed authentication services. */
export class MemoryAuthSessionStore implements AuthSessionStore {
 private values=new Map<string,{kind:Kind;state:AuthState<Kind>}>();
 private id(kind:Kind,key:string){return kind+':'+key;}
 get<K extends Kind>(kind:K,key:string){return clone(this.values.get(this.id(kind,key))?.state as AuthState<K>|undefined);}
 take<K extends Kind>(kind:K,key:string){const state=this.get(kind,key);this.delete(kind,key);return state;}
 create<K extends Kind>(kind:K,key:string,value:States[K]){
  const id=this.id(kind,key);if(this.values.has(id))return false;
  this.values.set(id,{kind,state:{revision:revision(),value:clone(value)}});return true;
 }
 replace<K extends Kind>(kind:K,key:string,expected:string,value:States[K]){
  const id=this.id(kind,key);if(this.values.get(id)?.state.revision!==expected)return false;
  this.values.set(id,{kind,state:{revision:revision(),value:clone(value)}});return true;
 }
 delete(kind:Kind,key:string,expected?:string){const id=this.id(kind,key);if(!expected||this.values.get(id)?.state.revision===expected)this.values.delete(id);}
 prune(now:number){for(const [key,entry] of this.values)if(entry.state.value.endsAt<=now)this.values.delete(key);}
 count(kind:Kind){return [...this.values.values()].filter(entry=>entry.kind===kind).length;}
 close(){this.values.clear();}
}
function valid(kind:Kind,value:any):value is States[Kind]{
 if(!value||typeof value!=='object'||Array.isArray(value)||!Number.isSafeInteger(value.endsAt)||value.endsAt<=0)return false;
 const text=(key:string,max:number)=>typeof value[key]==='string'&&value[key].length>0&&value[key].length<=max;
 return kind==='session'?text('access',32768)&&text('refresh',32768)&&text('subject',512)&&text('userId',512)&&Number.isSafeInteger(value.expiresAt)&&value.expiresAt>0:
  ['binding','verifier','nonce'].every(key=>text(key,43)&&/^[A-Za-z0-9_-]{43}$/.test(value[key]))&&text('returnTo',4096);
}
/** Single-host authentication state on the same persistent volume as workspace data.
 * Only hashed opaque identifiers and expiry metadata are plaintext; tokens and PKCE
 * secrets are authenticated ciphertext protected by an independent private key.
 */
export class SqliteAuthSessionStore implements AuthSessionStore {
 private db:DatabaseSync;
 private key:Buffer;
 private closed=false;
 constructor(directory:string){
  mkdirSync(directory,{recursive:true,mode:0o700});
  const keyPath=join(directory,'auth-session.key'),dbPath=join(directory,'auth-sessions.sqlite');
  if(!existsSync(keyPath)&&existsSync(dbPath))throw new Error('로그인 보호 키를 찾을 수 없습니다. 영속 데이터 폴더를 확인하세요.');
  try{writeFileSync(keyPath,randomBytes(32),{flag:'wx',mode:0o600});}catch(error:any){if(error.code!=='EEXIST')throw error;}
  chmodSync(keyPath,0o600);this.key=readFileSync(keyPath);
  if(this.key.length!==32)throw new Error('로그인 보호 키가 올바르지 않습니다.');
  // Create privately before SQLite opens its database and journal files.
  try{writeFileSync(dbPath,'',{flag:'wx',mode:0o600});}catch(error:any){if(error.code!=='EEXIST')throw error;}
  chmodSync(dbPath,0o600);this.db=new DatabaseSync(dbPath);
  this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
   CREATE TABLE IF NOT EXISTS auth_state(kind TEXT NOT NULL CHECK(kind IN ('session','login')),id TEXT NOT NULL,
   revision TEXT NOT NULL,ends_at INTEGER NOT NULL,encrypted TEXT NOT NULL,PRIMARY KEY(kind,id));
   CREATE INDEX IF NOT EXISTS auth_state_expiry ON auth_state(ends_at);`);
 }
 private seal(kind:Kind,key:string,id:string,value:States[Kind]){
  if(!valid(kind,value))throw new Error('저장할 로그인 정보를 확인할 수 없습니다.');
  const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',this.key,iv);
  cipher.setAAD(Buffer.from(JSON.stringify([kind,key,id,value.endsAt])));
  const encrypted=Buffer.concat([cipher.update(JSON.stringify(value),'utf8'),cipher.final()]);
  return [iv,cipher.getAuthTag(),encrypted].map(part=>part.toString('base64url')).join('.');
 }
 private open<K extends Kind>(kind:K,key:string,row:Record<string,any>|undefined):AuthState<K>|undefined{
  if(!row)return;
  try{
   const parts=String(row.encrypted).split('.');if(parts.length!==3||parts.some(part=>!/^[-A-Za-z0-9_]+$/.test(part))||String(row.encrypted).length>100000)throw new Error();
   const [iv,tag,encrypted]=parts.map(part=>Buffer.from(part,'base64url'));if(iv.length!==12||tag.length!==16)throw new Error();
   const decipher=createDecipheriv('aes-256-gcm',this.key,iv);
   decipher.setAAD(Buffer.from(JSON.stringify([kind,key,row.revision,row.ends_at])));decipher.setAuthTag(tag);
   const value=JSON.parse(Buffer.concat([decipher.update(encrypted),decipher.final()]).toString('utf8'));
   if(!valid(kind,value)||value.endsAt!==row.ends_at)throw new Error();
   return {revision:String(row.revision),value:value as States[K]};
  }catch{this.delete(kind,key,String(row.revision));return;}
 }
 get<K extends Kind>(kind:K,key:string){return this.open(kind,key,this.db.prepare('SELECT revision,ends_at,encrypted FROM auth_state WHERE kind=? AND id=?').get(kind,key));}
 take<K extends Kind>(kind:K,key:string){
  // DELETE RETURNING consumes the login attempt atomically before a token exchange.
  return this.open(kind,key,this.db.prepare('DELETE FROM auth_state WHERE kind=? AND id=? RETURNING revision,ends_at,encrypted').get(kind,key));
 }
 create<K extends Kind>(kind:K,key:string,value:States[K]){
  const id=revision(),encrypted=this.seal(kind,key,id,value);
  return this.db.prepare('INSERT OR IGNORE INTO auth_state(kind,id,revision,ends_at,encrypted) VALUES(?,?,?,?,?)').run(kind,key,id,value.endsAt,encrypted).changes>0;
 }
 replace<K extends Kind>(kind:K,key:string,expected:string,value:States[K]){
  const id=revision(),encrypted=this.seal(kind,key,id,value);
  return this.db.prepare('UPDATE auth_state SET revision=?,ends_at=?,encrypted=? WHERE kind=? AND id=? AND revision=?').run(id,value.endsAt,encrypted,kind,key,expected).changes>0;
 }
 delete(kind:Kind,key:string,expected?:string){
  if(expected)this.db.prepare('DELETE FROM auth_state WHERE kind=? AND id=? AND revision=?').run(kind,key,expected);
  else this.db.prepare('DELETE FROM auth_state WHERE kind=? AND id=?').run(kind,key);
 }
 prune(now:number){this.db.prepare('DELETE FROM auth_state WHERE ends_at<=?').run(now);}
 count(kind:Kind){return Number(this.db.prepare('SELECT count(*) AS total FROM auth_state WHERE kind=?').get(kind)!.total);}
 close(){if(!this.closed){this.closed=true;this.db.close();this.key.fill(0);}}
}
