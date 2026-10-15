import test from 'node:test';
import assert from 'node:assert/strict';
import {generateKeyPairSync,createHmac,verify as cryptoVerify,constants} from 'node:crypto';
import type {FlowNode,Json} from '../lib/workflow';
import type {CredentialSecret} from './contracts';
import {createBinaryFile,decodeBinaryFile,withBinary,BINARY_KEY} from './binary';
import {buildRespondToWebhookResponse,buildWebhookResponse,prepareWebhookResponse,sandboxWebhookHtml,verifyWebhookJwt,webhookResponseItem} from './webhook-response';
import {resolveValue} from './engine';

const node=(parameters:Json={}):FlowNode=>({id:'response',name:'응답',type:'n8n-nodes-base.respondToWebhook',typeVersion:1.5,position:[0,0],parameters});
const account=(data:Json):CredentialSecret=>({id:'jwt',name:'모의 JWT',provider:'jwt',createdAt:'',updatedAt:'',data});
const context={resolveValue};

test('Respond response formats evaluate parameters only against first item and strip binary data',async()=>{
 const input=[withBinary({id:1,name:'가'},'data',createBinaryFile(Buffer.from('sample'),{fileName:'a.txt',mimeType:'text/plain'})),{id:2,name:'나'}];
 const first=await buildRespondToWebhookResponse(node({options:{responseKey:'payload.customer'}}),input,context);assert.deepEqual(first,{statusCode:200,headers:{},body:{payload:{customer:{id:1,name:'가'}}}});
 const all=await buildRespondToWebhookResponse(node({respondWith:'allIncomingItems',options:{responseKey:'rows',responseHeaders:{entries:[{name:'X-Customer',value:'={{ $json.id }}'}]},responseCode:201}}),input,context);assert.deepEqual(all,{statusCode:201,headers:{'x-customer':'1'},body:{rows:[{id:1,name:'가'},{id:2,name:'나'}]}});assert.equal(JSON.stringify(all).includes(BINARY_KEY),false);
 const json=await buildRespondToWebhookResponse(node({respondWith:'json',responseBody:'={{ {selected:$json.name} }}'}),input,context);assert.deepEqual(json.body,{selected:'가'});
 assert.deepEqual((await buildRespondToWebhookResponse(node({respondWith:'json',responseBody:'[1,false,null]'}),input)).body,[1,false,null]);
 assert.equal((await buildRespondToWebhookResponse(node({respondWith:'text',responseBody:'={{ $json.name }}'}),input,context)).body,'가');
 assert.equal(Object.hasOwn(await buildRespondToWebhookResponse(node({respondWith:'noData'}),input),'body'),false);
});
test('Webhook trigger immediate and last replies honor canonical version two and legacy parameters',()=>{
 const trigger=node({responseMode:'onReceived',options:{responseCode:{values:{responseCode:'customCode',customCode:202}},responseData:'접수',responseHeaders:{entries:[{name:'X-Source',value:'test'}]}}});trigger.type='n8n-nodes-base.webhook';
 assert.deepEqual(buildWebhookResponse(trigger,[{}],'onReceived'),{statusCode:202,headers:{'x-source':'test'},body:'접수'});
 trigger.parameters.responseCode=201;assert.equal(buildWebhookResponse(trigger,[{}],'onReceived').statusCode,201);delete trigger.parameters.responseCode;
 trigger.parameters.options.noResponseBody=true;delete trigger.parameters.options.responseData;assert.equal(Object.hasOwn(buildWebhookResponse(trigger,[{}],'onReceived'),'body'),false);
 trigger.parameters.responseData='legacy custom body';trigger.parameters.options.responseData='new custom body';assert.equal(buildWebhookResponse(trigger,[{}],'onReceived').body,'legacy custom body');
 trigger.parameters.responseData='noData';assert.equal(Object.hasOwn(buildWebhookResponse(trigger,[{}],'onReceived'),'body'),false);
 delete trigger.parameters.responseData;trigger.parameters.options={responseData:''};assert.equal(buildWebhookResponse(trigger,[{}],'onReceived').body,'');
 trigger.parameters={};assert.deepEqual(buildWebhookResponse(trigger,[{}],'onReceived').body,{message:'Workflow was started'});
 const rows=[{customer:{name:'가'}},{customer:{name:'나'}}];trigger.parameters={responseData:'firstEntryJson',options:{responsePropertyName:'customer.name',responseContentType:'text/plain'}};assert.deepEqual(buildWebhookResponse(trigger,rows,'lastNode'),{statusCode:200,headers:{'content-type':'text/plain'},body:'가'});
 trigger.parameters={responseData:'allEntries',options:{}};assert.deepEqual(buildWebhookResponse(trigger,rows,'lastNode').body,rows);assert.deepEqual(buildWebhookResponse(trigger,[],'lastNode').body,[]);
 trigger.parameters={responseData:'firstEntryJson'};assert.throws(()=>buildWebhookResponse(trigger,[],'lastNode'),/항목/);
 trigger.parameters={responseData:'noData'};assert.equal(Object.hasOwn(buildWebhookResponse(trigger,[],'lastNode'),'body'),false);
});
test('binary replies own validated first-input bytes and native lastNode MIME wins',async()=>{
 const file=createBinaryFile(Buffer.from([0,1,127,255]),{mimeType:'application/octet-stream',fileName:'sample.bin'}),other=createBinaryFile(Buffer.from('second'),{fileName:'second.txt'}),item=withBinary(withBinary({id:1},'first',file),'second',other);
 const automatic=await buildRespondToWebhookResponse(node({respondWith:'binary'}),[item]);assert.equal(automatic.headers['content-length'],'4');assert.equal(automatic.headers['content-type'],file.mimeType);assert.deepEqual(decodeBinaryFile(automatic.binary),Buffer.from([0,1,127,255]));assert.notEqual(automatic.binary,file);
 const selected=await buildRespondToWebhookResponse(node({respondWith:'binary',responseDataSource:'set',inputFieldName:'second',options:{responseHeaders:{entries:[{name:'Content-Type',value:'text/plain'}]}}}),[item]);assert.equal(decodeBinaryFile(selected.binary).toString(),'second');assert.equal(selected.headers['content-type'],'text/plain');
 const last=node({responseData:'firstEntryBinary',responseBinaryPropertyName:'second',options:{responseHeaders:{entries:[{name:'Content-Type',value:'text/plain'}]}}});assert.equal(buildWebhookResponse(last,[item],'lastNode').headers['content-type'],other.mimeType);
 await assert.rejects(()=>buildRespondToWebhookResponse(node({respondWith:'binary',responseDataSource:'set',inputFieldName:'missing'}),[item]),/파일/);
 await assert.rejects(()=>buildRespondToWebhookResponse(node({respondWith:'binary'}),[{},item]),/파일/);
 const broken={...file,fileSize:999};assert.throws(()=>prepareWebhookResponse({statusCode:200,headers:{},binary:broken}),/크기/);
 assert.deepEqual(webhookResponseItem(automatic).response.body,{type:'Buffer',data:[0,1,127,255]});
});
test('response header and status validation prevent CRLF, invalid transport controls and stale length',async()=>{
 for(const responseCode of [99,100,101,103,199,600,NaN,200.5,'200'])await assert.rejects(()=>buildRespondToWebhookResponse(node({options:{responseCode}}),[{}]),/상태 코드/);
 for(const responseCode of [100,101,103,199]){assert.throws(()=>prepareWebhookResponse({statusCode:responseCode,headers:{},body:'never hang'}),/중간 응답/);assert.throws(()=>buildWebhookResponse(node({responseCode}),[{}],'onReceived'),/중간 응답/);}
 for(const entry of [{name:'X-Ok',value:'bad\r\nSet-Cookie: stolen'},{name:'X\nBad',value:'x'},{name:'Transfer-Encoding',value:'chunked'},{name:'__proto__',value:'x'}])await assert.rejects(()=>buildRespondToWebhookResponse(node({options:{responseHeaders:{entries:[entry]}}}),[{}]),/헤더/);
 const response=prepareWebhookResponse({statusCode:200,headers:{'Content-Type':'text/plain','Content-Length':'999'},body:'가나다'});assert.equal(response.headers['content-length'],'9');assert.equal(response.body,'가나다');assert.equal(response.headers['content-type'],'text/plain');
 const duplicate=await buildRespondToWebhookResponse(node({options:{responseHeaders:{entries:[{name:'X-Row',value:'first'},{name:'x-row',value:'last'}]}}}),[{}]);assert.equal(duplicate.headers['x-row'],'last');
 for(const statusCode of [204,205,304]){const empty=prepareWebhookResponse({statusCode,headers:{'Content-Length':'9'},body:'가나다'});assert.equal(Object.hasOwn(empty,'body'),false);assert.equal(Object.hasOwn(empty.headers,'content-length'),false);}
});
test('HTML text and binary replies use an escaped opaque-origin iframe with actual wrapped length',async()=>{
 const html='<!doctype html><script>parent.localStorage.getItem("key")</script><img src="x" onerror="alert(1)"> & </iframe>',response=prepareWebhookResponse({statusCode:200,headers:{},body:html});assert.equal(response.headers['content-type'],'text/html; charset=utf-8');assert.match(String(response.body),/srcdoc="/);assert.equal(String(response.body).includes('<script>'),false);assert.equal(String(response.body).includes('allow-same-origin'),false);assert.equal(response.headers['content-length'],String(Buffer.byteLength(String(response.body))));assert.equal(response.body,sandboxWebhookHtml(html));
 const binaryHtml=createBinaryFile(Buffer.from(html),{mimeType:'text/html',fileName:'page.html'}),binaryResponse=prepareWebhookResponse(await buildRespondToWebhookResponse(node({respondWith:'binary'}),[withBinary({},'page',binaryHtml)]));assert.equal(Object.hasOwn(binaryResponse,'binary'),false);assert.equal(binaryResponse.body,response.body);
 const text=prepareWebhookResponse({statusCode:200,headers:{'content-type':'text/plain'},body:html});assert.equal(text.body,html);
});
test('redirect default is 307 and validates header-safe http destination',async()=>{
 assert.deepEqual(await buildRespondToWebhookResponse(node({respondWith:'redirect',redirectURL:'https://example.test/result?id=1'}),[{}]),{statusCode:307,headers:{location:'https://example.test/result?id=1'}});
 assert.equal((await buildRespondToWebhookResponse(node({respondWith:'redirect',redirectURL:'https://example.test',options:{responseCode:302}}),[{}])).statusCode,302);
 for(const url of ['javascript:alert(1)','not-a-url','https://user:password@example.test','https://example.test/\r\nX: y'])await assert.rejects(()=>buildRespondToWebhookResponse(node({respondWith:'redirect',redirectURL:url}),[{}]));
});
test('JSON response data does not invoke accessors or pollute prototypes and rejects cycles/large outputs',async()=>{
 const unsafe=JSON.parse('{"__proto__":{"polluted":true},"constructor":"customer column"}');const response=await buildRespondToWebhookResponse(node({respondWith:'json',responseBody:unsafe}),[{}]);assert.equal(Object.hasOwn(response.body as object,'__proto__'),true);assert.equal(({} as any).polluted,undefined);
 await assert.rejects(()=>buildRespondToWebhookResponse(node({options:{responseKey:'__proto__.polluted'}}),[{}]),/경로/);await assert.rejects(()=>buildRespondToWebhookResponse(node({respondWith:'json',responseBody:'{"invalid":'}),[{}]),/JSON/);
 let reads=0;const accessor=Object.defineProperty({},'value',{enumerable:true,get(){reads++;return 1;}});await assert.rejects(()=>buildRespondToWebhookResponse(node({respondWith:'json',responseBody:accessor}),[{}]),/속성/);assert.equal(reads,0);
 const cycle:any={};cycle.self=cycle;assert.throws(()=>prepareWebhookResponse({statusCode:200,headers:{},body:cycle}),/순환/);
 await assert.rejects(()=>buildRespondToWebhookResponse(node({respondWith:'text',responseBody:'한'.repeat(1_333_334)}),[{}]),/4MB/);
});
test('JWT HMAC signing matches independent native crypto and verification rejects tampering and lifetimes',async()=>{
 for(const algorithm of ['HS256','HS384','HS512']){
  const secret='local-fixture-secret',credential=account({keyType:'passphrase',algorithm,secret}),payload={sub:'고객1',iat:123,exp:Math.floor(Date.now()/1000)+60},response=await buildRespondToWebhookResponse(node({respondWith:'jwt',payload}),[{}],{credential:()=>credential}),signed=(response.body as any).token,parts=signed.split('.');assert.deepEqual(JSON.parse(Buffer.from(parts[1],'base64url').toString()),payload);assert.equal(parts[2],createHmac('sha'+algorithm.slice(2),secret).update(parts[0]+'.'+parts[1]).digest('base64url'));assert.deepEqual(verifyWebhookJwt(signed,credential),payload);
  assert.throws(()=>verifyWebhookJwt(signed.replace(/.$/,signed.endsWith('A')?'B':'A'),credential),/인증/);assert.throws(()=>verifyWebhookJwt(signed,account({keyType:'passphrase',algorithm:'HS512',secret:'wrong'})),/인증/);
 }
 const credential=account({keyType:'passphrase',algorithm:'HS256',secret:'expiry-fixture'});for(const payload of [{exp:1},{nbf:Math.floor(Date.now()/1000)+100}]){const signed=(await buildRespondToWebhookResponse(node({respondWith:'jwt',payload}),[{}],{credential:()=>credential})).body as any;assert.throws(()=>verifyWebhookJwt(signed.token,credential),/인증/);}
 for(const data of [{keyType:'pemKey',algorithm:'HS256',secret:'x'},{keyType:'passphrase',algorithm:'none',secret:'x'}])await assert.rejects(()=>buildRespondToWebhookResponse(node({respondWith:'jwt'}),[{}],{credential:()=>account(data)}),/JWT/);
});
test('JWT RSA/PSS/ECDSA signing and verification use matching public-key algorithms and curves',async()=>{
 const rsa=generateKeyPairSync('rsa',{modulusLength:2048}),curves={ES256:'prime256v1',ES384:'secp384r1',ES512:'secp521r1'};
 for(const algorithm of ['RS256','RS384','RS512','PS256','PS384','PS512','ES256','ES384','ES512']){
  const keys=algorithm.startsWith('ES')?generateKeyPairSync('ec',{namedCurve:curves[algorithm as keyof typeof curves]}):rsa,credential=account({keyType:'pemKey',algorithm,privateKey:keys.privateKey.export({format:'pem',type:'pkcs8'}).toString(),publicKey:keys.publicKey.export({format:'pem',type:'spki'}).toString()}),signed=(await buildRespondToWebhookResponse(node({respondWith:'jwt',payload:{sub:'fixture',iat:123}}),[{}],{credential:()=>credential})).body as any,parts=signed.token.split('.');assert.equal(cryptoVerify('sha'+algorithm.slice(2),Buffer.from(parts[0]+'.'+parts[1]),{key:keys.publicKey,...(algorithm.startsWith('PS')?{padding:constants.RSA_PKCS1_PSS_PADDING,saltLength:constants.RSA_PSS_SALTLEN_DIGEST}:{}),...(algorithm.startsWith('ES')?{dsaEncoding:'ieee-p1363' as const}:{})},Buffer.from(parts[2],'base64url')),true);assert.equal(verifyWebhookJwt(signed.token,credential).sub,'fixture');
  assert.throws(()=>verifyWebhookJwt(signed.token,account({...credential.data,algorithm:'HS256',keyType:'passphrase',secret:credential.data.publicKey})),/인증/);
 }
 const wrong=generateKeyPairSync('ec',{namedCurve:'secp384r1'});await assert.rejects(()=>buildRespondToWebhookResponse(node({respondWith:'jwt'}),[{}],{credential:()=>account({algorithm:'ES256',keyType:'pemKey',privateKey:wrong.privateKey.export({format:'pem',type:'pkcs8'}).toString()})}),/서명/);
});
