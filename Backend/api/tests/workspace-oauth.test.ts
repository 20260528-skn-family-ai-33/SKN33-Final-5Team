import test from 'node:test';
import assert from 'node:assert/strict';
import {request as httpRequest,type IncomingMessage} from 'node:http';
import {randomUUID} from 'node:crypto';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createApplication} from '../src/app-server';
import {authConfig} from '../src/auth-config';
import {CognitoAuth} from '../src/cognito-auth';
import type {AuthUser,AuthUsers} from '../src/auth-users';
import {GMAIL_OAUTH,GOOGLE_SHEETS_OAUTH} from '../src/oauth';
import {GMAIL_CONNECTION_ID,Store} from '../src/store';

test('HTTP workspace routing preserves public OAuth callbacks and isolates concurrent refreshes',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-workspace-oauth-')),previousDirectory=process.env.EASYFLOW_DATA_DIR,previousFetch=globalThis.fetch;
 const previousGmailId=process.env.A4B_GMAIL_CLIENT_ID,previousGmailSecret=process.env.A4B_GMAIL_CLIENT_SECRET;
 process.env.EASYFLOW_DATA_DIR=directory;
 process.env.A4B_GMAIL_CLIENT_ID='gmail-test-client';process.env.A4B_GMAIL_CLIENT_SECRET='gmail-test-secret';
 const config=authConfig({A4B_AUTH_MODE:'cognito',EASYFLOW_PUBLIC_URL:'https://app.example.com:8443',A4B_COGNITO_USER_POOL_ID:'ap-northeast-2_TestPool',A4B_COGNITO_CLIENT_ID:'testclient',A4B_COGNITO_DOMAIN:'https://test.auth.ap-northeast-2.amazoncognito.com',A4B_AUTH_USER_STORE:'postgres',A4B_DATABASE_URL:'postgresql://test@db.example.com/test'})!;
 const member=(name:string):AuthUser=>({id:randomUUID(),name,email:name+'@example.com',role:'member',status:'active'}),alice=member('alice'),bob=member('bob'),carol=member('carol');
 const members=new Map([['alice',alice],['bob',bob],['carol',carol]]);
 const users:AuthUsers={async provision(){throw new Error('not used');},async get(id){return [...members.values()].find(user=>user.id===id);},async close(){}};
 let refreshRequests=0,allRequests!:()=>void;
 // Authentication itself is covered by cognito-auth.test.ts with signed JWTs.
 class WorkspaceTestAuth extends CognitoAuth {
  async handle(){return false;}
  async currentUser(req:IncomingMessage){
   if(req.url==='/api/credentials/test'&&++refreshRequests===3)allRequests();
   return members.get(req.headers.cookie||'');
  }
 }
 const auth=new WorkspaceTestAuth(config,users);
 const app=createApplication({port:3030,config,auth,users,restore:false});
 await new Promise<void>((resolve,reject)=>{app.server.once('error',reject);app.server.listen(0,'127.0.0.1',resolve);});
 const address=app.server.address();assert.ok(address&&typeof address!=='string');
 const call=(path:string,member='alice',body?:unknown,headers:Record<string,string>={},method=body===undefined?'GET':'POST')=>new Promise<{status:number;body:string}>((resolve,reject)=>{
  const request=httpRequest({hostname:'127.0.0.1',port:address.port,path,method,headers:{Host:new URL(config.origin).host,Origin:config.origin,Cookie:member,'Content-Type':'application/json',...headers}},response=>{
   let text='';response.setEncoding('utf8');response.on('data',chunk=>{text+=chunk;});response.on('end',()=>resolve({status:response.statusCode!,body:text}));response.on('error',reject);
  });request.on('error',reject);request.end(body===undefined?undefined:JSON.stringify(body));
 });
 try{
  await t.test('trusted HTTPS origin reaches begin and code exchange; another member cannot consume its state',async()=>{
   let exchanges=0;
   globalThis.fetch=(async(input,init)=>{
    exchanges++;assert.equal(String(input),GOOGLE_SHEETS_OAUTH.tokenUrl);
    assert.equal(new URLSearchParams(String(init?.body)).get('redirect_uri'),config.origin+'/api/oauth/callback');
    return Response.json({access_token:'alice-connected',refresh_token:'alice-refresh',expires_in:3600});
   }) as typeof fetch;
   const input={name:'Google Sheets',service:'googleSheets',clientId:'google-client',clientSecret:'test-secret'};
   const begin=await call('/api/oauth/begin','alice',input,{'X-Forwarded-Host':'evil.example','X-Forwarded-Proto':'http'});
   assert.equal(begin.status,200,begin.body);const pending=JSON.parse(begin.body);
   assert.equal(pending.redirectUri,config.origin+'/api/oauth/callback');
   assert.equal(new URL(pending.authorizationUrl).searchParams.get('redirect_uri'),pending.redirectUri);
   assert.equal((await call('/api/oauth/begin','alice',input,{Host:'evil.example'})).status,403);
   const callback='/api/oauth/callback?'+new URLSearchParams({state:pending.stateId,code:'test-code'});
   assert.equal((await call(callback,'bob')).status,400);assert.equal(exchanges,0);
   assert.equal((await call(callback,'alice')).status,200);assert.equal(exchanges,1);
   assert.equal((await call(callback,'alice')).status,400);assert.equal(exchanges,1);
   const aliceCredentials=JSON.parse((await call('/api/credentials','alice')).body).credentials;
   assert.equal(aliceCredentials.length,1);assert.deepEqual(JSON.parse((await call('/api/credentials','bob')).body).credentials,[]);
  });
  await t.test('same-ID requests deduplicate within a member and refresh separately across members',async()=>{
   const id='shared-connection-id';let release!:()=>void,calls=0;
   const gate=new Promise<void>(resolve=>{release=resolve;}),received=new Promise<void>(resolve=>{allRequests=resolve;});
   globalThis.fetch=(async(_input,init)=>{
    calls++;const refresh=new URLSearchParams(String(init?.body)).get('refresh_token');await gate;
    return Response.json({access_token:refresh+'-access',refresh_token:refresh+'-rotated',expires_in:3600});
   }) as typeof fetch;
   for(const member of ['alice','bob']){
    const result=await call('/api/credentials',member,{id,name:member+' Google',provider:'bearer',service:'googleSheets',data:{authType:'oauth2',service:'googleSheets',...GOOGLE_SHEETS_OAUTH,clientId:'google-client',refreshToken:member,expiresAt:1}});
    assert.equal(result.status,200,result.body);
   }
   const requests=[call('/api/credentials/test','alice',{id}),call('/api/credentials/test','alice',{id}),call('/api/credentials/test','bob',{id})];
   try{await received;await new Promise<void>(resolve=>setImmediate(resolve));}finally{release();}
   const responses=await Promise.all(requests);for(const response of responses)assert.equal(response.status,200,response.body);
   assert.equal(calls,2);
   for(const member of [alice,bob]){
    const store=new Store(join(directory,'users',member.id));
    try{const credential=store.getCredential(id);assert.equal(credential.data.token,member.name+'-access');assert.equal(credential.data.refreshToken,member.name+'-rotated');}finally{store.close();}
   }
  });
  await t.test('Gmail requests read-only access and keeps the token in its member workspace and tool',async()=>{
   const path='/api/agent-builder/gmail';
   const asCarol=(route:string,data?:unknown,method?:string)=>call(route,'carol',data,{},method);
   const visits:string[]=[];
   globalThis.fetch=(async(input,init)=>{
    visits.push(String(input));
    if(String(input)===GMAIL_OAUTH.tokenUrl){
     const request=new URLSearchParams(String(init?.body));
     assert.equal(request.get('client_id'),'gmail-test-client');
     assert.equal(request.get('client_secret'),'gmail-test-secret');
     if(request.get('grant_type')==='refresh_token'){
      assert.equal(request.get('refresh_token'),'private-gmail-refresh');
      return Response.json({access_token:'private-gmail-access',expires_in:3600,scope:GMAIL_OAUTH.scope});
     }
     return Response.json({access_token:'expired-gmail-access',refresh_token:'private-gmail-refresh',expires_in:1,scope:GMAIL_OAUTH.scope});
    }
    assert.equal(String(input),'https://gmail.googleapis.com/gmail/v1/users/me/profile');
    assert.equal(new Headers(init?.headers).get('Authorization'),'Bearer private-gmail-access');
    return Response.json({emailAddress:'carol@example.com'});
   }) as typeof fetch;
   assert.deepEqual(JSON.parse((await asCarol(path)).body),{configured:true,connected:false,connectionVersion:0,email:null});
   const begin=await asCarol(path,{});
   assert.equal(begin.status,200,begin.body);
   assert.doesNotMatch(begin.body,/gmail-test-secret/);
   const pending=JSON.parse(begin.body),authorization=new URL(pending.authorizationUrl);
   assert.equal(authorization.searchParams.get('scope'),GMAIL_OAUTH.scope);
   assert.equal(authorization.searchParams.get('access_type'),'offline');
   const callback='/api/oauth/callback?'+new URLSearchParams({state:pending.stateId,code:'gmail-code'});
   assert.equal((await call(callback,'bob')).status,400);
   assert.equal(visits.length,0);
   assert.equal((await asCarol(callback)).status,200);
   const automatic=await asCarol(path);
   assert.deepEqual(JSON.parse(automatic.body),{configured:true,connected:true,connectionVersion:pending.connectionVersion+1,email:'carol@example.com'});
   assert.doesNotMatch(automatic.body,/private-gmail|gmail-test-secret|accessToken|refreshToken/);
   assert.equal(visits.length,3);
   assert.equal(JSON.parse((await asCarol(path)).body).email,'carol@example.com');
   assert.equal(visits.length,3,'status reuses the connected account identity');
   assert.equal(JSON.parse((await call(path,'bob')).body).connected,false);
   assert.equal((await call(path+'/test','bob',{})).status,400);
   assert.deepEqual(JSON.parse((await asCarol(path+'/test',{})).body),{email:'carol@example.com'});
   assert.ok(!JSON.parse((await asCarol('/api/credentials')).body).credentials.some((c:{id:string})=>c.id===GMAIL_CONNECTION_ID));
   assert.equal((await asCarol('/api/credentials/test',{id:GMAIL_CONNECTION_ID})).status,400);
   const reconnect=JSON.parse((await asCarol(path,{})).body);
   assert.equal((await asCarol(path,undefined,'DELETE')).status,200);
   assert.equal((await asCarol('/api/oauth/callback?'+new URLSearchParams({state:reconnect.stateId,code:'old-code'}))).status,400);
   assert.equal(JSON.parse((await asCarol(path)).body).connected,false);
   assert.equal(visits.length,4);
  });
  await t.test('Gmail account status retries failures, deduplicates profile reads, and drops disconnected identities',async()=>{
   const path='/api/agent-builder/gmail';
   // OAuthManager captures fetch when its workspace runtime is first created.
   // Give this scenario a new member so it captures this test's token exchange mock.
   members.set('identity',member('identity'));
   const asMember=(route:string,data?:unknown,method?:string)=>call(route,'identity',data,{},method);
   const releases:(()=>void)[]=[];
   const requests:Promise<{status:number;body:string}>[]=[];
   let fail=true,profileCalls=0,release:()=>void=()=>{},started:()=>void=()=>{};
   let gate=Promise.resolve();
   function blockProfiles(){gate=new Promise<void>(resolve=>{release=resolve;releases.push(resolve);});}
   globalThis.fetch=(async(input,init)=>{
    if(String(input)===GMAIL_OAUTH.tokenUrl){
     const code=new URLSearchParams(String(init?.body)).get('code');
     return Response.json({access_token:'private-'+code,refresh_token:'private-refresh',expires_in:3600,scope:GMAIL_OAUTH.scope});
    }
    assert.equal(String(input),'https://gmail.googleapis.com/gmail/v1/users/me/profile');
    profileCalls++;
    const token=new Headers(init?.headers).get('Authorization');
    if(fail)throw new Error('private-provider-diagnostic');
    started();await gate;
    return Response.json({emailAddress:token==='Bearer private-new-account'?'new@example.com':'old@example.com'});
   }) as typeof fetch;
   async function connect(code:string){
    const pending=JSON.parse((await asMember(path,{})).body);
    const result=await asMember('/api/oauth/callback?'+new URLSearchParams({state:pending.stateId,code}));
    assert.equal(result.status,200,result.body);
   }
   try{
   await connect('old-account');
   const unavailable=await asMember(path),status=JSON.parse(unavailable.body);
   assert.equal(unavailable.status,200);
   assert.equal(status.connected,true);assert.equal(status.email,null);
   assert.match(status.accountError,/새로고침/);
   assert.doesNotMatch(unavailable.body,/private-|old@example/);
   fail=false;
   blockProfiles();
   const requested=new Promise<void>(resolve=>{started=resolve;});
   const reads=[asMember(path),asMember(path)];
   requests.push(...reads);
   await requested;release();
   for(const result of await Promise.all(reads))assert.equal(JSON.parse(result.body).email,'old@example.com');
   assert.equal(profileCalls,2,'the retry succeeds once for concurrent status requests');
   assert.equal(JSON.parse((await asCarolStatus()).body).email,null,'another member has no identity');
   function asCarolStatus(){return call(path,'carol');}

   blockProfiles();
   const oldStarted=new Promise<void>(resolve=>{started=resolve;});
   const oldRead=asMember(path+'/test',{});
   requests.push(oldRead);
   await oldStarted;
   await asMember(path,undefined,'DELETE');
   const disconnected=JSON.parse((await asMember(path)).body);
   assert.equal(disconnected.connected,false);assert.equal(disconnected.email,null);
   await connect('new-account');
   // New requests can finish while the previous account's profile remains in flight.
   const releaseOld=release;gate=Promise.resolve();started=()=>{};
   assert.equal(JSON.parse((await asMember(path)).body).email,'new@example.com');
   releaseOld();
   const oldResult=await oldRead;
   assert.equal(oldResult.status,400);
   assert.doesNotMatch(oldResult.body,/old@example|private-/);
   const count=profileCalls;
   assert.equal(JSON.parse((await asMember(path)).body).email,'new@example.com');
   assert.equal(profileCalls,count,'stale profile completion does not replace the new cache');
   }finally{
    for(const unblock of releases)unblock();
    await Promise.allSettled(requests);
   }
  });
 }finally{
  globalThis.fetch=previousFetch;await app.close();await new Promise<void>(resolve=>app.server.close(()=>resolve()));
  if(previousDirectory===undefined)delete process.env.EASYFLOW_DATA_DIR;else process.env.EASYFLOW_DATA_DIR=previousDirectory;
  if(previousGmailId===undefined)delete process.env.A4B_GMAIL_CLIENT_ID;else process.env.A4B_GMAIL_CLIENT_ID=previousGmailId;
  if(previousGmailSecret===undefined)delete process.env.A4B_GMAIL_CLIENT_SECRET;else process.env.A4B_GMAIL_CLIENT_SECRET=previousGmailSecret;
  await rm(directory,{recursive:true,force:true});
 }
});
