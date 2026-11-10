import test,{type TestContext} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID,createHash} from 'node:crypto';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {request,type IncomingMessage} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {createApplication} from '../src/app-server';
import {CognitoAuth} from '../src/cognito-auth';
import type {AuthConfig} from '../src/auth-config';
import type {AuthUser,AuthUsers} from '../src/auth-users';
import {GMAIL_OAUTH} from '../src/oauth';

type Gate={started:Promise<void>;release:()=>void;wait:Promise<void>;enter:()=>void};
function gate():Gate{
 let release!:()=>void,enter!:()=>void;
 return {wait:new Promise(resolve=>{release=resolve;}),started:new Promise(resolve=>{enter=resolve;}),release:()=>release(),enter:()=>enter()};
}
async function fixture(t:TestContext){
 const directory=await mkdtemp(join(tmpdir(),'a4b-gmail-shutdown-'));
 const env={EASYFLOW_DATA_DIR:directory,A4B_FOCUSED_DEMO:'1',A4B_GMAIL_CLIENT_ID:'fixture-client',A4B_GMAIL_CLIENT_SECRET:'fixture-secret'};
 const previous=new Map(Object.keys(env).map(key=>[key,process.env[key]]));
 for(const [key,value] of Object.entries(env))process.env[key]=value;
 let now=1700000000000,access='',refresh='',refreshCalls=0,codeCalls=0,rejectExchanges=false,app:ReturnType<typeof createApplication>,port=0;
 const holds=new Map<string,Gate>(),releases:Gate[]=[];
 t.mock.method(Date,'now',()=>now);
 const config:AuthConfig={mode:'cognito',origin:'https://app.example.com',userPoolId:'ap-northeast-2_TestPool',clientId:'testclient',domain:'https://test.auth.ap-northeast-2.amazoncognito.com',userStore:'postgres',sessionSeconds:28800};
 const user:AuthUser={id:randomUUID(),name:'fixture',email:'fixture@example.com',role:'member',status:'active'};
 const users:AuthUsers={async provision(){return user;},async get(id){return id===user.id?user:undefined;},async close(){}};
 class StubAuth extends CognitoAuth{async handle(){return false;}async currentUser(req:IncomingMessage){return req.headers.cookie==='fixture'?user:undefined;}}
 t.mock.method(globalThis,'fetch',async(input:any,init:any)=>{
  if(String(input)===GMAIL_OAUTH.tokenUrl){
   const params=new URLSearchParams(String(init?.body)),grant=params.get('grant_type')!;
   if(rejectExchanges)return Response.json({error:'invalid_grant'},{status:400});
   if(grant==='refresh_token'){
    refreshCalls++;if(params.get('refresh_token')!==refresh)return Response.json({error:'invalid_grant'},{status:400});
   }else codeCalls++;
   const pending=holds.get(grant);if(pending){pending.enter();await pending.wait;}
   access='fixture-access-'+randomUUID();refresh='fixture-refresh-'+randomUUID();
   return Response.json({access_token:access,refresh_token:refresh,expires_in:3600,scope:GMAIL_OAUTH.scope});
  }
  assert.equal(String(input),'https://gmail.googleapis.com/gmail/v1/users/me/profile');
  assert.equal(new Headers(init?.headers).get('Authorization'),'Bearer '+access);
  const pending=holds.get('profile');if(pending){pending.enter();await pending.wait;}
  return Response.json({emailAddress:user.email});
 });
 async function open(){
  app=createApplication({port:3030,config,auth:new StubAuth(config,users),users,restore:false});
  await new Promise<void>((resolve,reject)=>{app.server.once('error',reject);app.server.listen(0,'127.0.0.1',resolve);});
  const address=app.server.address();assert.ok(address&&typeof address!=='string');port=address.port;
 }
 async function close(){await app.close();await new Promise<void>(resolve=>app.server.close(()=>resolve()));}
 t.after(async()=>{
  for(const pending of releases)pending.release();
  await close();for(const [key,value] of previous)if(value===undefined)delete process.env[key];else process.env[key]=value;
  await rm(directory,{recursive:true,force:true});
 });
 await open();
 function call(path:string,body?:unknown,method=body===undefined?'GET':'POST'){
  return new Promise<{status:number;json:any;body:string}>((resolve,reject)=>{
   const req=request({hostname:'127.0.0.1',port,path,method,headers:{Host:'app.example.com',Origin:config.origin,Cookie:'fixture','Content-Type':'application/json'}},res=>{
    let body='';res.setEncoding('utf8');res.on('data',part=>{body+=part;});res.on('end',()=>{let json;try{json=JSON.parse(body);}catch{}resolve({status:res.statusCode!,json,body});});
   });req.on('error',reject);req.end(body===undefined?undefined:JSON.stringify(body));
  });
 }
 async function begin(){const result=await call('/api/agent-builder/gmail',{});assert.equal(result.status,200);return '/api/oauth/callback?'+new URLSearchParams({state:result.json.stateId,code:'fixture-code'});}
 const workspace=join(directory,'users',user.id);
 return {call,begin,workspace,
  async connect(){const response=await call(await begin());assert.equal(response.status,200);},
  hold(kind:string){const pending=gate();holds.set(kind,pending);releases.push(pending);return pending;},
  shutdown:()=>app.close(),async restart(){await close();await open();},advance:(ms:number)=>{now+=ms;},
  rejectExchanges:()=>{rejectExchanges=true;},
  events(){const db=new DatabaseSync(join(workspace,'easyflow.sqlite'));try{return db.prepare('SELECT event FROM gmail_connection_events ORDER BY seq').all().map(row=>String(row.event));}finally{db.close();}},
  count(){const db=new DatabaseSync(join(workspace,'easyflow.sqlite'));try{return Number(db.prepare("SELECT count(*) AS n FROM credentials WHERE id='a4b-gmail'").get()!.n);}finally{db.close();}},
  async keyFingerprint(){return createHash('sha256').update(await readFile(join(workspace,'credential.key'))).digest('hex');},
  get refreshCalls(){return refreshCalls;},get codeCalls(){return codeCalls;}};
}

test('shutdown completes an accepted Gmail token exchange and retains it after restart',async t=>{
 const f=await fixture(t),path=await f.begin(),pending=f.hold('authorization_code'),callback=f.call(path);
 await pending.started;let closed=false;const closing=f.shutdown().then(()=>{closed=true;});await new Promise(resolve=>setImmediate(resolve));assert.equal(closed,false);
 pending.release();assert.equal((await callback).status,200);await closing;assert.equal(f.count(),1);const key=await f.keyFingerprint();
 await f.restart();const status=await f.call('/api/agent-builder/gmail');assert.equal(status.json.connected,true);assert.equal(status.json.email,'fixture@example.com');assert.equal(f.codeCalls,1);assert.equal(await f.keyFingerprint(),key);
});

test('shutdown drains a strict Gmail refresh rotation and its HTTP profile response',async t=>{
 const f=await fixture(t);await f.connect();f.advance(7200000);
 const pending=f.hold('refresh_token'),request=f.call('/api/agent-builder/gmail');await pending.started;
 let closed=false;const closing=f.shutdown().then(()=>{closed=true;});await new Promise(resolve=>setImmediate(resolve));assert.equal(closed,false);
 pending.release();const response=await request;assert.equal(response.status,200);assert.equal(response.json.connected,true);assert.equal(response.json.email,'fixture@example.com');await closing;
 await f.restart();assert.equal((await f.call('/api/agent-builder/gmail')).json.email,'fixture@example.com');assert.equal(f.refreshCalls,1);
 f.advance(7200000);assert.equal((await f.call('/api/agent-builder/gmail/test',{})).status,200);assert.equal(f.refreshCalls,2,'the only valid rotated refresh token was persisted');
});

test('shutdown waits for a Gmail profile read and its final database check',async t=>{
 const f=await fixture(t);await f.connect();const pending=f.hold('profile'),request=f.call('/api/agent-builder/gmail');await pending.started;
 let closed=false;const closing=f.shutdown().then(()=>{closed=true;});await new Promise(resolve=>setImmediate(resolve));assert.equal(closed,false);
 pending.release();const response=await request;assert.equal(response.status,200);assert.equal(response.json.connected,true);assert.equal(response.json.email,'fixture@example.com');await closing;
 await f.restart();assert.equal((await f.call('/api/agent-builder/gmail')).json.connected,true);
});

test('disconnect during Gmail refresh stays deleted through shutdown and restart',async t=>{
 const f=await fixture(t);await f.connect();f.advance(7200000);
 const pending=f.hold('refresh_token'),request=f.call('/api/agent-builder/gmail/test',{});await pending.started;
 assert.equal((await f.call('/api/agent-builder/gmail',undefined,'DELETE')).status,200);
 const closing=f.shutdown();pending.release();assert.equal((await request).status,400);await closing;assert.equal(f.count(),0);
 await f.restart();assert.equal((await f.call('/api/agent-builder/gmail')).json.connected,false);assert.equal(f.count(),0);
});

test('OAuth failure evidence distinguishes Gmail, another service and an unknown state',async t=>{
 const f=await fixture(t);f.rejectExchanges();
 const sheets=await f.call('/api/oauth/begin',{name:'Sheets',service:'googleSheets',clientId:'fixture-client',clientSecret:'fixture-secret'});assert.equal(sheets.status,200);
 assert.equal((await f.call('/api/oauth/callback?'+new URLSearchParams({state:sheets.json.stateId,code:'fixture-code'}))).status,400);
 assert.equal(f.events().includes('oauth_callback_failed'),false);assert.equal(f.events().includes('oauth_callback_unattributed'),false);
 const generic=await f.call('/api/oauth/begin',{name:'External API',clientId:'fixture-client',authorizationUrl:'https://login.example.com/authorize',tokenUrl:'https://login.example.com/token'});assert.equal(generic.status,200);
 assert.equal((await f.call('/api/oauth/callback?'+new URLSearchParams({state:generic.json.stateId,error:'access_denied'}))).status,400);
 assert.equal(f.events().includes('oauth_callback_failed'),false);assert.equal(f.events().includes('oauth_callback_unattributed'),false,'a known generic OAuth request is distinct from a missing state');
 const gmail=await f.begin();assert.equal((await f.call(gmail)).status,400);assert.equal(f.events().filter(event=>event==='oauth_callback_failed').length,1);
 assert.equal((await f.call('/api/oauth/callback?'+new URLSearchParams({state:'A'.repeat(43),code:'fixture-code'}))).status,400);
 assert.equal(f.events().filter(event=>event==='oauth_callback_failed').length,1);assert.equal(f.events().filter(event=>event==='oauth_callback_unattributed').length,1);
});
