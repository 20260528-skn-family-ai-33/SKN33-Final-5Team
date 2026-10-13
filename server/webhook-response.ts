import {constants,createHmac,createPrivateKey,createPublicKey,createSecretKey,sign as cryptoSign,verify as cryptoVerify,timingSafeEqual} from 'node:crypto';
import {validateHeaderName,validateHeaderValue} from 'node:http';
import type {FlowNode,Json} from '../lib/workflow';
import {decodeBinaryFile,getBinary,jsonWithoutBinary,splitBinaryItem,MAX_BINARY_BYTES,type BinaryFile} from './binary';
import type {CredentialSecret,WebhookResponse} from './contracts';

export type WebhookResponseContext={resolveValue?:(value:any,item:Json)=>any;credential?:(node:FlowNode)=>CredentialSecret|Promise<CredentialSecret>};
const MAX_RESPONSE_BYTES=4_000_000,MAX_HEADERS=100,MAX_HEADER_BYTES=32_768;
const forbidden=new Set(['__proto__','prototype','constructor']);
const transportHeaders=new Set(['transfer-encoding','connection','trailer','upgrade','keep-alive','proxy-connection']);

/** Native Buffer JSON uses the binary byte budget, rather than its expanded text size. */
export function assertWebhookJsonSize(value:unknown):void {
 let jsonBytes=0,binaryBytes=0,nodes=0;const seen=new Set<object>();
 const add=(amount:number)=>{jsonBytes+=amount;if(jsonBytes>MAX_RESPONSE_BYTES)throw new Error('단계 JSON 결과는 4MB 이내로 줄여주세요. 필요한 항목만 가져와주세요.');};
 const visit=(item:any,depth:number):void=>{
  if(++nodes>2_100_000||depth>1000)throw new Error('응답 데이터가 너무 복잡해요. 필요한 항목만 반환해주세요.');
  if(item===undefined){add(4);return;}if(item===null||typeof item==='boolean'||typeof item==='number'&&Number.isFinite(item)){add(JSON.stringify(item).length);return;}if(typeof item==='string'){add(Buffer.byteLength(JSON.stringify(item),'utf8'));return;}if(!item||typeof item!=='object'||seen.has(item))throw new Error('응답에는 순환 참조 없이 JSON으로 표현할 수 있는 데이터를 입력해주세요.');seen.add(item);
  if(Array.isArray(item)){add(2);for(let i=0;i<item.length;i++){const descriptor=Object.getOwnPropertyDescriptor(item,String(i));if(descriptor?.get||descriptor?.set)throw new Error('응답에는 자동으로 실행되는 속성을 사용할 수 없어요.');if(i)add(1);visit(descriptor?.value,depth+1);}}
  else {const record=plainRecord(item,'응답 데이터'),keys=Object.keys(record);if(keys.length===2&&Object.hasOwn(record,'type')&&Object.hasOwn(record,'data')&&record.type==='Buffer'&&Array.isArray(record.data)){
    const data=record.data;if(data.length>MAX_BINARY_BYTES)throw new Error('응답 파일은 8MB 이하로 줄여주세요.');for(let i=0;i<data.length;i++){const descriptor=Object.getOwnPropertyDescriptor(data,String(i));if(!descriptor||descriptor.get||descriptor.set||!Number.isInteger(descriptor.value)||descriptor.value<0||descriptor.value>255)throw new Error('응답 Buffer 파일은 0부터 255까지의 바이트 배열이어야 해요.');}binaryBytes+=data.length;if(binaryBytes>MAX_BINARY_BYTES)throw new Error('한 단계의 응답 파일 총 크기는 8MB 이하로 줄여주세요.');add(27);
   }else {add(2);for(const [i,key] of keys.entries()){if(record[key]===undefined)continue;if(i)add(1);add(Buffer.byteLength(JSON.stringify(key),'utf8')+1);visit(record[key],depth+1);}}}
  seen.delete(item);
 };visit(value,0);
}

function plainRecord(value:unknown,label:string):Record<string,any> {
 if(!value||typeof value!=='object'||Array.isArray(value)||![Object.prototype,null].includes(Object.getPrototypeOf(value)))throw new Error(`${label}은 JSON 객체로 입력해주세요.`);
 for(const property of Object.values(Object.getOwnPropertyDescriptors(value)))if(property.get||property.set)throw new Error(`${label}에는 자동으로 실행되는 속성을 사용할 수 없어요.`);
 return value as Record<string,any>;
}
function jsonClone(value:any):any {
 assertWebhookJsonSize(value);return value===undefined?undefined:JSON.parse(JSON.stringify(value));
}
function resolved(value:any,item:Json,context:WebhookResponseContext):any {
 if(Array.isArray(value))return value.map(entry=>resolved(entry,item,context));
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(plainRecord(value,'응답 설정')).map(([key,entry])=>[key,resolved(entry,item,context)]));
 return context.resolveValue?context.resolveValue(value,item):value;
}
function headers(value:any):Record<string,string> {
 if(value===undefined)return {};const source=plainRecord(value,'응답 헤더'),entries=source.entries;
 if(entries!==undefined&&!Array.isArray(entries))throw new Error('응답 헤더 목록을 확인해주세요.');
 const list=entries??Object.entries(source).map(([name,value])=>({name,value}));if(list.length>MAX_HEADERS)throw new Error('응답 헤더는 100개 이하로 설정해주세요.');
 const pairs:[string,string][]=[];let bytes=0;
 for(const entry of list){const row=plainRecord(entry,'응답 헤더');if(!['string','number','boolean'].includes(typeof row.name)||!['string','number','boolean'].includes(typeof row.value))throw new Error('응답 헤더 이름과 값은 글자 또는 숫자로 입력해주세요.');const name=String(row.name).toLowerCase(),value=String(row.value);if(forbidden.has(name)||transportHeaders.has(name))throw new Error('이 응답 헤더 이름은 사용할 수 없어요.');try{validateHeaderName(name);validateHeaderValue(name,value);}catch{throw new Error('응답 헤더 이름과 값에 올바른 문자를 입력해주세요. 줄바꿈은 사용할 수 없어요.');}bytes+=Buffer.byteLength(name+value,'utf8')+4;if(bytes>MAX_HEADER_BYTES)throw new Error('응답 헤더 전체 크기는 32KB 이하로 줄여주세요.');pairs.push([name,value]);}
 return Object.fromEntries(pairs);
}
function statusCode(value:any,fallback=200):number {const code=value===undefined?fallback:value;if(typeof code!=='number'||!Number.isInteger(code)||code<200||code>599)throw new Error('최종 응답 상태 코드는 200부터 599 사이의 정수로 입력해주세요. 100대 중간 응답은 아직 지원하지 않아요.');return code;}
function path(value:any):string[] {if(typeof value!=='string'||!value.trim()||value.length>500)throw new Error('응답 항목 이름을 확인해주세요.');const keys=value.replace(/\[(\d+)\]/g,'.$1').split('.');if(keys.some(key=>!key||forbidden.has(key)))throw new Error('응답 항목 이름에 사용할 수 없는 경로가 있어요.');return keys;}
function wrap(value:any,key:any):any {if(key===undefined||key==='')return value;const keys=path(key),output:Json={};let target:any=output;for(const [i,part] of keys.entries()){if(i===keys.length-1)target[part]=value;else{const next=keys[i+1];target[part]=/^\d+$/.test(next)?[]:{};target=target[part];}}return output;}
function property(value:any,key:any):any {let result:any=value;for(const segment of path(key)){if(!result||typeof result!=='object'||!Object.hasOwn(result,segment))return undefined;const descriptor=Object.getOwnPropertyDescriptor(result,segment);if(!descriptor||descriptor.get||descriptor.set)throw new Error('응답 항목에는 자동으로 실행되는 속성을 사용할 수 없어요.');result=descriptor.value;}return result;}
function binary(item:Json,key:any,automatically=false):BinaryFile {
 const fields=splitBinaryItem(item).binary,names=Object.keys(fields);if(automatically)key=names[0];if(typeof key!=='string'||!key)throw new Error('첫 번째 입력에 반환할 파일이 없어요. 파일 단계를 먼저 연결해주세요.');return {...getBinary(item,key)};
}
function binaryResponse(file:BinaryFile,code:number,responseHeaders:Record<string,string>,mimeOverride=false):WebhookResponse {
 const bytes=decodeBinaryFile(file);return {statusCode:code,headers:{...responseHeaders,'content-type':mimeOverride?file.mimeType:responseHeaders['content-type']||file.mimeType,'content-length':String(bytes.length)},binary:file};
}
function parseJson(value:any,label:string):any {if(typeof value==='string'){try{value=JSON.parse(value);}catch{throw new Error(`${label}에 올바른 JSON을 입력해주세요.`);}}return jsonClone(value);}
function token(payloadValue:any,credential:CredentialSecret):string {
 const payload=plainRecord(parseJson(payloadValue??{},'JWT 내용'),'JWT 내용'),data=plainRecord(credential.data,'JWT 연결 정보'),algorithm=data.algorithm||'HS256';if(typeof algorithm!=='string'||!/^(?:HS|RS|PS)(?:256|384|512)$|^ES(?:256|384|512)$/.test(algorithm))throw new Error('JWT 서명 알고리즘은 HS, RS, PS, ES 256·384·512 중에서 선택해주세요.');
 for(const key of ['iat','exp','nbf'])if(payload[key]!==undefined&&(typeof payload[key]!=='number'||!Number.isFinite(payload[key])))throw new Error('JWT 시간 항목에는 숫자를 입력해주세요.');
 const signingPayload={...payload,iat:payload.iat??Math.floor(Date.now()/1000)},header={alg:algorithm,typ:'JWT'},content=Buffer.from(JSON.stringify(header),'utf8').toString('base64url')+'.'+Buffer.from(JSON.stringify(signingPayload),'utf8').toString('base64url'),digest='sha'+algorithm.slice(2);
 try{
  let signature:Buffer;if(algorithm.startsWith('HS')){if(data.keyType&&data.keyType!=='passphrase'||typeof data.secret!=='string'||!data.secret)throw new Error('invalid key');signature=createHmac(digest,createSecretKey(Buffer.from(data.secret,'utf8'))).update(content).digest();}
  else {if(data.keyType&&data.keyType!=='pemKey'||typeof data.privateKey!=='string'||!data.privateKey)throw new Error('invalid key');const key=createPrivateKey(data.privateKey.replace(/\\n/g,'\n'));if(['RS','PS'].includes(algorithm.slice(0,2))){if(!['rsa','rsa-pss'].includes(key.asymmetricKeyType||'')||(key.asymmetricKeyDetails?.modulusLength||0)<2048)throw new Error('invalid rsa key');signature=cryptoSign(digest,Buffer.from(content),{key,...(algorithm.startsWith('PS')?{padding:constants.RSA_PKCS1_PSS_PADDING,saltLength:constants.RSA_PSS_SALTLEN_DIGEST}:{padding:constants.RSA_PKCS1_PADDING})});}
   else {const curve={ES256:'prime256v1',ES384:'secp384r1',ES512:'secp521r1'}[algorithm];if(key.asymmetricKeyType!=='ec'||key.asymmetricKeyDetails?.namedCurve!==curve)throw new Error('invalid ec key');signature=cryptoSign(digest,Buffer.from(content),{key,dsaEncoding:'ieee-p1363'});}}
  return content+'.'+signature.toString('base64url');
 }catch{throw new Error('JWT 서명에 실패했어요. 알고리즘에 맞는 비밀 키 또는 개인 키를 확인해주세요.');}
}

/** Build the original response object; transport formatting is kept separate. */
export async function buildRespondToWebhookResponse(node:FlowNode,input:Json[],context:WebhookResponseContext={}):Promise<WebhookResponse> {
 const item=input[0];if(!item)throw new Error('응답할 첫 번째 입력이 없어요.');const p=node.parameters,options=resolved(p.options??{},item,context);plainRecord(options,'응답 옵션');const responseHeaders=headers(options.responseHeaders),respondWith=String(resolved(p.respondWith??'firstIncomingItem',item,context)),code=statusCode(options.responseCode,respondWith==='redirect'?307:200);let body:unknown;
 switch(respondWith){
  case 'allIncomingItems':body=wrap(input.map(value=>{plainRecord(value,'응답 입력');return jsonClone(jsonWithoutBinary(value));}),options.responseKey);break;
  case 'firstIncomingItem':plainRecord(item,'응답 입력');body=wrap(jsonClone(jsonWithoutBinary(item)),options.responseKey);break;
  case 'json':{const value=resolved(p.responseBody,item,context);body=value===''||value===undefined?undefined:parseJson(value,'응답 본문');break;}
  case 'text':{const value=resolved(p.responseBody??'',item,context);if(typeof value!=='string')throw new Error('텍스트 응답 본문에는 글자를 입력해주세요.');if(Buffer.byteLength(value,'utf8')>MAX_RESPONSE_BYTES)throw new Error('텍스트 응답은 4MB 이하로 줄여주세요.');body=value;break;}
  case 'binary':return binaryResponse(binary(item,resolved(p.inputFieldName??'data',item,context),resolved(p.responseDataSource??'automatically',item,context)!=='set'),code,responseHeaders);
  case 'noData':break;
  case 'redirect':{const location=resolved(p.redirectURL,item,context);if(typeof location!=='string')throw new Error('이동할 URL을 입력해주세요.');let url:URL;try{url=new URL(location);}catch{throw new Error('이동할 URL을 올바르게 입력해주세요.');}if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw new Error('이동 주소는 http 또는 https 주소를 사용해주세요.');responseHeaders.location=location;headers(responseHeaders);break;}
  case 'jwt':{if(!context.credential)throw new Error('JWT 연결 정보를 선택해주세요.');body={token:token(resolved(p.payload??{},item,context),await context.credential(node))};break;}
  default:throw new Error('지원되는 Webhook 응답 형식을 선택해주세요.');
 }
 return {statusCode:code,headers:responseHeaders,...(body!==undefined?{body}:{} )};
}

/** Trigger replies use native Webhook parameter names, including version 2 responseCode. */
export function buildWebhookResponse(node:FlowNode,items:Json[],mode:'onReceived'|'lastNode',resolveValue?:WebhookResponseContext['resolveValue']):WebhookResponse {
 const p=node.parameters,item=items[0]||{},context={resolveValue},options=resolved(p.options??{},item,context);plainRecord(options,'응답 옵션');const configuredCode=options.responseCode?.values?.responseCode,rawCode=resolved(p.responseCode,item,context)??options.responseCode?.values?.customCode??configuredCode,code=statusCode(rawCode),responseHeaders=headers(options.responseHeaders);let body:unknown;
 if(mode==='onReceived'){const primary=resolved(p.responseData,item,context),configured=primary||options.responseData||(options.noResponseBody?'noData':undefined);if(configured!=='noData')body=(configured||options.responseData)??{message:'Workflow was started'};}
 else {const type=String(resolved(p.responseData??'firstEntryJson',item,context));switch(type){case 'allEntries':body=items.map(value=>jsonClone(jsonWithoutBinary(value)));break;case 'firstEntryJson':if(!items.length)throw new Error('반환할 마지막 단계 항목이 없어요.');body=options.responsePropertyName!==undefined&&options.responsePropertyName!==''?property(jsonWithoutBinary(item),options.responsePropertyName):jsonClone(jsonWithoutBinary(item));if(options.responseContentType)responseHeaders['content-type']=String(options.responseContentType);break;case 'firstEntryBinary':if(!items.length)throw new Error('반환할 마지막 단계 파일이 없어요.');return binaryResponse(binary(item,resolved(p.responseBinaryPropertyName??'data',item,context)),code,responseHeaders,true);case 'noData':break;default:throw new Error('지원되는 마지막 단계 응답 형식을 선택해주세요.');}}
 return {statusCode:code,headers:responseHeaders,...(body!==undefined?{body:jsonClone(body)}:{})};
}

/** HTML runs in an opaque-origin iframe, keeping workflow HTML away from editor cookies. */
export function sandboxWebhookHtml(html:string):string {
 const escaped=html.replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]!));
 return '<!doctype html><html><head><meta charset="utf-8"></head><body><iframe srcdoc="'+escaped+'" sandbox="allow-scripts allow-forms allow-popups allow-modals allow-orientation-lock allow-pointer-lock allow-presentation allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation" style="position:fixed;top:0;left:0;width:100vw;height:100vh;border:none;overflow:auto" allowtransparency="true"></iframe></body></html>';
}
export function prepareWebhookResponse(response:WebhookResponse):WebhookResponse {
 const code=statusCode(response.statusCode),responseHeaders=headers(response.headers),result:WebhookResponse={statusCode:code,headers:responseHeaders};
 if(response.binary&&response.body!==undefined)throw new Error('응답 본문과 파일을 동시에 반환할 수 없어요.');
 if(response.binary){const bytes=decodeBinaryFile(response.binary),file={...response.binary};responseHeaders['content-type']??=file.mimeType;if(/^text\/html(?:;|$)/i.test(responseHeaders['content-type']))result.body=sandboxWebhookHtml(new TextDecoder('utf-8',{fatal:true}).decode(bytes));else result.binary=file;}
 else if(response.body!==undefined){result.body=jsonClone(response.body);responseHeaders['content-type']??=typeof result.body==='string'?'text/html; charset=utf-8':'application/json; charset=utf-8';if(typeof result.body==='string'&&/^text\/html(?:;|$)/i.test(responseHeaders['content-type']))result.body=sandboxWebhookHtml(result.body);}
 if(code<200||[204,205,304].includes(code)){delete result.body;delete result.binary;delete responseHeaders['content-length'];return result;}
 const length=result.binary?decodeBinaryFile(result.binary).length:result.body===undefined?0:Buffer.byteLength(typeof result.body==='string'?result.body:JSON.stringify(result.body),'utf8');responseHeaders['content-length']=String(length);return result;
}

/** Response branch uses n8n's response envelope, with serializable Buffer data for files. */
export function webhookResponseItem(response:WebhookResponse):Json {
 const body=response.binary?{type:'Buffer',data:[...decodeBinaryFile(response.binary)]}:response.body;
 const result={response:{statusCode:response.statusCode,headers:{...response.headers},...(body!==undefined?{body:response.binary?body:jsonClone(body)}:{})}};assertWebhookJsonSize(result);return result;
}

/** Verify only the credential's algorithm and key family; return authenticated claims. */
export function verifyWebhookJwt(value:string,credential:CredentialSecret):Json {
 try{
  if(typeof value!=='string'||value.length>100_000)throw new Error('invalid token');const parts=value.split('.');if(parts.length!==3||parts.some(part=>!part||!/^[A-Za-z0-9_-]+$/.test(part)))throw new Error('invalid token');
  const bytes=parts.map(part=>{const result=Buffer.from(part,'base64url');if(result.toString('base64url')!==part)throw new Error('invalid base64');return result;}),header=plainRecord(JSON.parse(bytes[0].toString('utf8')),'JWT 헤더'),payload=plainRecord(jsonClone(JSON.parse(bytes[1].toString('utf8'))),'JWT 내용'),data=plainRecord(credential.data,'JWT 연결 정보'),algorithm=data.algorithm||'HS256';
  if(typeof algorithm!=='string'||!/^(?:HS|RS|PS)(?:256|384|512)$|^ES(?:256|384|512)$/.test(algorithm)||header.alg!==algorithm||header.crit!==undefined)throw new Error('invalid algorithm');const content=Buffer.from(parts[0]+'.'+parts[1]),digest='sha'+algorithm.slice(2);let valid=false;
  if(algorithm.startsWith('HS')){if(data.keyType&&data.keyType!=='passphrase'||typeof data.secret!=='string'||!data.secret)throw new Error('invalid key');const expected=createHmac(digest,createSecretKey(Buffer.from(data.secret,'utf8'))).update(content).digest();valid=expected.length===bytes[2].length&&timingSafeEqual(expected,bytes[2]);}
  else {if(data.keyType&&data.keyType!=='pemKey'||typeof data.publicKey!=='string'||!data.publicKey)throw new Error('invalid key');const key=createPublicKey(data.publicKey.replace(/\\n/g,'\n'));if(['RS','PS'].includes(algorithm.slice(0,2))){if(!['rsa','rsa-pss'].includes(key.asymmetricKeyType||'')||(key.asymmetricKeyDetails?.modulusLength||0)<2048)throw new Error('invalid rsa key');valid=cryptoVerify(digest,content,{key,...(algorithm.startsWith('PS')?{padding:constants.RSA_PKCS1_PSS_PADDING,saltLength:constants.RSA_PSS_SALTLEN_DIGEST}:{padding:constants.RSA_PKCS1_PADDING})},bytes[2]);}
   else {const curve={ES256:'prime256v1',ES384:'secp384r1',ES512:'secp521r1'}[algorithm];if(key.asymmetricKeyType!=='ec'||key.asymmetricKeyDetails?.namedCurve!==curve)throw new Error('invalid ec key');valid=cryptoVerify(digest,content,{key,dsaEncoding:'ieee-p1363'},bytes[2]);}}
  if(!valid)throw new Error('invalid signature');const now=Math.floor(Date.now()/1000);for(const name of ['iat','exp','nbf'])if(payload[name]!==undefined&&(typeof payload[name]!=='number'||!Number.isFinite(payload[name])))throw new Error('invalid timestamp');if(payload.exp!==undefined&&now>=payload.exp||payload.nbf!==undefined&&now<payload.nbf)throw new Error('invalid lifetime');return payload;
 }catch{throw new Error('JWT 인증에 실패했어요. 서명, 알고리즘과 유효 시간을 확인해주세요.');}
}
