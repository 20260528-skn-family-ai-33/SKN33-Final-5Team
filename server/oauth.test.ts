import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {mkdtempSync,readFileSync,readdirSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {OAuthManager,getUsableCredential,validateOAuthEndpoint,GOOGLE_SHEETS_OAUTH,OAUTH_STATE_TTL_MS,type OAuthBeginInput} from './oauth';
import {Store} from './store';
import type {CredentialSecret} from './contracts';

const authority='127.0.0.1:3010';
const generic:OAuthBeginInput={name:'외부 API',clientId:'registered-client-id',clientSecret:'private-client-secret',authorizationUrl:'https://oauth.example.com/authorize',tokenUrl:'https://oauth.example.com/token',scope:'records:read records:write'};
const json=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
const mock=(fn:(url:string,init:RequestInit)=>Response|Promise<Response>)=>((url:any,init:any)=>fn(String(url),init)) as typeof fetch;
function secret(data:Record<string,any>={}):CredentialSecret {
 return {id:randomUUID(),name:'로그인 연결',provider:'bearer',createdAt:'2026-01-01T00:00:00.000Z',updatedAt:'2026-01-01T00:00:00.000Z',data:{authType:'oauth2',clientId:generic.clientId,clientSecret:generic.clientSecret,authorizationUrl:generic.authorizationUrl,tokenUrl:generic.tokenUrl,scope:generic.scope,redirectUri:`http://${authority}/api/oauth/callback`,token:'private-expired-access',accessToken:'private-expired-access',refreshToken:'private-refresh-token',expiresAt:1000,...data}};
}

test('Google Sheets begins an offline code grant with S256 and no client secret in public metadata',()=>{
 const manager=new OAuthManager({port:3010,now:()=>1234});
 const result=manager.begin({name:'Google 시트',service:'googleSheets',clientId:'registered-google-client',clientSecret:'private-google-secret'},authority);
 const url=new URL(result.authorizationUrl);
 assert.equal(url.origin+url.pathname,GOOGLE_SHEETS_OAUTH.authorizationUrl);
 assert.equal(url.searchParams.get('scope'),GOOGLE_SHEETS_OAUTH.scope);
 assert.equal(url.searchParams.get('response_type'),'code');
 assert.equal(url.searchParams.get('access_type'),'offline');
 assert.equal(url.searchParams.get('prompt'),'consent');
 assert.equal(url.searchParams.get('code_challenge_method'),'S256');
 assert.match(url.searchParams.get('code_challenge')||'',/^[A-Za-z0-9_-]{43}$/);
 assert.equal(url.searchParams.get('state'),result.stateId);
 assert.equal(result.expiresAt,1234+OAUTH_STATE_TTL_MS);
 assert.equal(result.redirectUri,`http://${authority}/api/oauth/callback`);
 assert.ok(!JSON.stringify(result).includes('private-google-secret'));
 assert.equal(url.searchParams.has('code_verifier'),false);
});

test('code exchange sends the matching verifier and exact callback in a POST body, consumes state, and returns server-only credential data',async()=>{
 let calls=0;let authorization:URL;
 const manager=new OAuthManager({port:3010,now:()=>10000,fetch:mock((url,init)=>{
  calls++;assert.equal(url,generic.tokenUrl);assert.equal(init.method,'POST');assert.equal(init.redirect,'error');assert.equal(init.credentials,'omit');
  const headers=new Headers(init.headers);assert.equal(headers.get('Content-Type'),'application/x-www-form-urlencoded');
  const body=new URLSearchParams(String(init.body));assert.equal(body.get('grant_type'),'authorization_code');assert.equal(body.get('code'),'private-authorization-code');assert.equal(body.get('client_id'),generic.clientId);assert.equal(body.get('client_secret'),generic.clientSecret);
  assert.equal(body.get('redirect_uri'),`http://${authority}/api/oauth/callback`);
  const verifier=body.get('code_verifier')!;assert.match(verifier,/^[A-Za-z0-9_-]{43}$/);
  assert.equal(createHash('sha256').update(verifier).digest('base64url'),authorization.searchParams.get('code_challenge'));
  return json({access_token:'private-new-access',refresh_token:'private-new-refresh',expires_in:3600,token_type:'Bearer'});
 })});
 const begin=manager.begin({...generic,id:'existing-credential'},authority);authorization=new URL(begin.authorizationUrl);
 const credential=await manager.complete({state:begin.stateId,code:'private-authorization-code'},authority);
 assert.equal(credential.id,'existing-credential');assert.equal(credential.provider,'bearer');assert.equal(credential.data.authType,'oauth2');assert.equal(credential.data.token,'private-new-access');assert.equal(credential.data.accessToken,'private-new-access');assert.equal(credential.data.refreshToken,'private-new-refresh');assert.equal(credential.data.expiresAt,3610000);
 assert.equal(credential.data.code_verifier,undefined);assert.equal(credential.data.code,undefined);assert.equal(credential.data.state,undefined);
 await assert.rejects(manager.complete({state:begin.stateId,code:'private-authorization-code'},authority),/이미 사용/);assert.equal(calls,1);
});

test('each loopback authority has its own callback and a state cannot switch authority',async()=>{
 let calls=0;const manager=new OAuthManager({port:3010,fetch:mock(()=>{calls++;return json({access_token:'access',expires_in:3600});})});
 for(const host of [authority,'localhost:3010','[::1]:3010']){
  const begin=manager.begin(generic,host);assert.equal(begin.redirectUri,`http://${host}/api/oauth/callback`);
  const other=host===authority?'localhost:3010':authority;
  await assert.rejects(manager.complete({state:begin.stateId,code:'code'},other),/시작한 로컬 주소/);
  await manager.complete({state:begin.stateId,code:'code'},host);
 }
 assert.equal(calls,3);
 assert.throws(()=>manager.begin(generic,'attacker.example.com:3010'),/로컬 프로그램/);
 assert.throws(()=>manager.begin(generic,'127.0.0.1:3011'),/로컬 프로그램/);
});

test('expired, unknown, denied and concurrently reused states never repeat token exchange',async()=>{
 let now=1000,calls=0;const manager=new OAuthManager({port:3010,now:()=>now,fetch:mock(()=>{calls++;return json({access_token:'access',expires_in:3600});})});
 const expired=manager.begin(generic,authority);now+=OAUTH_STATE_TTL_MS;
 await assert.rejects(manager.complete({state:expired.stateId,code:'code'},authority),/만료/);
 await assert.rejects(manager.complete({state:'invalid',code:'code'},authority),/만료/);
 const denied=manager.begin(generic,authority);
 await assert.rejects(manager.complete({state:denied.stateId,error:'private-provider-error'},authority),/취소/);
 await assert.rejects(manager.complete({state:denied.stateId,code:'code'},authority),/이미 사용/);
 const begin=manager.begin(generic,authority);
 const results=await Promise.allSettled([manager.complete({state:begin.stateId,code:'code'},authority),manager.complete({state:begin.stateId,code:'code'},authority)]);
 assert.equal(results.filter(result=>result.status==='fulfilled').length,1);assert.equal(calls,1);
});

test('invalid and oversized callback codes consume state without leaking untrusted values',async()=>{
 let calls=0;const manager=new OAuthManager({port:3010,fetch:mock(()=>{calls++;return json({access_token:'access'});})});
 for(const code of [undefined,'','private\ncode','x'.repeat(40000)]){
  const begin=manager.begin(generic,authority);await assert.rejects(manager.complete({state:begin.stateId,code},authority),error=>!String(error).includes('private'));
  await assert.rejects(manager.complete({state:begin.stateId,code:'code'},authority),/이미 사용/);
 }
 assert.equal(calls,0);
});

test('Google preset endpoints and scopes cannot be changed to another provider',()=>{
 const manager=new OAuthManager({port:3010});
 for(const extra of [{tokenUrl:generic.tokenUrl},{authorizationUrl:generic.authorizationUrl},{scope:'https://www.googleapis.com/auth/drive'},{tokenAuthMethod:'none' as const}])assert.throws(()=>manager.begin({name:'시트',clientId:'google-client',service:'googleSheets',...extra},authority),/Google Sheets/);
 assert.doesNotThrow(()=>manager.begin({name:'읽기 전용 시트',clientId:'google-client',service:'googleSheets',scope:GOOGLE_SHEETS_OAUTH.readonlyScope},authority));
 assert.throws(()=>manager.begin({...generic,clientId:''},authority),/클라이언트 ID/);
});

test('OAuth endpoints reject local/IP/HTTP/query/fragment/embedded credentials',()=>{
 for(const value of ['http://oauth.example.com/token','https://localhost/token','https://app.localhost/token','https://app.local/token','https://127.0.0.1/token','https://0x7f000001/token','https://10.0.0.4/token','https://[::1]/token','https://private:secret@oauth.example.com/token','https://oauth.example.com/token?secret=private','https://oauth.example.com/token#private','file:///token'])assert.throws(()=>validateOAuthEndpoint(value),error=>!String(error).includes('private:secret'));
 assert.equal(validateOAuthEndpoint(generic.tokenUrl),generic.tokenUrl);
});

test('Basic token client authentication uses encoded client credentials; public clients omit their secret',async()=>{
 for(const tokenAuthMethod of ['basic','none'] as const){
  const manager=new OAuthManager({port:3010,fetch:mock((_url,init)=>{
   const body=new URLSearchParams(String(init.body));const headers=new Headers(init.headers);assert.equal(body.has('client_secret'),false);
   if(tokenAuthMethod==='basic'){assert.equal(body.has('client_id'),false);assert.equal(headers.get('Authorization'),'Basic '+Buffer.from('client+id:secret%3Avalue').toString('base64'));}
   else {assert.equal(body.get('client_id'),'client id');assert.equal(headers.has('Authorization'),false);}
   return json({access_token:'access',token_type:'Bearer'});
  })});
  const begin=manager.begin({...generic,clientId:'client id',clientSecret:'secret:value',tokenAuthMethod},authority);
  await manager.complete({state:begin.stateId,code:'code'},authority);
 }
});

test('HTTP, redirect, malformed and unsupported token responses yield safe generic errors',async()=>{
 const cases=[()=>json({error:'private-access',error_description:'private-client-secret'},400),()=>new Response(null,{status:302,headers:{Location:'https://attacker.example.com/private-access'}}),()=>new Response('private-access invalid JSON'),()=>json({access_token:'private-access',token_type:'DPoP'}),()=>json({access_token:'private-access',expires_in:0}),()=>json({access_token:'private-access',expires_in:true}),()=>json({access_token:'private\naccess'}),()=>json({access_token:'access',refresh_token:''}),()=>new Response('x'.repeat(300000))];
 for(const response of cases){
  const manager=new OAuthManager({port:3010,fetch:mock(response)});const begin=manager.begin(generic,authority);
  await assert.rejects(manager.complete({state:begin.stateId,code:'private-code'},authority),error=>/완료하지 못/.test(String(error))&&!String(error).includes('private'));
  await assert.rejects(manager.complete({state:begin.stateId,code:'private-code'},authority),/이미 사용/);
 }
 const network=new OAuthManager({port:3010,fetch:mock(()=>{throw new Error('private-client-secret private-refresh-token');})});const begin=network.begin(generic,authority);
 await assert.rejects(network.complete({state:begin.stateId,code:'code'},authority),error=>!String(error).includes('private'));
});

test('pending login requests are bounded and expire without retaining unlimited client secrets',()=>{
 let now=1000;const manager=new OAuthManager({port:3010,now:()=>now});
 for(let index=0;index<128;index++)manager.begin(generic,authority);
 assert.throws(()=>manager.begin(generic,authority),/진행 중/);now+=OAUTH_STATE_TTL_MS;
 assert.doesNotThrow(()=>manager.begin(generic,authority));
});

test('manual credentials and fresh OAuth credentials never trigger token refresh',async()=>{
 let calls=0,saves=0;const options={now:()=>100000,fetch:mock(()=>{calls++;return json({access_token:'unexpected'});})};
 const manual=secret({authType:undefined,token:'manual-token'});assert.equal(await getUsableCredential(manual,()=>{saves++;},options),manual);
 const fresh=secret({expiresAt:200000,token:undefined,accessToken:'fresh-access'});const result=await getUsableCredential(fresh,()=>{saves++;},options);
 assert.equal(result.data.token,'fresh-access');assert.equal(result.data.accessToken,'fresh-access');assert.equal(calls,0);assert.equal(saves,0);
 const noExpiry=secret({expiresAt:undefined});await getUsableCredential(noExpiry,()=>{saves++;},options);assert.equal(calls,0);
});

test('expired OAuth tokens refresh once, persist server-side, and preserve/rotate refresh tokens',async()=>{
 for(const rotate of [false,true]){
  const credential=secret({additional:'preserved'});let saved:CredentialSecret|undefined;
  const result=await getUsableCredential(credential,updated=>{saved=updated;},{now:()=>100000,fetch:mock((url,init)=>{
   assert.equal(url,generic.tokenUrl);assert.equal(init.redirect,'error');const body=new URLSearchParams(String(init.body));assert.equal(body.get('grant_type'),'refresh_token');assert.equal(body.get('refresh_token'),'private-refresh-token');assert.equal(body.get('client_secret'),generic.clientSecret);assert.equal(body.has('code_verifier'),false);assert.equal(body.has('redirect_uri'),false);
   return json({access_token:'fresh-private-access',expires_in:'3600',token_type:'bearer',...(rotate?{refresh_token:'rotated-private-refresh'}:{})});
  })});
  assert.equal(saved,result);assert.equal(result.data.token,'fresh-private-access');assert.equal(result.data.accessToken,'fresh-private-access');assert.equal(result.data.expiresAt,3700000);assert.equal(result.data.additional,'preserved');assert.equal(result.data.refreshToken,rotate?'rotated-private-refresh':'private-refresh-token');assert.equal(credential.data.token,'private-expired-access');
 }
});

test('near-expiry and ISO expiry tokens refresh, while responses without expiry do not retain an expired timestamp',async()=>{
 let calls=0;for(const expiresAt of [150000,'1970-01-01T00:01:40.000Z']){
  const result=await getUsableCredential(secret({expiresAt}),()=>{}, {now:()=>100000,fetch:mock(()=>{calls++;return json({access_token:'fresh-access'});})});assert.equal(result.data.expiresAt,undefined);
 }
 assert.equal(calls,2);
});

test('parallel requests share one refresh and a waiting caller can cancel independently',async()=>{
 let release!:(response:Response)=>void;const response=new Promise<Response>(resolve=>{release=resolve;});let calls=0,saves=0;const credential=secret();const request=mock(()=>{calls++;return response;});const controller=new AbortController();
 const first=getUsableCredential(credential,()=>{saves++;},{fetch:request,now:()=>100000});
 const second=getUsableCredential(credential,()=>{saves++;},{fetch:request,now:()=>100000});
 const canceled=getUsableCredential(credential,()=>{saves++;},{fetch:request,now:()=>100000,signal:controller.signal});controller.abort();
 await assert.rejects(canceled,/중지/);release(json({access_token:'shared-access',expires_in:3600,refresh_token:'rotated-refresh'}));
 const values=await Promise.all([first,second]);assert.equal(values[0],values[1]);assert.equal(calls,1);assert.equal(saves,1);
});

test('an initiating abort reaches token fetch and never persists a canceled refresh',async()=>{
 let requestSignal:AbortSignal|undefined,saves=0;const controller=new AbortController();
 const request=mock((_url,init)=>new Promise((_resolve,reject)=>{requestSignal=init.signal||undefined;requestSignal!.addEventListener('abort',()=>reject(new Error('private-refresh-token')),{once:true});}));
 const pending=getUsableCredential(secret(),()=>{saves++;},{fetch:request,signal:controller.signal});controller.abort('private-abort-reason');
 await assert.rejects(pending,error=>/중지/.test(String(error))&&!String(error).includes('private'));assert.equal(requestSignal?.aborted,true);assert.equal(saves,0);
 const already=new AbortController();already.abort();let calls=0;await assert.rejects(getUsableCredential(secret(),()=>{}, {signal:already.signal,fetch:mock(()=>{calls++;return json({access_token:'x'});})}),/중지/);assert.equal(calls,0);
});

test('missing refresh, unsafe token URLs, provider failures and failed persistence expose no secrets',async()=>{
 let calls=0;const fetcher=mock(()=>{calls++;throw new Error('private-client-secret private-refresh-token');});
 await assert.rejects(getUsableCredential(secret({refreshToken:undefined}),()=>{}, {fetch:fetcher}),/다시 연결/);
 await assert.rejects(getUsableCredential(secret({tokenUrl:'https://127.0.0.1/token'}),()=>{}, {fetch:fetcher}),/공개 서비스/);assert.equal(calls,0);
 await assert.rejects(getUsableCredential(secret({expiresAt:'invalid-date'}),()=>{}, {fetch:fetcher}),/만료 시각/);
 const credential=secret();await assert.rejects(getUsableCredential(credential,()=>{}, {fetch:fetcher}),error=>/갱신하지 못/.test(String(error))&&!String(error).includes('private'));
 const recovered=await getUsableCredential(credential,()=>{}, {fetch:mock(()=>json({access_token:'recovered-access',expires_in:3600}))});assert.equal(recovered.data.token,'recovered-access');
 await assert.rejects(getUsableCredential(secret(),()=>{throw new Error('private-refresh-token');}, {fetch:mock(()=>json({access_token:'access',expires_in:3600}))}),error=>/저장하지 못/.test(String(error))&&!String(error).includes('private'));
});

test('Google refresh uses the fixed official token endpoint and rejects a substituted endpoint',async()=>{
 const credential=secret({service:'googleSheets',authorizationUrl:GOOGLE_SHEETS_OAUTH.authorizationUrl,tokenUrl:GOOGLE_SHEETS_OAUTH.tokenUrl,scope:GOOGLE_SHEETS_OAUTH.scope});credential.service='googleSheets';let calls=0;
 const result=await getUsableCredential(credential,()=>{}, {fetch:mock((url,init)=>{calls++;assert.equal(url,GOOGLE_SHEETS_OAUTH.tokenUrl);assert.equal(new URLSearchParams(String(init.body)).get('client_secret'),generic.clientSecret);return json({access_token:'google-access',expires_in:3600});})});assert.equal(result.data.token,'google-access');
 await assert.rejects(getUsableCredential({...credential,id:randomUUID(),data:{...credential.data,tokenUrl:generic.tokenUrl}},()=>{}, {fetch:mock(()=>{calls++;return json({access_token:'x'});})}),/Google Sheets/);assert.equal(calls,1);
});

test('OAuth completion and refreshed secrets are encrypted by Store and public credential metadata excludes every token',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'easyflow-oauth-'));const store=new Store(directory);
 try {
  const manager=new OAuthManager({port:3010,fetch:mock(()=>json({access_token:'private-completed-access',refresh_token:'private-completed-refresh',expires_in:3600}))});const begin=manager.begin(generic,authority);const input=await manager.complete({state:begin.stateId,code:'private-code'},authority);const publicCredential=store.saveCredential(input);
  assert.equal('data' in publicCredential,false);assert.equal('accessToken' in publicCredential,false);assert.equal('refreshToken' in publicCredential,false);
  const credential=store.getCredential(publicCredential.id);credential.data.expiresAt=0;
  await getUsableCredential(credential,updated=>store.saveCredential({id:updated.id,name:updated.name,provider:updated.provider,data:updated.data}),{fetch:mock(()=>json({access_token:'private-refreshed-access',refresh_token:'private-refreshed-refresh',expires_in:3600}))});
  assert.equal(store.getCredential(publicCredential.id).data.token,'private-refreshed-access');assert.ok(!JSON.stringify(store.listCredentials()).includes('private'));
  for(const filename of readdirSync(directory).filter(name=>name.startsWith('easyflow.sqlite'))){const bytes=readFileSync(join(directory,filename));for(const value of ['private-client-secret','private-completed-access','private-completed-refresh','private-refreshed-access','private-refreshed-refresh'])assert.equal(bytes.includes(Buffer.from(value)),false);}
 } finally {store.close();rmSync(directory,{recursive:true,force:true});}
});
