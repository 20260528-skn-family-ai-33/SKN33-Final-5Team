import test from 'node:test';
import assert from 'node:assert/strict';
import {createHmac} from 'node:crypto';
import type {IncomingHttpHeaders} from 'node:http';
import type {Flow,FlowNode,Json} from '../lib/workflow';
import type {CredentialSecret,Provider} from './contracts';
import {authenticateWebhook,encodedWebhookPath,validateWebhookConfiguration,webhookAuthentication,webhookCredentialProvider,webhookParams,webhookPath,WebhookAuthError} from './webhook-auth';
import {buildRespondToWebhookResponse} from './webhook-response';

const node=(parameters:Json={}):FlowNode=>({id:'hook',webhookId:'native-webhook-id',name:'외부 요청',type:'n8n-nodes-base.webhook',typeVersion:2,position:[0,0],parameters});
const credential=(provider:Provider,data:Json):CredentialSecret=>({id:'fixture',name:'모의 계정',provider,data,createdAt:'',updatedAt:''});
const flow=(hook:FlowNode,response?:FlowNode,connected=true):Flow=>({id:'fixture',name:'Webhook 검증',nodes:[hook,...(response?[response]:[])],connections:response&&connected?{[hook.name]:{main:[[{node:response.name,type:'main',index:0}]]}}:{},settings:{timezone:'Asia/Seoul'}});
const unavailable=()=>{throw new Error('계정을 불러오면 안 됩니다.');};
async function rejectsStatus(promise:Promise<unknown>,status:number){await assert.rejects(promise,error=>error instanceof WebhookAuthError&&error.status===status);}

test('Webhook paths encode literal Korean segments and decode dynamic values after splitting',()=>{
 const hook=node({path:'/고객 목록/:customerId/요청/'});assert.equal(webhookPath(hook),'고객 목록/:customerId/요청');assert.equal(encodedWebhookPath(hook),'%EA%B3%A0%EA%B0%9D%20%EB%AA%A9%EB%A1%9D/:customerId/%EC%9A%94%EC%B2%AD');
 assert.deepEqual(webhookParams(hook,'%EA%B3%A0%EA%B0%9D%20%EB%AA%A9%EB%A1%9D/customer%2Fone/%EC%9A%94%EC%B2%AD'),{customerId:'customer/one'});
 assert.deepEqual(webhookParams(hook,'고객%20목록/%EA%B3%A0%EA%B0%9D%201/%EC%9A%94%EC%B2%AD'),{customerId:'고객 1'});
 assert.equal(webhookParams(hook,'고객%20목록/id/wrong'),undefined);assert.equal(webhookParams(hook,'고객%20목록/id/요청/extra'),undefined);assert.equal(webhookParams(hook,'고객%20목록/%E0%A4/요청'),undefined);assert.equal(webhookParams(hook,'고객%20목록//요청'),undefined);
 const literal=node({path:'customer/orders'});assert.deepEqual(webhookParams(literal,'customer%2Forders'),{});assert.deepEqual(webhookParams(literal,'customer/orders'),{});
 const percent=node({path:'discount/%sale'});assert.deepEqual(webhookParams(percent,'discount/%25sale'),{});assert.equal(webhookPath(node()),'hook');
});
test('Webhook path validation rejects traversal, malformed or duplicate variables without prototype mutation',()=>{
 for(const path of ['', '/', 'a//b','a/../b','a/./b','a?query','a#fragment','a\nheader',':id/:id',':__proto__',':constructor',':prototype',':bad-name',':','x'.repeat(201),1])assert.throws(()=>webhookPath(node({path})),/주소/);
 const hook=node({path:':safe'}),params=webhookParams(hook,'__proto__');assert.deepEqual(params,{safe:'__proto__'});assert.equal(({} as any).polluted,undefined);
});
test('Webhook configuration requires a connected response node and preserves disabled-node native configuration',()=>{
 const response:FlowNode={id:'respond',name:'요청에 응답',type:'n8n-nodes-base.respondToWebhook',typeVersion:1.5,position:[0,0],parameters:{}},hook=node({path:'valid',responseMode:'responseNode'});assert.doesNotThrow(()=>validateWebhookConfiguration(flow(hook,response),hook));assert.throws(()=>validateWebhookConfiguration(flow(hook,response,false),hook),/연결/);
 response.disabled=true;assert.doesNotThrow(()=>validateWebhookConfiguration(flow(hook,response),hook));hook.parameters.responseMode='lastNode';assert.throws(()=>validateWebhookConfiguration(flow(hook,response),hook),/응답 방법/);assert.doesNotThrow(()=>validateWebhookConfiguration(flow(hook,response,false),hook));
 for(const responseMode of ['streaming','formPage','unknown'])assert.throws(()=>validateWebhookConfiguration(flow(node({responseMode})),node({responseMode})),/아직 지원/);
 for(const httpMethod of ['OPTIONS','TRACE',['GET','POST'],'get'])assert.throws(()=>validateWebhookConfiguration(flow(node({httpMethod})),node({httpMethod})),/요청 방식/);
 for(const httpMethod of ['DELETE','GET','HEAD','PATCH','POST','PUT'])assert.doesNotThrow(()=>validateWebhookConfiguration(flow(node({httpMethod})),node({httpMethod})));
 for(const authentication of ['none','easyflowToken','basicAuth','headerAuth','bearerAuth','jwtAuth'])assert.doesNotThrow(()=>validateWebhookConfiguration(flow(node({authentication})),node({authentication})));
 assert.throws(()=>validateWebhookConfiguration(flow(node({authentication:'n8nOAuth2'})),node({authentication:'n8nOAuth2'})),/인증 방식/);
});
test('local token and no-auth modes never load credentials and remove authentication secrets from returned headers',async()=>{
 const hook=node(),token='local-secret-token',headers:IncomingHttpHeaders={'x-easyflow-token':token,authorization:'private authorization',cookie:'private cookie','x-auth-token':'private alternate','x-keep':'visible'};assert.equal(webhookAuthentication(hook),'easyflowToken');assert.equal(webhookCredentialProvider(hook),undefined);
 const result=await authenticateWebhook(hook,token,headers,new URLSearchParams('token=wrong'),unavailable);assert.deepEqual(result.headers,{'x-keep':'visible'});assert.equal(headers.authorization,'private authorization');assert.equal(headers['x-easyflow-token'],token);
 assert.deepEqual((await authenticateWebhook(hook,token,{},new URLSearchParams({token}),unavailable)).headers,{});
 for(const supplied of ['',token+' ',' '+token,token.toUpperCase()])await rejectsStatus(authenticateWebhook(hook,token,{'x-easyflow-token':supplied},new URLSearchParams(),unavailable),401);
 await rejectsStatus(authenticateWebhook(hook,undefined,{'x-easyflow-token':token},new URLSearchParams(),unavailable),401);
 hook.parameters.authentication='none';assert.deepEqual((await authenticateWebhook(hook,undefined,headers,new URLSearchParams(),unavailable)).headers,{'x-keep':'visible'});
});
test('native Basic credentials and alternate HMAC authenticate exactly and retain proper challenges',async()=>{
 const hook=node({authentication:'basicAuth'});hook.credentialId='fixture';const user='모의 사용자',password='password:with:colon',account=credential('basic',{user,password}),authorization='Basic '+Buffer.from(user+':'+password,'utf8').toString('base64');assert.equal(webhookCredentialProvider(hook),'basic');
 const result=await authenticateWebhook(hook,undefined,{authorization,'x-visible':'keep'},new URLSearchParams(),async id=>{assert.equal(id,'fixture');return account;});assert.deepEqual(result.headers,{'x-visible':'keep'});
 const signature=createHmac('sha256',user+':'+password).update(hook.id+'-'+hook.webhookId).digest('hex');assert.deepEqual((await authenticateWebhook(hook,undefined,{'x-auth-token':signature},new URLSearchParams(),()=>account)).headers,{});
 await rejectsStatus(authenticateWebhook(hook,undefined,{'x-auth-token':signature+'0'},new URLSearchParams(),()=>account),403);
 const nativeDifferentId={...hook,webhookId:'different'};await rejectsStatus(authenticateWebhook(nativeDifferentId,undefined,{'x-auth-token':signature},new URLSearchParams(),()=>account),403);
 const compatibility=credential('basic',{username:user,password});assert.deepEqual((await authenticateWebhook(hook,undefined,{authorization},new URLSearchParams(),()=>compatibility)).headers,{});
 for(const value of ['Bearer token','Basic '+Buffer.from(user+':'+password+' ').toString('base64'),'Basic @invalid'])await assert.rejects(authenticateWebhook(hook,undefined,{authorization:value,'x-auth-token':signature},new URLSearchParams(),()=>account),error=>error instanceof WebhookAuthError&&error.status===401&&Boolean(error.headers['WWW-Authenticate']));
 await assert.rejects(authenticateWebhook(hook,undefined,{},new URLSearchParams(),()=>account),error=>error instanceof WebhookAuthError&&error.status===401&&error.headers['WWW-Authenticate'].includes('charset="UTF-8"'));
});
test('custom header credentials redact only the selected secret and never mutate caller header data',async()=>{
 const hook=node({authentication:'headerAuth'});hook.credentialId='fixture';const account=credential('header',{name:'X-Custom-Secret',value:'case-Sensitive-value'}),original:IncomingHttpHeaders={'x-custom-secret':'case-Sensitive-value','x-public':'keep','x-auth-token':'private'},result=await authenticateWebhook(hook,undefined,original,new URLSearchParams(),()=>account);assert.equal(webhookCredentialProvider(hook),'header');assert.deepEqual(result.headers,{'x-public':'keep'});assert.equal(original['x-custom-secret'],'case-Sensitive-value');
 for(const supplied of ['case-sensitive-value','case-Sensitive-value ','wrong',''])await rejectsStatus(authenticateWebhook(hook,undefined,{'x-custom-secret':supplied},new URLSearchParams(),()=>account),403);
 const legacy=credential('header',{headerName:'X-Custom-Secret',headerValue:'case-Sensitive-value'});assert.deepEqual((await authenticateWebhook(hook,undefined,original,new URLSearchParams(),()=>legacy)).headers,{'x-public':'keep'});
 await rejectsStatus(authenticateWebhook(hook,undefined,original,new URLSearchParams(),()=>credential('header',{name:'Invalid\nName',value:'secret'})),500);
});
test('Bearer authentication enforces exact token and provider, with configuration failures distinct from bad auth',async()=>{
 const hook=node({authentication:'bearerAuth'});hook.credentialId='fixture';const account=credential('bearer',{token:'fixture-private-bearer'});assert.equal(webhookCredentialProvider(hook),'bearer');assert.deepEqual((await authenticateWebhook(hook,undefined,{authorization:'Bearer fixture-private-bearer','x-public':'keep'},new URLSearchParams(),()=>account)).headers,{'x-public':'keep'});
 for(const authorization of ['bearer fixture-private-bearer','Bearer  fixture-private-bearer','Bearer fixture-private-bearer ','Bearer fixture-private-bear',''])await rejectsStatus(authenticateWebhook(hook,undefined,{authorization},new URLSearchParams(),()=>account),403);
 await rejectsStatus(authenticateWebhook(hook,undefined,{authorization:['Bearer fixture-private-bearer']} as unknown as IncomingHttpHeaders,new URLSearchParams(),()=>account),403);
 await rejectsStatus(authenticateWebhook(hook,undefined,{},new URLSearchParams(),()=>credential('header',{name:'X',value:'secret'})),500);delete hook.credentialId;await rejectsStatus(authenticateWebhook(hook,undefined,{},new URLSearchParams(),unavailable),500);
 hook.parameters.authentication='unrecognized';await rejectsStatus(authenticateWebhook(hook,undefined,{},new URLSearchParams(),unavailable),400);
});
test('JWT Webhook auth returns verified native claims, strips bearer secret, and distinguishes missing from invalid tokens',async()=>{
 const hook=node({authentication:'jwtAuth'});hook.credentialId='fixture';const account=credential('jwt',{keyType:'passphrase',algorithm:'HS256',secret:'fixture-jwt-auth-secret'}),response:FlowNode={id:'respond',name:'응답',type:'n8n-nodes-base.respondToWebhook',typeVersion:1.5,position:[0,0],parameters:{respondWith:'jwt',payload:{sub:'customer',iat:123,exp:Math.floor(Date.now()/1000)+60}}},signed=(await buildRespondToWebhookResponse(response,[{}],{credential:()=>account})).body as any;
 const authenticated=await authenticateWebhook(hook,undefined,{authorization:'Bearer '+signed.token,cookie:'private','x-visible':'keep'},new URLSearchParams(),()=>account);assert.deepEqual(authenticated.headers,{'x-visible':'keep'});assert.deepEqual(authenticated.jwtPayload,{sub:'customer',iat:123,exp:response.parameters.payload.exp});assert.equal(webhookCredentialProvider(hook),'jwt');
 await rejectsStatus(authenticateWebhook(hook,undefined,{},new URLSearchParams(),()=>account),401);await rejectsStatus(authenticateWebhook(hook,undefined,{authorization:'Bearer invalid'},new URLSearchParams(),()=>account),403);
 await rejectsStatus(authenticateWebhook(hook,undefined,{authorization:'Bearer '+signed.token},new URLSearchParams(),()=>credential('jwt',{...account.data,secret:'different'})),403);
});
