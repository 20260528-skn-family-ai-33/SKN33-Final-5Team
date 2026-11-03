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
import {GOOGLE_SHEETS_OAUTH} from '../src/oauth';
import {Store} from '../src/store';

test('HTTP workspace routing preserves public OAuth callbacks and isolates concurrent refreshes',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-workspace-oauth-')),previousDirectory=process.env.EASYFLOW_DATA_DIR,previousFetch=globalThis.fetch;
 process.env.EASYFLOW_DATA_DIR=directory;
 const config=authConfig({A4B_AUTH_MODE:'cognito',EASYFLOW_PUBLIC_URL:'https://app.example.com:8443',A4B_COGNITO_USER_POOL_ID:'ap-northeast-2_TestPool',A4B_COGNITO_CLIENT_ID:'testclient',A4B_COGNITO_DOMAIN:'https://test.auth.ap-northeast-2.amazoncognito.com',A4B_AUTH_USER_STORE:'postgres',A4B_DATABASE_URL:'postgresql://test@db.example.com/test'})!;
 const member=(name:string):AuthUser=>({id:randomUUID(),name,email:name+'@example.com',role:'member',status:'active'}),alice=member('alice'),bob=member('bob');
 const members=new Map([['alice',alice],['bob',bob]]);
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
 const call=(path:string,member='alice',body?:unknown,headers:Record<string,string>={})=>new Promise<{status:number;body:string}>((resolve,reject)=>{
  const request=httpRequest({hostname:'127.0.0.1',port:address.port,path,method:body===undefined?'GET':'POST',headers:{Host:new URL(config.origin).host,Origin:config.origin,Cookie:member,'Content-Type':'application/json',...headers}},response=>{
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
 }finally{
  globalThis.fetch=previousFetch;await app.close();await new Promise<void>(resolve=>app.server.close(()=>resolve()));
  if(previousDirectory===undefined)delete process.env.EASYFLOW_DATA_DIR;else process.env.EASYFLOW_DATA_DIR=previousDirectory;
  await rm(directory,{recursive:true,force:true});
 }
});
