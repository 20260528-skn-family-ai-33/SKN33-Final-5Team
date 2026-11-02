import test from 'node:test';
import assert from 'node:assert/strict';
import {generateKeyPairSync,sign,createHash,randomUUID} from 'node:crypto';
import {mkdtemp,rm,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {request as httpRequest} from 'node:http';
import {CognitoJwtVerifier} from 'aws-jwt-verify';
import {authConfig,type AuthConfig} from '../src/auth-config';
import {CognitoAuth,safeReturnTo} from '../src/cognito-auth';
import {SqliteAuthUsers,postgresTls} from '../src/auth-users';
import type {PeerCertificate} from 'node:tls';
import {createApplication} from '../src/app-server';
import {baseDataDirectory,dataDirectory,withDataDirectory} from '../src/paths';
import {makeFlow} from '../../../shared/workflow';

const pool='ap-northeast-2_TestPool',client='testclient',issuer='https://cognito-idp.ap-northeast-2.amazonaws.com/'+pool;
const keys=generateKeyPairSync('rsa',{modulusLength:2048}),jwk={...keys.publicKey.export({format:'jwk'}),kty:'RSA',kid:'test-key',alg:'RS256',use:'sig'};
function jwt(payload:Record<string,unknown>){const header=Buffer.from(JSON.stringify({kid:jwk.kid,alg:'RS256'})).toString('base64url'),body=Buffer.from(JSON.stringify(payload)).toString('base64url'),input=header+'.'+body;return input+'.'+sign('RSA-SHA256',Buffer.from(input),keys.privateKey).toString('base64url');}
const basicEnv={A4B_AUTH_MODE:'cognito',EASYFLOW_PUBLIC_URL:'http://localhost:3030',A4B_COGNITO_USER_POOL_ID:pool,A4B_COGNITO_CLIENT_ID:client,A4B_COGNITO_DOMAIN:'https://a4b.auth.ap-northeast-2.amazoncognito.com',A4B_AUTH_USER_STORE:'sqlite'};
test('Cognito config rejects incomplete, insecure and non-local SQLite configurations',()=>{
 assert.equal(authConfig({}),undefined);
 assert.throws(()=>authConfig({A4B_AUTH_MODE:'cognito'}),/EASYFLOW_PUBLIC_URL/);
 for(const extra of [{EASYFLOW_PUBLIC_URL:'http://service.example.com'},{EASYFLOW_PUBLIC_URL:'https://service.example.com'},{A4B_COGNITO_DOMAIN:'http://localhost'},{A4B_AUTH_SESSION_SECONDS:'0'},{A4B_AUTH_MODE:'unknown'},{A4B_COGNITO_CLIENT_ID:'bad?client'},{EASYFLOW_PUBLIC_URL:'https://example.com/path'}])assert.throws(()=>authConfig({...basicEnv,...extra}));
 assert.throws(()=>authConfig({...basicEnv,A4B_AUTH_USER_STORE:'postgres',A4B_DATABASE_URL:'postgres://user:secret@db/a4b?sslmode=no-verify'}),/SSL/);
 assert.throws(()=>authConfig({EASYFLOW_PUBLIC_URL:'https://service.example.com'}),/cognito/);
 assert.equal(authConfig(basicEnv)?.userStore,'sqlite');
 assert.equal(authConfig({...basicEnv,A4B_DATABASE_TLS_SERVER_NAME:'a4b-db.example.rds.amazonaws.com'})?.databaseServerName,'a4b-db.example.rds.amazonaws.com');
 assert.throws(()=>authConfig({...basicEnv,A4B_DATABASE_TLS_SERVER_NAME:'127.0.0.1'}),/실제 DB 호스트/);
 assert.equal(authConfig({...basicEnv,A4B_AUTH_USER_STORE:'postgres',A4B_DATABASE_URL:'postgres://user:secret@db/a4b',EASYFLOW_PUBLIC_URL:'https://service.example.com'})?.userStore,'postgres');
});
test('return URLs cannot redirect to another site or to auth endpoints',()=>{
 for(const path of ['//evil.example','https://evil.example','/\\evil.example','/auth/logout','/auth/callback'])assert.equal(safeReturnTo(path,'https://app.example'),'/?screen=workflows');
 assert.equal(safeReturnTo('/?screen=run&auth_error=login_failed','https://app.example'),'/?screen=run');
});
test('RDS tunnel TLS checks the real database hostname and rejects unrelated certificates',()=>{
 const tls=postgresTls({databaseServerName:'a4b-db.example.rds.amazonaws.com'});
 assert.equal(tls.rejectUnauthorized,true);
 const matching={subjectaltname:'DNS:a4b-db.example.rds.amazonaws.com',subject:{CN:'a4b-db.example.rds.amazonaws.com'}} as PeerCertificate;
 assert.equal(tls.checkServerIdentity!('host.docker.internal',matching),undefined);
 const wrong={subjectaltname:'DNS:other.example.com',subject:{CN:'other.example.com'}} as PeerCertificate;
 assert.ok(tls.checkServerIdentity!('host.docker.internal',wrong) instanceof Error);
 assert.equal(postgresTls({}).checkServerIdentity,undefined);
});
test('workspace directory context stays isolated across concurrent async requests',async()=>{
 const first=join(tmpdir(),'alice-'+randomUUID()),second=join(tmpdir(),'bob-'+randomUUID());
 const values=await Promise.all([first,second].map((dir,index)=>withDataDirectory(dir,async()=>{await new Promise(resolve=>setTimeout(resolve,index?1:10));return dataDirectory('reports','x.csv');})));
 assert.deepEqual(values,[join(first,'reports','x.csv'),join(second,'reports','x.csv')]);assert.equal(dataDirectory(),baseDataDirectory());
});

test('signed Cognito login protects APIs, separates members, refreshes and revokes sessions',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-cognito-')),previous=process.env.EASYFLOW_DATA_DIR;process.env.EASYFLOW_DATA_DIR=directory;
 let now=Date.now(),refreshCalls=0,revocations=0;
 const codes=new Map<string,{subject:string;nonce:string;verifierChallenge:string;override?:Record<string,unknown>}>();
 const accessVerifier=CognitoJwtVerifier.create({userPoolId:pool,clientId:client,tokenUse:'access'}),idVerifier=CognitoJwtVerifier.create({userPoolId:pool,clientId:client,tokenUse:'id'});
 accessVerifier.cacheJwks({keys:[jwk]});idVerifier.cacheJwks({keys:[jwk]});
 const config={...authConfig(basicEnv)!,origin:'http://127.0.0.1:3030'} as AuthConfig,users=new SqliteAuthUsers(directory);
 const mockFetch=(async(input:any,init:any)=>{
  const parameters=new URLSearchParams(init.body);
  if(String(input).endsWith('/oauth2/revoke')){revocations++;return new Response('{}');}
  if(parameters.get('grant_type')==='refresh_token'){
   refreshCalls++;await new Promise(resolve=>setTimeout(resolve,10));const sub=parameters.get('refresh_token')!.slice('refresh:'.length);
   return Response.json({token_type:'Bearer',access_token:jwt({iss:issuer,sub,exp:Math.floor(now/1000)+3600,token_use:'access',client_id:client})});
  }
  const entry=codes.get(parameters.get('code')||'');assert.ok(entry);codes.delete(parameters.get('code')!);
  assert.equal(createHash('sha256').update(parameters.get('code_verifier')!).digest('base64url'),entry.verifierChallenge);
  assert.equal(parameters.get('redirect_uri'),config.origin+'/auth/callback');
  const claims={iss:issuer,sub:entry.subject,exp:Math.floor(now/1000)+3600};
  return Response.json({token_type:'Bearer',access_token:jwt({...claims,token_use:'access',client_id:client,...entry.override}),id_token:jwt({...claims,token_use:'id',aud:client,nonce:entry.nonce,email:entry.subject+'@example.com',email_verified:true,name:entry.subject,...entry.override}),refresh_token:'refresh:'+entry.subject});
 }) as typeof fetch;
 const auth=new CognitoAuth(config,users,{fetch:mockFetch,now:()=>now,verifyAccess:token=>accessVerifier.verify(token),verifyId:token=>idVerifier.verify(token)});
 const app=createApplication({port:3030,config,auth,users,restore:false});
 await new Promise<void>((resolve,reject)=>{app.server.once('error',reject);app.server.listen(0,'127.0.0.1',resolve);});const address=app.server.address();assert.ok(address&&typeof address!=='string');config.origin=`http://127.0.0.1:${address.port}`;
 const call=(path:string,session='',method='GET',body?:unknown,extra:Record<string,string>={})=>fetch(config.origin+path,{method,redirect:'manual',headers:{...(session?{Cookie:session}:{}),...(body===undefined?{}:{'Content-Type':'application/json'}),...(method==='GET'?{}:{Origin:config.origin}),...extra},...(body===undefined?{}:{body:JSON.stringify(body)})});
 async function begin(subject:string,override?:Record<string,unknown>){
  const response=await call('/auth/login?returnTo='+encodeURIComponent('/?screen=workflows'));assert.equal(response.status,303);
  const target=new URL(response.headers.get('location')!);assert.equal(target.origin,config.domain);assert.equal(target.searchParams.get('code_challenge_method'),'S256');
  assert.equal(target.searchParams.get('response_type'),'code');const code=randomUUID();
  codes.set(code,{subject,nonce:target.searchParams.get('nonce')!,verifierChallenge:target.searchParams.get('code_challenge')!,override});
  return {state:target.searchParams.get('state')!,code,cookie:response.headers.getSetCookie()[0].split(';')[0]};
 }
 async function login(subject:string,override?:Record<string,unknown>){const start=await begin(subject,override),callback=await call('/auth/callback?'+new URLSearchParams({state:start.state,code:start.code}),start.cookie);return {start,callback,session:callback.headers.getSetCookie().find(value=>value.startsWith('a4b-session='))?.split(';')[0]||''};}
 try{
  await t.test('anonymous requests cannot read, modify or download private resources',async()=>{
   for(const path of ['/api/workflows','/api/credentials','/api/executions','/api/agent-builder/agents','/api/agent-builder/original?id='+randomUUID(),'/api/files/download?executionId='+randomUUID()])assert.equal((await call(path)).status,401,path);
   assert.equal((await call('/api/workflows','','POST',makeFlow())).status,401);
   assert.equal((await call('/api/health')).status,200);assert.deepEqual(await (await call('/api/auth/me')).json(),{enabled:true,authenticated:false,user:null});
  });
  await t.test('callback state is bound to the browser, expires and cannot be replayed',async()=>{
   const start=await begin('state-test');const stolen=await call('/auth/callback?'+new URLSearchParams({state:start.state,code:start.code}));assert.equal(stolen.headers.get('location'),'/?auth_error=login_failed');
   const replay=await call('/auth/callback?'+new URLSearchParams({state:start.state,code:start.code}),start.cookie);assert.equal(replay.headers.get('location'),'/?auth_error=login_failed');
   const expired=await begin('expired');now+=600001;assert.equal((await call('/auth/callback?'+new URLSearchParams({state:expired.state,code:expired.code}),expired.cookie)).headers.get('location'),'/?auth_error=login_failed');now=Date.now();
  });
  await t.test('invalid issuer, client, nonce, token use, expiration and unverified email never create a session',async()=>{
   for(const override of [{iss:'https://evil.example'},{client_id:'anotherapp'},{aud:'anotherapp'},{nonce:'wrong'},{token_use:'id'},{exp:1},{email_verified:false}]){
    const result=await login('invalid',override);assert.equal(result.session,'');assert.equal(result.callback.headers.get('location'),'/?auth_error=login_failed');
   }
  });
  const alice=await login('alice',{role:'admin'}),bob=await login('bob');assert.ok(alice.session);assert.ok(bob.session);assert.equal(alice.callback.headers.get('location'),'/?screen=workflows');
  const aliceCookie=alice.callback.headers.getSetCookie().find(value=>value.startsWith('a4b-session='))!;assert.match(aliceCookie,/HttpOnly; SameSite=Lax/);assert.ok(!aliceCookie.includes('refresh:'));
  const aliceMe=await (await call('/api/auth/me',alice.session)).json() as any,bobMe=await (await call('/api/auth/me',bob.session)).json() as any;
  assert.notEqual(aliceMe.user.id,bobMe.user.id);assert.equal(aliceMe.user.role,'member');
  await t.test('an email collision cannot attach a different Cognito identity to an existing member',async()=>{
   const attacker=await login('different-subject',{email:'alice@example.com'});assert.equal(attacker.session,'');
   assert.equal((await (await call('/api/auth/me',alice.session)).json() as any).user.id,aliceMe.user.id);
  });
  let flow=makeFlow();flow={...flow,name:'Alice private workflow'};
  await t.test('workflows, executions and connection credentials are isolated between two signed-in members',async()=>{
   assert.equal((await call('/api/workflows',alice.session,'POST',flow)).status,200);
   const aliceFlows=await (await call('/api/workflows',alice.session)).json() as any,bobFlows=await (await call('/api/workflows',bob.session)).json() as any;
   assert.ok(aliceFlows.workflows.some((f:any)=>f.id===flow.id));assert.ok(!bobFlows.workflows.some((f:any)=>f.id===flow.id));
   const credential=await (await call('/api/credentials',alice.session,'POST',{name:'Alice private connection',provider:'bearer',data:{token:'alice-private-token'}})).json() as any;
   assert.ok(credential.credential.id);assert.deepEqual(await (await call('/api/credentials',bob.session)).json(),{credentials:[]});
   await call('/api/workflows?id='+flow.id,bob.session,'DELETE');assert.ok((await (await call('/api/workflows',alice.session)).json() as any).workflows.some((f:any)=>f.id===flow.id));
   assert.equal((await call('/api/runs',bob.session,'POST',{workflowId:flow.id,mode:'learning',input:[{}]})).status,400);
   const start=await (await call('/api/runs',alice.session,'POST',{workflowId:flow.id,mode:'learning',input:[{}]})).json() as any;assert.ok(start.executionId);
   assert.equal((await call('/api/executions?id='+start.executionId,bob.session)).status,404);
   assert.equal((await call('/api/files/download?executionId='+start.executionId,bob.session)).status,404);
   assert.ok((await readFile(join(directory,'users',aliceMe.user.id,'easyflow.sqlite'))).length>0);
  });
  await t.test('CSRF, foreign hosts and caller-supplied user IDs cannot change the selected workspace',async()=>{
   assert.equal((await call('/api/workflows',alice.session,'POST',flow,{Origin:'https://evil.example'})).status,403);
   const missingOrigin=await fetch(config.origin+'/api/workflows',{method:'POST',headers:{Cookie:alice.session,'Content-Type':'application/json'},body:JSON.stringify(flow)});assert.equal(missingOrigin.status,403);
   const foreignHost=await new Promise<number|undefined>((resolve,reject)=>{const request=httpRequest(config.origin+'/api/workflows',{headers:{Host:'evil.example',Cookie:alice.session}},response=>{response.resume();resolve(response.statusCode);});request.on('error',reject);request.end();});
   assert.equal(foreignHost,403);
   const rows=await (await call('/api/workflows?userId='+aliceMe.user.id,bob.session)).json() as any;assert.ok(!rows.workflows.some((f:any)=>f.id===flow.id));
  });
  await t.test('private webhook URLs select the owner but still require their own webhook authentication',async()=>{
   const hook={...makeFlow(),id:randomUUID(),nodes:[{id:'hook',name:'개인 Webhook',type:'n8n-nodes-base.webhook',typeVersion:2,position:[0,0],parameters:{path:'private-hook',httpMethod:'POST',responseMode:'onReceived',authentication:'easyflowToken'}}],connections:{}};
   assert.equal((await call('/api/workflows',alice.session,'POST',hook)).status,200);
   assert.equal((await call('/api/activation',alice.session,'POST',{workflowId:hook.id,active:true})).status,200);
   const info=await (await call('/api/webhook?workflowId='+hook.id,alice.session)).json() as any;
   const target=new URL(info.url);assert.equal(target.pathname,`/hooks/${aliceMe.user.id}/${hook.id}/private-hook`);assert.ok(target.searchParams.get('token'));
   assert.equal((await call(target.pathname,'','POST',{})).status,401);
   const response=await call(target.pathname+target.search,'','POST',{private:'alice-only'});assert.equal(response.status,200);const result=await response.json() as any;assert.ok(result.executionId);
   assert.equal((await call('/api/executions?id='+result.executionId,bob.session)).status,404);
   assert.equal((await call(target.pathname.replace(aliceMe.user.id,bobMe.user.id)+target.search,'','POST',{})).status,404);
  });
  await t.test('concurrent API requests refresh a session only once',async()=>{
   now+=3600000;await Promise.all([call('/api/workflows',alice.session),call('/api/workflows',alice.session)]);assert.equal(refreshCalls,1);
  });
  await t.test('logout invalidates a copied session immediately and revokes the refresh token',async()=>{
   const logout=await call('/api/auth/logout',alice.session,'POST',{});assert.equal(logout.status,200);
   const target=new URL((await logout.json() as any).logoutUrl);assert.equal(target.origin,config.domain);assert.equal(target.pathname,'/logout');assert.equal(target.searchParams.get('logout_uri'),config.origin+'/');
   assert.equal(revocations,1);assert.equal((await call('/api/workflows',alice.session)).status,401);
  });
  await t.test('disabling a member blocks an existing session and webhook access',async()=>{
   const db=new DatabaseSync(join(directory,'auth-users.sqlite'));db.prepare("UPDATE users SET status='disabled' WHERE id=?").run(bobMe.user.id);db.close();
   assert.equal((await call('/api/workflows',bob.session)).status,403);
   assert.equal((await call('/hooks/'+bobMe.user.id+'/'+flow.id+'/hook')).status,404);
  });
 }finally{
  await app.close();await new Promise<void>(resolve=>app.server.close(()=>resolve()));
  if(previous===undefined)delete process.env.EASYFLOW_DATA_DIR;else process.env.EASYFLOW_DATA_DIR=previous;
  await rm(directory,{recursive:true,force:true});
 }
});
