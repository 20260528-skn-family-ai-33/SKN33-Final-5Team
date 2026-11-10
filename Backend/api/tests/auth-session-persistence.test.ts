import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomBytes,randomUUID} from 'node:crypto';
import {mkdtemp,readFile,rm,stat,writeFile} from 'node:fs/promises';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import type {AuthConfig} from '../src/auth-config';
import type {AuthUser,AuthUsers} from '../src/auth-users';
import {CognitoAuth} from '../src/cognito-auth';
import {SqliteAuthSessionStore} from '../src/auth-session-store';
import {createApplication} from '../src/app-server';

type Claims={sub:string;exp:number;[key:string]:unknown};
const request=(session='',method='GET')=>({method,headers:{cookie:session}} as IncomingMessage);
function response(){
 const headers=new Map<string,any>();
 return {headers,statusCode:0,body:'',getHeader(name:string){return headers.get(name.toLowerCase());},setHeader(name:string,value:any){headers.set(name.toLowerCase(),value);},
  writeHead(status:number,values:Record<string,unknown>){this.statusCode=status;for(const [name,value] of Object.entries(values))headers.set(name.toLowerCase(),value);},end(body=''){this.body=body;}};
}
async function fixture(t:{after:(fn:()=>Promise<void>)=>void}){
 const directory=await mkdtemp(join(tmpdir(),'a4b-auth-persistence-'));
 const config:AuthConfig={mode:'cognito',origin:'https://app.example.com',userPoolId:'ap-northeast-2_TestPool',clientId:'testclient',domain:'https://test.auth.ap-northeast-2.amazoncognito.com',userStore:'postgres',sessionSeconds:28800};
 const user:AuthUser={id:randomUUID(),email:'member@example.com',name:'member',role:'member',status:'active'};
 let usersClosed=false;
 const users:AuthUsers={async provision(){assert.equal(usersClosed,false);return {...user};},async get(id){assert.equal(usersClosed,false);return id===user.id?{...user}:undefined;},async close(){usersClosed=true;}};
 let now=1700000000000,auth:CognitoAuth,refreshCalls=0,revocations=0,validRefresh:string|undefined,refreshWait:Promise<void>|undefined,callbackWait:Promise<void>|undefined;
 const tokens=new Map<string,Claims>(),codes=new Map<string,{nonce:string;challenge:string}>(),secrets:string[]=[];
 function access(){const token='access-secret-'+randomUUID();secrets.push(token);tokens.set(token,{sub:'member-subject',exp:Math.floor(now/1000)+3600});return token;}
 const mockFetch=(async(input:any,init:any)=>{
  const parameters=new URLSearchParams(init.body);
  if(String(input).endsWith('/oauth2/revoke')){revocations++;return new Response('{}');}
  const refresh_token='refresh-secret-'+randomUUID();secrets.push(refresh_token);
  if(parameters.get('grant_type')==='refresh_token'){
   refreshCalls++;
   if(parameters.get('refresh_token')!==validRefresh)return Response.json({error:'invalid_grant'},{status:400});
   await refreshWait;validRefresh=refresh_token;
   return Response.json({token_type:'Bearer',access_token:access(),refresh_token});
  }
  const pending=codes.get(parameters.get('code')!);assert.ok(pending);codes.delete(parameters.get('code')!);
  const verifier=parameters.get('code_verifier')!;secrets.push(verifier);
  assert.equal(createHash('sha256').update(verifier).digest('base64url'),pending.challenge);
  await callbackWait;validRefresh=refresh_token;
  const id_token='id-secret-'+randomUUID();secrets.push(id_token);
  tokens.set(id_token,{sub:'member-subject',exp:Math.floor(now/1000)+3600,nonce:pending.nonce,email:user.email,email_verified:true});
  return Response.json({token_type:'Bearer',access_token:access(),id_token,refresh_token});
 }) as typeof fetch;
 const verify=async(token:string)=>{const claims=tokens.get(token);assert.ok(claims);assert.ok(claims.exp*1000>now);return claims;};
 const peers:CognitoAuth[]=[];
 const makeAuth=()=>new CognitoAuth(config,users,{store:new SqliteAuthSessionStore(directory),now:()=>now,fetch:mockFetch,verifyAccess:verify,verifyId:verify});
 const open=()=>{auth=makeAuth();};
 open();t.after(async()=>{await auth.close();await Promise.all(peers.map(peer=>peer.close()));await rm(directory,{recursive:true,force:true});});
 async function call(path:string,session='',method='GET'){
  const res=response();assert.equal(await auth.handle(request(session,method),res as unknown as ServerResponse,new URL(path,config.origin)),true);return res;
 }
 async function begin(){
  const res=await call('/auth/login?returnTo='+encodeURIComponent('/?screen=schedules'));
  assert.equal(res.statusCode,303);const target=new URL(res.headers.get('location')),code=randomUUID();
  const nonce=target.searchParams.get('nonce')!;secrets.push(nonce);
  codes.set(code,{nonce,challenge:target.searchParams.get('code_challenge')!});
  return {path:'/auth/callback?'+new URLSearchParams({state:target.searchParams.get('state')!,code}),binding:res.headers.get('set-cookie')[0].split(';')[0]};
 }
 async function finish(login:Awaited<ReturnType<typeof begin>>,binding=login.binding){
  const res=await call(login.path,binding);
  const session=(res.headers.get('set-cookie') as string[]).find(value=>value.startsWith('__Host-a4b-session='));
  return {res,session:session?.split(';')[0]};
 }
 return {directory,config,user,users,secrets,call,begin,finish,
  async login(){const result=await finish(await begin());assert.ok(result.session);return result.session;},
  current:(session:string)=>auth.currentUser(request(session)),close:()=>auth.close(),async reopen(){await auth.close();usersClosed=false;open();},peer(){const peer=makeAuth();peers.push(peer);return peer;},
  advance:(ms:number)=>{now+=ms;},pauseRefresh:(wait:Promise<void>)=>{refreshWait=wait;},pauseCallback:(wait:Promise<void>)=>{callbackWait=wait;},get auth(){return auth;},get usersClosed(){return usersClosed;},get refreshCalls(){return refreshCalls;},get revocations(){return revocations;}};
}

test('encrypted Cognito login and opaque session survive close/recreate; logout stays revoked',async t=>{
 const f=await fixture(t),pending=await f.begin();await f.reopen();
 const {res,session}=await f.finish(pending);assert.ok(session);assert.equal(res.headers.get('location'),'/?screen=schedules');
 assert.match(session,/^__Host-a4b-session=[A-Za-z0-9_-]{43}$/);
 assert.match(res.headers.get('set-cookie').find((value:string)=>value.startsWith('__Host-a4b-session=')),/; Path=\/; HttpOnly; SameSite=Lax; Max-Age=28800; Secure$/);
 await f.close();const bytes=await readFile(join(f.directory,'auth-sessions.sqlite'));
 for(const value of [...f.secrets,session.split('=')[1],pending.binding.split('=')[1]])assert.equal(bytes.includes(Buffer.from(value)),false,'authentication secret must not appear in SQLite');
 if(process.platform!=='win32')for(const name of ['auth-session.key','auth-sessions.sqlite'])assert.equal((await stat(join(f.directory,name))).mode&0o777,0o600);
 await f.reopen();assert.equal((await f.current(session))?.id,f.user.id);
 const logout=await f.call('/api/auth/logout',session,'POST');assert.equal(logout.statusCode,200);assert.equal(f.revocations,1);
 await f.reopen();assert.equal(await f.current(session),undefined);
});

test('session refresh remains single-flight and its rotated tokens survive restart',async t=>{
 const f=await fixture(t),session=await f.login();f.advance(3600000);
 assert.deepEqual((await Promise.all([f.current(session),f.current(session)])).map(user=>user?.id),[f.user.id,f.user.id]);assert.equal(f.refreshCalls,1);
 await f.reopen();assert.equal((await f.current(session))?.id,f.user.id);assert.equal(f.refreshCalls,1);
 f.advance(3600000);assert.equal((await f.current(session))?.id,f.user.id);assert.equal(f.refreshCalls,2);
});

test('logout by another instance during token refresh cannot restore the session on disk',async t=>{
 const f=await fixture(t),session=await f.login();f.advance(3600000);
 const peer=f.peer();
 let release!:()=>void;f.pauseRefresh(new Promise<void>(resolve=>{release=resolve;}));
 const current=f.current(session);assert.equal(f.refreshCalls,1);
 const closing=f.close();
 const res=response();await peer.handle(request(session,'POST'),res as unknown as ServerResponse,new URL('/api/auth/logout',f.config.origin));
 release();assert.equal(await current,undefined);await closing;assert.equal(await peer.currentUser(request(session)),undefined);await peer.close();
 await f.reopen();assert.equal(await f.current(session),undefined);
});

test('shutdown drains a rotating refresh before closing persistent authentication',async t=>{
 const f=await fixture(t),session=await f.login();f.advance(3600000);
 let release!:()=>void;f.pauseRefresh(new Promise<void>(resolve=>{release=resolve;}));
 const current=f.current(session);assert.equal(f.refreshCalls,1);
 let stopped=false;const closing=f.close().then(()=>{stopped=true;});await Promise.resolve();assert.equal(stopped,false);
 await assert.rejects(f.current(session),error=>!!error&&typeof error==='object'&&'status' in error&&error.status===503);
 release();assert.equal((await current)?.id,f.user.id);await closing;
 await f.reopen();assert.equal((await f.current(session))?.id,f.user.id);assert.equal(f.refreshCalls,1);
 f.advance(3600000);assert.equal((await f.current(session))?.id,f.user.id);assert.equal(f.refreshCalls,2,'restart must retain the only valid rotated refresh token');
});

test('application shutdown drains an accepted login callback before closing member storage',async t=>{
 const f=await fixture(t),pending=await f.begin();
 let release!:()=>void;f.pauseCallback(new Promise<void>(resolve=>{release=resolve;}));
 const callback=f.finish(pending),app=createApplication({port:3030,config:f.config,auth:f.auth,users:f.users,restore:false});
 let stopped=false;const closing=app.close().then(()=>{stopped=true;});await Promise.resolve();
 assert.equal(stopped,false);assert.equal(f.usersClosed,false);
 await assert.rejects(f.begin(),error=>!!error&&typeof error==='object'&&'status' in error&&error.status===503);
 release();const {session}=await callback;assert.ok(session);await closing;assert.equal(f.usersClosed,true);
 await f.reopen();assert.equal((await f.current(session))?.id,f.user.id);
});

test('absolute expiry and disabled members remain blocked after restart',async t=>{
 const f=await fixture(t),session=await f.login();f.advance(f.config.sessionSeconds*1000);await f.reopen();
 assert.equal(await f.current(session),undefined);assert.equal(f.refreshCalls,0);
 const second=await f.login();f.user.status='disabled';await f.reopen();
 await assert.rejects(f.current(second),error=>!!error&&typeof error==='object'&&'status' in error&&error.status===403);
 f.user.status='active';await f.reopen();assert.equal(await f.current(second),undefined);
});

for(const tamper of ['ciphertext','expiry','revision','key'] as const)test(`stored ${tamper} modification fails closed`,async t=>{
 const f=await fixture(t),session=await f.login();await f.close();
 if(tamper==='key')await writeFile(join(f.directory,'auth-session.key'),randomBytes(32));
 else{
  const db=new DatabaseSync(join(f.directory,'auth-sessions.sqlite'));
  if(tamper==='ciphertext')db.prepare("UPDATE auth_state SET encrypted=substr(encrypted,1,length(encrypted)-1)||'!' WHERE kind='session'").run();
  if(tamper==='expiry')db.prepare("UPDATE auth_state SET ends_at=ends_at+86400000 WHERE kind='session'").run();
  if(tamper==='revision')db.prepare("UPDATE auth_state SET revision='replacement' WHERE kind='session'").run();db.close();
 }
 await f.reopen();assert.equal(await f.current(session),undefined);
});

test('missing persistent key refuses startup instead of replacing existing session protection',async t=>{
 const f=await fixture(t);await f.login();await f.close();await rm(join(f.directory,'auth-session.key'));
 assert.throws(()=>new SqliteAuthSessionStore(f.directory),/보호 키/);
});

test('persistent PKCE keeps cookie binding, expiry and one-time consumption',async t=>{
 const f=await fixture(t),pending=await f.begin();await f.reopen();
 assert.equal((await f.finish(pending,'__Host-a4b-login='+randomBytes(32).toString('base64url'))).session,undefined);
 assert.equal((await f.finish(pending)).session,undefined,'failed callback is consumed');
 const second=await f.begin();await f.reopen();assert.ok((await f.finish(second)).session);
 await f.reopen();assert.equal((await f.finish(second)).session,undefined,'successful callback cannot be replayed');
 const expired=await f.begin();f.advance(600000);await f.reopen();assert.equal((await f.finish(expired)).session,undefined);
});

test('application injects a persistent root session store only when constructing authentication',async t=>{
 const f=await fixture(t),previous=process.env.EASYFLOW_DATA_DIR,directory=join(f.directory,'application');process.env.EASYFLOW_DATA_DIR=directory;
 async function call(app:ReturnType<typeof createApplication>,path:string){
  const res=response();let end!:()=>void;const finished=new Promise<void>(resolve=>{end=resolve;});
  res.end=()=>{end();};
  app.server.emit('request',{...request(),url:path,headers:{host:new URL(f.config.origin).host}},res);await finished;return res;
 }
 const count=()=>{const db=new DatabaseSync(join(directory,'auth-sessions.sqlite'));try{return Number(db.prepare("SELECT count(*) AS total FROM auth_state WHERE kind='login'").get()!.total);}finally{db.close();}};
 try{
  const first=createApplication({port:3030,config:f.config,users:f.users,restore:false});
  const login=await call(first,'/auth/login');assert.equal(login.statusCode,303);const state=new URL(login.headers.get('location')).searchParams.get('state')!;
  await first.close();assert.equal(count(),1);
  const second=createApplication({port:3030,config:f.config,users:f.users,restore:false});
  // A missing binding rejects before HTTP token exchange, but still consumes the persisted attempt.
  const callback=await call(second,'/auth/callback?'+new URLSearchParams({state,code:'test'}));assert.equal(callback.headers.get('location'),'/?auth_error=login_failed');
  await second.close();assert.equal(count(),0);
 }finally{if(previous===undefined)delete process.env.EASYFLOW_DATA_DIR;else process.env.EASYFLOW_DATA_DIR=previous;}
});
