import {readDocument} from './document-reader';
import {searchDocument} from './knowledge';
import {randomUUID} from 'node:crypto';
import {clone,validateFlow,nodePorts,type Flow,type FlowNode,type Json,type Execution,type NodeRun} from '../lib/workflow';
import type {RunOptions,CredentialSecret,CodeContext} from './contracts';
import {runCode} from './sandbox';
import {resolveExpression,type ExpressionContext} from './expressions';
import {executeMerge} from './merge';
import {attachItemPairs,getItemPairs,lookupItemSource,lookupItemSources,sourceParents,refKey,pairInputIndex,MAX_TRACE_REFS,MAX_TRACE_EDGES,type ItemPair,type ItemSourceRef,type TraceRecord} from './provenance';
import {dataNodeTypes,executeDataNode,normalOutputCount} from './data-nodes';
import {connectorTypes,executeConnector} from './connectors';
import {compileLoopPlan,loopNodeType,nextBatch,type LoopContext,type LoopRegion,type StructuredPlan} from './loops';
import {BINARY_KEY,binaryNodeTypes,executeBinaryNode,assertBinaryItems,jsonWithoutBinary,splitBinaryItem,getBinary,decodeBinaryFile,withBinary,readBinaryResponse} from './binary';
import {readPagination,resolvePagination,paginationUpdates,patchPaginationBody,paginationNextURL,PaginationGuard,type PaginationContext,type PaginationRequest,type PaginationResponse} from './http-pagination';
import {assertWebhookJsonSize,buildRespondToWebhookResponse,webhookResponseItem} from './webhook-response';

const triggerTypes=new Set(['n8n-nodes-base.manualTrigger','n8n-nodes-base.scheduleTrigger','n8n-nodes-base.webhook','n8n-nodes-base.executeWorkflowTrigger','n8n-nodes-base.errorTrigger']);
const forbiddenKeys=new Set(['__proto__','prototype','constructor']);
const credentialSecretKey=/^(authorization|password|secret|clientSecret|apiKey|accessToken|refreshToken|token|botToken|privateKey|headerValue|value|webhookUrl)$/i;
const maxItems=5000;
const supportedTypes=new Set(['easyflow.documentRead','easyflow.knowledgeSearch',...triggerTypes,...dataNodeTypes,...connectorTypes,...binaryNodeTypes,loopNodeType,'n8n-nodes-base.stickyNote','n8n-nodes-base.executeWorkflow','n8n-nodes-base.noOp','n8n-nodes-base.set','n8n-nodes-base.if','n8n-nodes-base.merge','n8n-nodes-base.wait','n8n-nodes-base.code','n8n-nodes-base.httpRequest','n8n-nodes-base.respondToWebhook','@n8n/n8n-nodes-langchain.openAi','@n8n/n8n-nodes-langchain.agent','n8n-nodes-base.slack']);

export function resolveValue(value:any,item:Json,context?:ExpressionContext):any {
 return resolveExpression(value,item,context);
}
function resolved(value:any,item:Json,context?:ExpressionContext):any {if(Array.isArray(value))return value.map(v=>resolved(v,item,context));if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,resolved(v,item,context)]));return resolveValue(value,item,context);}
function jsonValue(value:any,item:Json,context?:ExpressionContext):any {
 if(typeof value!=='string')return resolved(value,item,context);
 if(/^=?\s*\{\{[\s\S]*\}\}$/.test(value)){const data=resolveValue(value,item,context);return typeof data==='string'?JSON.parse(data):data;}
 try{return resolved(JSON.parse(value),item,context);}catch(error){if(!(error instanceof SyntaxError))throw error;return JSON.parse(resolveValue(value,item,context));}
}
function cast(value:any,type:string,label='값'):any {
 switch(type){
  case 'string':return value==null?'':typeof value==='object'?JSON.stringify(value):String(value);
  case 'number':{const n=typeof value==='number'?value:typeof value==='string'&&value.trim()?Number(value):NaN;if(!Number.isFinite(n))throw new Error(`${label}에는 숫자를 입력해주세요.`);return n;}
  case 'boolean':if(value===true||value==='true')return true;if(value===false||value==='false')return false;throw new Error(`${label}에는 true 또는 false를 입력해주세요.`);
  case 'object':case 'array':{const data=typeof value==='string'?JSON.parse(value):value;if(!data||typeof data!=='object'||(type==='array')!==Array.isArray(data))throw new Error(`${label}의 데이터 종류를 확인해주세요.`);return data;}
  default:return value;
 }
}
function assign(target:Json,path:string,value:any) {const keys=path.split('.');if(keys.some(k=>!k||forbiddenKeys.has(k)))throw new Error('항목 이름을 확인해주세요.');let output=target;for(const key of keys.slice(0,-1)){if(output[key]==null)output[key]={};if(typeof output[key]!=='object'||Array.isArray(output[key]))throw new Error('겹치는 항목 이름을 확인해주세요.');output=output[key];}output[keys[keys.length-1]]=value;}
function compare(left:any,right:any,operation:string):boolean {
 switch(operation){
  case 'gte':case 'afterOrEquals':return left>=right;case 'gt':case 'after':return left>right;case 'lte':case 'beforeOrEquals':return left<=right;case 'lt':case 'before':return left<right;
  case 'equals':case 'equal':return typeof left==='object'?JSON.stringify(left)===JSON.stringify(right):left===right;
  case 'notEquals':case 'notEqual':return !compare(left,right,'equals');
  case 'contains':return Array.isArray(left)?left.some(v=>compare(v,right,'equals')):String(left).includes(String(right));
  case 'notContains':return !compare(left,right,'contains');case 'startsWith':return String(left).startsWith(String(right));case 'endsWith':return String(left).endsWith(String(right));
  case 'notStartsWith':return !String(left).startsWith(String(right));case 'notEndsWith':return !String(left).endsWith(String(right));
  case 'lengthEquals':case 'lengthEqual':return left.length===right;case 'lengthNotEquals':case 'lengthNotEqual':return left.length!==right;case 'lengthGt':return left.length>right;case 'lengthGte':return left.length>=right;case 'lengthLt':return left.length<right;case 'lengthLte':return left.length<=right;
  case 'true':case 'isTrue':return left===true;case 'false':case 'isFalse':return left===false;
  case 'exists':return left!==undefined&&left!==null;case 'notExists':return left===undefined||left===null;
  case 'empty':return left==null||left===''||Array.isArray(left)&&!left.length||typeof left==='object'&&!Object.keys(left).length;
  case 'notEmpty':return !compare(left,right,'empty');
  default:throw new Error('이 조건 비교는 아직 지원하지 않아요. 숫자·글자·참/거짓 비교를 선택해주세요.');
 }
}
export function matchConditions(spec:Json,item:Json,context?:ExpressionContext):boolean {
 const conditions=spec?.conditions;if(!Array.isArray(conditions)||!conditions.length)throw new Error('확인할 조건을 하나 이상 설정해주세요.');
 const matches=conditions.map((c:any)=>{const operator=c.operator||{},type=operator.type;let left=resolveValue(c.leftValue,item,context),right=resolveValue(c.rightValue,item,context);const unary=['exists','notExists','empty','notEmpty','true','false','isTrue','isFalse'].includes(operator.operation),presence=['exists','notExists','empty','notEmpty'].includes(operator.operation);
  if(type==='dateTime'&&!presence){left=Date.parse(left);right=unary?undefined:Date.parse(right);if(!Number.isFinite(left)||!unary&&!Number.isFinite(right))throw new Error('조건에는 올바른 날짜와 시간을 입력해주세요.');}
  else if(['number','boolean','string','array','object'].includes(type)&&!presence){const rightType=String(operator.operation).startsWith('length')?'number':type==='array'&&['contains','notContains'].includes(operator.operation)?'any':type;
   if(spec.options?.typeValidation!=='loose'){const correct=(v:any,t:string)=>t==='any'?true:t==='array'?Array.isArray(v):t==='object'?v!==null&&typeof v==='object'&&!Array.isArray(v):typeof v===t&&(t!=='number'||Number.isFinite(v));if(!correct(left,type)||!unary&&!correct(right,rightType))throw new Error('조건 값의 종류가 맞지 않아요. 숫자·글자·참/거짓 설정을 확인해주세요.');}
   else {left=cast(left,type);if(!unary)right=cast(right,rightType);}
  }
  if(type==='string'&&spec.options?.caseSensitive===false){left=String(left).toLowerCase();right=String(right).toLowerCase();}return compare(left,right,operator.operation);
 });return spec.combinator==='or'?matches.some(Boolean):matches.every(Boolean);
}
function pause(milliseconds:number,signal?:AbortSignal):Promise<void> {return new Promise((resolve,reject)=>{if(signal?.aborted)return reject(new Error('실행을 취소했어요.'));const abort=()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);reject(new Error('실행을 취소했어요.'));};const timer=setTimeout(()=>{signal?.removeEventListener('abort',abort);resolve();},milliseconds);signal?.addEventListener('abort',abort,{once:true});});}
function waitDelay(node:FlowNode,item:Json={},context?:ExpressionContext):number {
 const p=node.parameters;if(p.resume&&p.resume!=='timeInterval')throw new Error('기다리기는 시간 간격 방식을 지원해요.');
 const units:Record<string,number>={milliseconds:1,seconds:1000,minutes:60000,hours:3600000,days:86400000};
 const unit=units[String(resolveValue(p.unit||'seconds',item,context))];if(!unit)throw new Error('기다릴 시간 단위를 확인해주세요.');
 const amount=Number(resolveValue(p.amount??0,item,context)),delay=amount*unit;if(!Number.isFinite(delay)||delay<0||delay>365*86400000)throw new Error('기다릴 시간을 0초에서 365일 사이로 설정해주세요.');return delay;
}
function objects(data:any):Json[] {const values=Array.isArray(data)?data:[data];return values.map(v=>v&&typeof v==='object'&&!Array.isArray(v)?v:{data:v});}
function orderedPairs(items:Json[],input:Json[]):Json[] {return attachItemPairs(items,items.map((_,item)=>[{item}]));}
function ensureSize(output:Json[][]) {const items=output.flat();if(items.length>maxItems)throw new Error('한 단계는 5,000개 항목까지 처리할 수 있어요.');assertBinaryItems(items);assertWebhookJsonSize(output.map(group=>group.map(jsonWithoutBinary)));}
function safeURL(value:any):URL {let url:URL;try{url=new URL(String(value));}catch{throw new Error('http 또는 https로 시작하는 올바른 주소를 입력해주세요.');}if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw new Error('http 또는 https 주소를 사용하고 인증 정보는 연결 정보에 저장해주세요.');return url;}
async function responseText(response:Response,limit=4_000_000,signal?:AbortSignal):Promise<string> {
 if(!response.body)return '';
 const reader=response.body.getReader(),decoder=new TextDecoder(),parts:string[]=[];let bytes=0;
 const abort=()=>{void reader.cancel().catch(()=>{});};signal?.addEventListener('abort',abort,{once:true});
 try{while(true){if(signal?.aborted){await reader.cancel().catch(()=>{});throw new Error('실행을 취소했어요.');}const {done,value}=await reader.read();if(signal?.aborted)throw new Error('실행을 취소했어요.');if(done)break;bytes+=value.byteLength;if(bytes>limit){await reader.cancel();throw new Error('API 응답이 너무 커요. 필요한 데이터만 요청해주세요.');}parts.push(decoder.decode(value,{stream:true}));}parts.push(decoder.decode());return parts.join('');}
 finally{signal?.removeEventListener('abort',abort);reader.releaseLock();}
}

class Runtime {
 private secrets=new Set<string>();
 nodeDetails:Json={};
 expressionContext:ExpressionContext={};
 codeContext:CodeContext={};
 webhookResponseSent:boolean;
 constructor(private options:RunOptions) {this.webhookResponseSent=options.resume?.webhookResponseSent===true;}
 contextFor(item:Json):ExpressionContext {const input=this.expressionContext.input||[],i=input.indexOf(item);return {...this.expressionContext,itemIndex:i<0?0:i};}
 resolve=(value:any,item:Json):any=>resolveValue(value,item,this.contextFor(item));
 json=(value:any,item:Json):any=>jsonValue(value,item,this.contextFor(item));
 matches=(spec:Json,item:Json):boolean=>matchConditions(spec,item,this.contextFor(item));
 code=(source:string,input:Json[],mode:string):Promise<Json[]> => (this.options.code||runCode)(source,input,mode,this.codeContext);

 register(value:any):void {if(value&&typeof value==='object'){for(const [key,item] of Object.entries(value)){if(credentialSecretKey.test(key))this.register(item);else if(item&&typeof item==='object')this.register(item);}}else if(typeof value==='string'&&value)this.secrets.add(value);}
 sanitize<T>(value:T):T {
  const visit=(v:any):any=>{
   if(typeof v==='string'){let result=v;for(const secret of this.secrets){if(result===secret)return '[보호된 연결 정보]';if(secret.length>=8)result=result.split(secret).join('[보호된 연결 정보]');}return result;}
   if(Array.isArray(v))return v.map(visit);
   if(v&&typeof v==='object')return Object.fromEntries(Object.entries(v).map(([key,item])=>[key,visit(item)]));
   return v;
  };return visit(value);
 }
 canceled():void {if(this.options.signal?.aborted)throw new Error('실행을 취소했어요.');}
 async credential(node:FlowNode,provider?:string):Promise<CredentialSecret> {
  const id=node.credentialId||node.parameters.credentialId||Object.values(node.credentials||{}).map((v:any)=>v?.id).find(Boolean);
  if(!id)throw new Error('연결 정보를 먼저 선택해주세요. 연결 정보 탭에서 추가할 수 있어요.');
  const c=await this.options.credential(String(id));if(provider&&c.provider!==provider)throw new Error('이 단계에 맞는 연결 정보를 선택해주세요.');this.register(c.data);return c;
 }
 async request(url:URL,init:RequestInit,timeout=30000):Promise<Response> {
  this.canceled();const signals=[AbortSignal.timeout(Math.min(120000,Math.max(100,timeout)))];if(this.options.signal)signals.push(this.options.signal);
  try{return await (this.options.fetch||fetch)(url.toString(),{...init,signal:AbortSignal.any(signals),redirect:'error'});}
  catch {this.canceled();throw new Error('요청을 완료하지 못했어요. 주소, 인터넷 연결, 제한 시간을 확인해주세요.');}
 }
 async auth(headers:Headers,node:FlowNode):Promise<void> {
  const p=node.parameters;if(!p.authentication||p.authentication==='none')return;
  const c=await this.credential(node),d=c.data;
  switch(c.provider){
   case 'bearer':{const token=d.token||d.accessToken||d.apiKey;if(!token)throw new Error('연결 정보에 인증 토큰을 입력해주세요.');headers.set('Authorization',`Bearer ${token}`);break;}
   case 'basic':{if(typeof d.username!=='string'||typeof d.password!=='string')throw new Error('연결 정보에 사용자 이름과 비밀번호를 입력해주세요.');const authorization=`Basic ${Buffer.from(`${d.username}:${d.password}`).toString('base64')}`;headers.set('Authorization',authorization);this.register(authorization);this.register(authorization.slice(6));break;}
   case 'header':{const name=d.name||d.headerName,value=d.value||d.headerValue;if(!name||value==null)throw new Error('연결 정보에 헤더 이름과 값을 입력해주세요.');headers.set(String(name),String(value));break;}
   default:throw new Error('API 단계에는 Bearer, Basic 또는 헤더 연결 정보를 사용해주세요.');
  }
 }
 async http(node:FlowNode,input:Json[]):Promise<Json[][]> {
  const p=node.parameters,method=String(p.method||'GET').toUpperCase();if(!['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'].includes(method))throw new Error('지원되는 요청 방식을 선택해주세요.');
  if(this.options.mode==='learning'&&!['GET','HEAD','OPTIONS'].includes(method))throw new Error('연습 모드에서는 데이터를 쓰거나 삭제하는 API를 호출하지 않아요. 실제 실행 모드에서 실행해주세요.');
  const pagination=readPagination(p),responseOptions=p.options?.response?.response||{},format=responseOptions.responseFormat||p.responseFormat||'autodetect';
  const fullResponse=responseOptions.fullResponse??responseOptions.includeResponseHeadersAndStatus??p.fullResponse??p.includeResponseHeadersAndStatus??false,neverError=responseOptions.neverError??p.neverError??false;
  if(typeof fullResponse!=='boolean'||typeof neverError!=='boolean')throw new Error('응답 헤더 포함과 HTTP 오류 허용은 true 또는 false로 설정해주세요.');
  if(!['json','text','autodetect','file'].includes(format))throw new Error('JSON, 텍스트 또는 파일 응답을 선택해주세요.');
  if(pagination?.parameters.some(parameter=>parameter.type==='body')&&(!p.sendBody||['GET','HEAD'].includes(method)||!['json','form-urlencoded','multipart-form-data'].includes(p.contentType||'json')))throw new Error('페이지 본문 매개변수는 JSON 또는 폼 본문을 보내는 요청에서 사용할 수 있어요.');
  const output:Json[]=[],httpPairs:ItemPair[][]=[];let fetchedItems=0,totalRequests=0,limited=false;
  for(const [itemIndex,item] of input.entries()){
   const baseURL=safeURL(this.resolve(p.url,item)),baseHeaders=new Headers();
   const pairs=(group:any)=>Array.isArray(group?.parameters)?group.parameters:[];
   if(p.sendQuery){if(p.specifyQuery==='json'){const json=this.json(p.jsonQuery,item);for(const [name,value] of Object.entries(json||{}))baseURL.searchParams.set(name,String(value));}else for(const pair of pairs(p.queryParameters))baseURL.searchParams.append(String(this.resolve(pair.name,item)),String(this.resolve(pair.value,item)??''));}
   const credentialHeader=(name:string)=>/authorization|cookie|token|api[-_]?key|secret|password/i.test(name);
   if(p.sendHeaders){if(p.specifyHeaders==='json'){const json=this.json(p.jsonHeaders,item);for(const [name,value] of Object.entries(json||{})){baseHeaders.set(name,String(value));if(credentialHeader(name))this.register(String(value));}}else for(const pair of pairs(p.headerParameters)){const name=String(this.resolve(pair.name,item)),value=String(this.resolve(pair.value,item)??'');baseHeaders.set(name,value);if(credentialHeader(name))this.register(value);}}
   const authenticated=!!(p.authentication&&p.authentication!=='none')||[...baseHeaders.keys()].some(credentialHeader)||!!pagination?.parameters.some(parameter=>parameter.type==='headers'&&credentialHeader(parameter.name));
   const guard=new PaginationGuard(pagination);let pageCount=0,url=new URL(baseURL),previous:PaginationResponse|undefined,lastRequest:PaginationRequest|undefined;
   const property=String(this.resolve(responseOptions.outputPropertyName||p.options?.outputPropertyName||p.outputPropertyName||'data',item));if(!property.trim()||forbiddenKeys.has(property)||property===BINARY_KEY||fullResponse&&['text','autodetect'].includes(format)&&['headers','statusCode','statusMessage'].includes(property))throw new Error('응답을 담을 항목 이름을 확인해주세요. 헤더·상태 항목과 다른 이름을 사용해주세요.');
   while(true){
   this.canceled();const context:PaginationContext={$json:jsonWithoutBinary(item),$pageCount:pageCount,$response:previous,$request:lastRequest},updates=paginationUpdates(pagination,context),headers=new Headers(baseHeaders);
   for(const [name,value] of Object.entries(updates.qs))if(value===undefined||value===null)url.searchParams.delete(name);else url.searchParams.set(name,String(value));
   for(const [name,value] of Object.entries(updates.headers))if(value===undefined||value===null)headers.delete(name);else{headers.set(name,String(value));if(credentialHeader(name))this.register(String(value));}
   let body:BodyInit|undefined,requestBody:any;
   if(p.sendBody){if(['GET','HEAD'].includes(method))throw new Error('GET과 HEAD에는 요청 본문을 보낼 수 없어요.');const type=p.contentType||'json';
    if(type==='json'){
     let data=p.specifyBody==='json'||p.jsonBody!==undefined?this.json(p.jsonBody,item):Object.fromEntries(pairs(p.bodyParameters).map((v:any)=>[String(this.resolve(v.name,item)),this.resolve(v.value,item)]));
     if(data===undefined)throw new Error('보낼 JSON 본문을 입력해주세요.');if(Object.keys(updates.body).length)data=patchPaginationBody(data,updates.body);requestBody=data;body=JSON.stringify(data);if(!headers.has('Content-Type'))headers.set('Content-Type','application/json');
    }else if(type==='form-urlencoded'){const form=p.specifyBody==='string'?new URLSearchParams(String(this.resolve(p.body||'',item))):new URLSearchParams();if(p.specifyBody!=='string')for(const pair of pairs(p.bodyParameters))form.append(String(this.resolve(pair.name,item)),String(this.resolve(pair.value,item)??''));for(const [name,value] of Object.entries(updates.body))if(value===undefined)form.delete(name);else form.set(name,String(value??''));body=form;requestBody=Object.fromEntries(form);if(!headers.has('Content-Type'))headers.set('Content-Type','application/x-www-form-urlencoded');}
    else if(type==='multipart-form-data'){const form=new FormData();for(const pair of pairs(p.bodyParameters)){const name=String(this.resolve(pair.name,item));if(pair.parameterType==='formBinaryData'){const file=getBinary(item,String(this.resolve(pair.inputDataFieldName||pair.binaryPropertyName||'data',item)));form.append(name,new Blob([new Uint8Array(decodeBinaryFile(file))],{type:file.mimeType}),file.fileName);}else form.append(name,String(this.resolve(pair.value,item)??''));}for(const [name,value] of Object.entries(updates.body))if(value===undefined)form.delete(name);else form.set(name,String(value??''));body=form;requestBody=Object.fromEntries([...form.entries()].map(([name,value])=>[name,typeof value==='string'?value:{fileName:value.name,size:value.size,type:value.type}]));headers.delete('Content-Type');}
    else if(type==='binaryData'){const file=getBinary(item,String(this.resolve(p.inputDataFieldName||p.binaryPropertyName||'data',item)));body=new Blob([new Uint8Array(decodeBinaryFile(file))],{type:file.mimeType});requestBody={fileName:file.fileName,size:file.fileSize};if(!headers.has('Content-Type'))headers.set('Content-Type',file.mimeType);}
    else if(type==='raw'){body=String(this.resolve(p.body??p.rawBody??'',item));requestBody=body;if(p.rawContentType)headers.set('Content-Type',String(p.rawContentType));}
    else throw new Error('JSON, 일반 텍스트, 파일 또는 폼 본문을 선택해주세요.');
   }
   const request:PaginationRequest={url:url.toString(),method,headers:Object.fromEntries(headers),qs:Object.fromEntries(url.searchParams),body:requestBody};guard.claim(request,authenticated);
   await this.auth(headers,node);lastRequest={...request,headers:Object.fromEntries(headers)};
   const response=await this.request(url,{method,headers,body},Number(p.options?.timeout)||30000);
   if(!response.ok&&!neverError){void response.body?.cancel().catch(()=>{});throw new Error(`API가 HTTP ${response.status}로 응답했어요. 요청 주소와 연결 정보를 확인해주세요.`);}
   const responseHeaders=Object.fromEntries(response.headers),mime=response.headers.get('content-type')||'',actualFormat=format==='autodetect'?(mime.includes('json')?'json':/^(?:image\/|audio\/|video\/|application\/(?:octet-stream|pdf|zip|gzip))/i.test(mime)?'file':'text'):format;
   if(actualFormat==='file'){
    const name=String(this.resolve(p.options?.fileName||p.options?.response?.response?.fileName||decodeURIComponent(url.pathname.split('/').at(-1)||'file.bin'),item));
    const file=await readBinaryResponse(neverError&&!response.ok?new Response(response.body,{headers:response.headers}):response,{fileName:name},this.options.signal);
    const result=fullResponse?{...(item[BINARY_KEY]?{[BINARY_KEY]:clone(item[BINARY_KEY])}:{}),headers:responseHeaders,statusCode:response.status,statusMessage:response.statusText}:clone(item);
    output.push(withBinary(result,property,file));previous={body:undefined,headers:responseHeaders,statusCode:response.status,statusMessage:response.statusText,binaryBytes:file.fileSize};
   }else{
   const text=await responseText(response,4_000_000,this.options.signal);
   let data:any;if(method==='HEAD'||response.status===204||!text.trim())data=undefined;else if(actualFormat==='text')data=text;else {try{data=JSON.parse(text);}catch{throw new Error('API 응답이 JSON이 아니에요. 응답 형식을 텍스트로 바꿔주세요.');}}
   previous={body:data,headers:responseHeaders,statusCode:response.status,statusMessage:response.statusText};
   if(actualFormat==='text')output.push({[property]:data??'',...(fullResponse?{headers:responseHeaders,statusCode:response.status,statusMessage:response.statusText}:{})});
   else if(fullResponse)output.push({body:data??{},headers:responseHeaders,statusCode:response.status,statusMessage:response.statusText});else output.push(...objects(data??{}));
   }
   while(httpPairs.length<output.length)httpPairs.push([{item:itemIndex}]);fetchedItems+=Array.isArray(previous.body)?previous.body.length:1;if(fetchedItems>maxItems)throw new Error('API 페이지 결과는 총 5,000개 항목까지 처리할 수 있어요.');ensureSize([output]);const after:PaginationContext={$json:jsonWithoutBinary(item),$pageCount:pageCount,$response:previous,$request:lastRequest};if(guard.shouldStop(after))break;
   if(pagination?.paginationMode==='responseContainsNextURL')url=paginationNextURL(resolvePagination(pagination.nextURL,after),url);else url=new URL(baseURL);
   await pause(pagination!.requestInterval,this.options.signal);pageCount++;
   }
   totalRequests+=guard.requestsFetched;limited||=guard.limited;
  }
  if(pagination)this.nodeDetails={pagination:{pagesFetched:totalRequests,itemsFetched:fetchedItems,complete:!limited,limited,maxRequests:pagination.maxRequests},...(limited?{warning:`설정한 ${pagination.maxRequests}페이지까지만 가져왔어요. 뒤에 결과가 더 있을 수 있어요.`}:{})};
  return [attachItemPairs(output,httpPairs)];
 }
 private async openaiKey(node:FlowNode):Promise<string> {const c=await this.credential(node,'openai'),key=c.data.apiKey||c.data.key||c.data.token;if(typeof key!=='string'||!key)throw new Error('OpenAI 연결 정보에 API 키를 입력해주세요.');return key;}
 private messages(node:FlowNode,item:Json):{role:string;content:string}[] {
  const p=node.parameters,values=p.messages?.values||p.messages;
  if(Array.isArray(values)&&values.length)return values.map(v=>({role:v.role||'user',content:String(this.resolve(v.content,item)??'')}));
  const content=this.resolve(p.text??p.prompt??'={{ $json.prompt }}',item);if(!content)throw new Error('AI에게 보낼 내용을 입력해주세요.');
  return [{role:'system',content:String(p.options?.systemMessage||p.systemPrompt||'사용자 요청에 한국어로 정확하게 답하세요.')},{role:'user',content:String(content)}];
 }
 private async openai(body:Json,key:string,chat=false):Promise<Json> {
  const response=await this.request(new URL(`https://api.openai.com/v1/${chat?'chat/completions':'responses'}`),{method:'POST',headers:{Authorization:`Bearer ${key}`,'Content-Type':'application/json'},body:JSON.stringify(body)},60000);
  if(!response.ok){if(response.status===401)throw new Error('OpenAI API 키를 확인해주세요.');if(response.status===429)throw new Error('OpenAI 사용 한도에 도달했어요. 잠시 후 실행하거나 계정 한도를 확인해주세요.');throw new Error(`OpenAI 요청을 처리하지 못했어요(HTTP ${response.status}). 모델 이름과 설정을 확인해주세요.`);}
  let data:Json;try{data=JSON.parse(await responseText(response,2_000_000));}catch{throw new Error('OpenAI 응답을 읽을 수 없어요.');}
  if(data.error||data.status==='failed'||data.status==='incomplete')throw new Error('AI가 응답을 완성하지 못했어요. 입력과 출력 길이 설정을 확인해주세요.');return data;
 }
 private responseText(data:Json,chat=false):string {
  if(chat){const message=data.choices?.[0]?.message;if(message?.refusal)throw new Error('AI가 이 요청에 답할 수 없다고 응답했어요.');if(typeof message?.content!=='string')throw new Error('AI의 텍스트 응답을 받지 못했어요.');return message.content;}
  const content=(data.output||[]).filter((v:any)=>v.type==='message').flatMap((v:any)=>v.content||[]);if(content.some((v:any)=>v.type==='refusal'))throw new Error('AI가 이 요청에 답할 수 없다고 응답했어요.');
  const text=content.filter((v:any)=>v.type==='output_text').map((v:any)=>v.text||'').join('');if(!text)throw new Error('AI의 텍스트 응답을 받지 못했어요.');return text;
 }
 async ai(node:FlowNode,input:Json[],agent=false):Promise<Json[][]> {
  if(this.options.mode==='learning')throw new Error('연습 모드에서는 AI API를 호출하지 않아요. 연결 정보를 선택하고 실제 실행 모드로 실행해주세요.');
  const p=node.parameters;if(!agent&&(p.resource&&p.resource!=='text'||p.operation&&p.operation!=='message'))throw new Error('현재 AI 단계는 텍스트 응답을 지원해요.');
  const key=await this.openaiKey(node),model=p.modelId?.value||p.modelId||p.model;
  if(typeof model!=='string'||!model)throw new Error('계정에서 사용할 수 있는 AI 모델 이름을 입력해주세요.');
  const chat=!agent&&(p.apiMode==='chat'||p.api==='chat'),output:Json[]=[];
  for(const item of input){
   const messages=this.messages(node,item),instructions=messages.filter(v=>['system','developer'].includes(v.role)).map(v=>v.content).join('\n');
   const conversation:any[]=messages.filter(v=>!['system','developer'].includes(v.role));
   const body:Json=chat?{model,messages,stream:false}:{model,instructions,input:conversation,store:false};
   const maxTokens=p.options?.maxOutputTokens??p.options?.maxTokens??p.maxOutputTokens;if(maxTokens!==undefined)body[chat?'max_completion_tokens':'max_output_tokens']=Number(maxTokens);
   if(p.options?.temperature!==undefined)body.temperature=Number(p.options.temperature);
   let tools:Json[]=[];
   if(agent){
    if(!Array.isArray(p.tools)||!p.tools.length)throw new Error('AI 도우미가 사용할 읽기 전용 API 도구를 설정해주세요.');
    tools=p.tools.map((tool:any,index:number)=>{const url=safeURL(this.resolve(tool.url,item));if(tool.method&&String(tool.method).toUpperCase()!=='GET')throw new Error('AI 도구는 GET 읽기 요청만 사용할 수 있어요.');const name=tool.name||`read_api_${index+1}`;if(!/^[a-zA-Z_][\w-]{0,63}$/.test(name))throw new Error('AI 도구 이름은 영문과 숫자를 사용해주세요.');return {...tool,name,url:url.toString()};});
    if(new Set(tools.map(v=>v.name)).size!==tools.length)throw new Error('AI 도구 이름은 서로 달라야 해요.');
    body.tools=tools.map(tool=>({type:'function',name:tool.name,description:tool.description||`Read JSON from ${tool.url}`,parameters:{type:'object',properties:{},required:[],additionalProperties:false},strict:true}));body.parallel_tool_calls=false;
   }
   let data=await this.openai(body,key,chat),calls=0;
   while(agent){
    const requested=(data.output||[]).filter((v:any)=>v.type==='function_call');if(!requested.length)break;
    if(calls+requested.length>5)throw new Error('AI 도우미가 도구 사용 5회 제한에 도달했어요. 요청 범위를 줄여주세요.');
    conversation.push(...data.output);
    for(const call of requested){
     const tool=tools.find(v=>v.name===call.name);if(!tool)throw new Error('AI가 등록되지 않은 도구를 요청했어요.');
     let args:any;try{args=JSON.parse(call.arguments||'{}');}catch{throw new Error('AI 도구 입력을 해석할 수 없어요.');}if(!args||typeof args!=='object'||Array.isArray(args)||Object.keys(args).length)throw new Error('AI 도구에는 임의의 주소나 인수를 보낼 수 없어요.');
     const toolResponse=await this.request(safeURL(tool.url),{method:'GET',headers:{Accept:'application/json'}},30000);if(!toolResponse.ok)throw new Error(`AI 도구 API가 HTTP ${toolResponse.status}로 응답했어요.`);
     const text=await responseText(toolResponse,200_000);
     conversation.push({type:'function_call_output',call_id:call.call_id,output:text});calls++;
    }
    body.input=conversation;data=await this.openai(body,key);
   }
   const content=this.responseText(data,chat);output.push({message:{role:'assistant',content},text:content,output:content,usage:data.usage||{},...(agent?{toolCalls:calls}:{})});
  }
  return [orderedPairs(output,input)];
 }
 async slack(node:FlowNode,input:Json[]):Promise<Json[][]> {
  if(this.options.mode==='learning')throw new Error('연습 모드에서는 Slack에 메시지를 보내지 않아요. 실제 실행 모드로 실행해주세요.');
  const p=node.parameters;if(p.resource&&p.resource!=='message'||p.operation&&p.operation!=='post')throw new Error('Slack 단계는 메시지 보내기를 지원해요.');
  const credential=await this.credential(node,'slack'),token=credential.data.token||credential.data.botToken||credential.data.accessToken;if(!token)throw new Error('Slack 연결 정보에 봇 토큰을 입력해주세요.');
  const output:Json[]=[];
  for(const item of input){const channel=this.resolve(p.channelId?.value||p.channelId||p.channel,item),text=this.resolve(p.text,item);if(!channel||!text)throw new Error('받을 채널 ID와 메시지 내용을 입력해주세요.');
   const body:Json={channel:String(channel),text:String(text)};if(p.otherOptions?.thread_ts)body.thread_ts=this.resolve(p.otherOptions.thread_ts,item);if(p.blocks)body.blocks=this.json(p.blocks,item);
   const response=await this.request(new URL('https://slack.com/api/chat.postMessage'),{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(body)});
   if(!response.ok)throw new Error(response.status===429?'Slack 전송 한도에 도달했어요. 잠시 후 다시 실행해주세요.':`Slack 요청이 HTTP ${response.status}로 응답했어요.`);
   const data:any=JSON.parse(await responseText(response,1_000_000));if(data.ok!==true){const errors:Record<string,string>={channel_not_found:'채널 ID와 봇의 채널 참여 여부를 확인해주세요.',not_in_channel:'Slack 봇을 해당 채널에 초대해주세요.',invalid_auth:'Slack 봇 토큰을 확인해주세요.',missing_scope:'Slack 앱에 chat:write 권한을 추가해주세요.',token_revoked:'Slack 토큰이 해제되었어요. 다시 연결해주세요.'};throw new Error(errors[data.error]||'Slack 메시지를 보내지 못했어요. 채널과 앱 권한을 확인해주세요.');}
   output.push({channel:data.channel,ts:data.ts,message:data.message});
  }
  return [orderedPairs(output,input)];
 }
 async node(node:FlowNode,input:Json[]):Promise<Json[][]> {
  this.canceled();this.nodeDetails={};const p=node.parameters;
  if(triggerTypes.has(node.type)||node.type==='n8n-nodes-base.noOp')return [orderedPairs(input,input)];
  if(dataNodeTypes.includes(node.type))return executeDataNode(node,input,{resolve:this.resolve,matches:this.matches,code:this.code});
  if(binaryNodeTypes.includes(node.type))return executeBinaryNode(node,input,{resolveValue:this.resolve,signal:this.options.signal});
  if(connectorTypes.includes(node.type))return executeConnector(node,input,{mode:this.options.mode,credential:this.credential.bind(this),request:this.request.bind(this),resolveValue:this.resolve,registerSecret:this.register.bind(this)});
  if(node.type==='easyflow.documentRead')return readDocument(node,input,this.options.signal);
  if(node.type==='easyflow.knowledgeSearch')return searchDocument(node,input,this.resolve);
  switch(node.type){
   case 'n8n-nodes-base.set':{
    const preserveFiles=(item:Json,data:Json)=>{const {binary}=splitBinaryItem(item);return {...data,...(Object.keys(binary).length?{[BINARY_KEY]:clone(binary)}:{})};};
    if(p.mode==='raw')return [orderedPairs(input.map(item=>{const data=this.json(p.jsonOutput,item);if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('데이터 정리 결과는 JSON 객체여야 해요.');return preserveFiles(item,{...(p.includeOtherFields?clone(jsonWithoutBinary(item)):{}),...data});}),input)];
    const assignments=p.assignments?.assignments;if(!Array.isArray(assignments))throw new Error('정리할 항목을 먼저 입력해주세요.');
    return [orderedPairs(input.map(item=>{const data:Json=p.includeOtherFields?clone(jsonWithoutBinary(item)):{};for(const a of assignments){if(typeof a.name!=='string'||!a.name||a.name.split('.').includes(BINARY_KEY))throw new Error('항목 이름을 입력해주세요. 파일 정보는 파일 단계에서 관리해주세요.');assign(data,a.name,cast(this.resolve(a.value,item),a.type,a.name));}return preserveFiles(item,data);}),input)];
   }
   case 'n8n-nodes-base.if':{
    const yes:Json[]=[],no:Json[]=[],yesPairs:ItemPair[][]=[],noPairs:ItemPair[][]=[];for(const [index,item] of input.entries()){const match=this.matches(p.conditions,item);(match?yes:no).push(item);(match?yesPairs:noPairs).push([{item:index}]);}return [attachItemPairs(yes,yesPairs),attachItemPairs(no,noPairs)];
   }
   case 'n8n-nodes-base.merge':return executeMerge(node,this.expressionContext.inputByPort||[input]);
   case 'n8n-nodes-base.wait':return [input];
   case 'n8n-nodes-base.code':if(p.language&&p.language!=='javaScript')throw new Error('맞춤 계산은 JavaScript를 지원해요.');return [await this.code(p.jsCode||'',input,p.mode||'runOnceForAllItems')];
   case 'n8n-nodes-base.httpRequest':return this.http(node,input);
   case 'n8n-nodes-base.respondToWebhook':{
    const output=[orderedPairs(input,input)],responseBranch=node.typeVersion===1.3||node.typeVersion>=1.4&&p.enableResponseOutput===true;if(responseBranch)output.push([]);
    // No open request channel exists for manual, child, resumed or later responses.
    if(!this.options.onWebhookResponse||this.options.webhookResponseMode!=='responseNode'||this.webhookResponseSent)return output;
    const response=await buildRespondToWebhookResponse(node,input,{resolveValue:this.resolve,credential:n=>this.credential(n,'jwt')});
    this.webhookResponseSent=true;await this.options.onWebhookResponse(response);
    if(responseBranch)output[1]=attachItemPairs([webhookResponseItem(response)],[[{item:0}]]);return output;
   }
   case '@n8n/n8n-nodes-langchain.openAi':return this.ai(node,input);
   case '@n8n/n8n-nodes-langchain.agent':return this.ai(node,input,true);
   case 'n8n-nodes-base.slack':return this.slack(node,input);
   default:throw new Error('이 단계는 아직 독립 실행 엔진에서 지원하지 않아요. 지원되는 단계로 바꿔주세요.');
  }
 }
}

/** Execute a DAG or validated structured loops. Replay keys identify individual activations. */
export async function executeFlow(value:Flow,initial:Json[]=[{}],options:RunOptions):Promise<Execution> {
 const execution:Execution=options.resume?clone(options.resume):{id:options.executionId||randomUUID(),workflowId:value.id,workflowName:value.name,status:'running',startedAt:new Date().toISOString(),duration:0,runs:[],mode:options.mode};
 const runtime=new Runtime(options),started=Date.now(),previousDuration=execution.duration||0;
 const checkpoint=async()=>{execution.duration=previousDuration+Date.now()-started;if(runtime.webhookResponseSent)execution.webhookResponseSent=true;await options.onProgress?.(runtime.sanitize(clone(execution)));};
 try {
  runtime.canceled();const flow=validateFlow(clone(value));if(!Array.isArray(initial)||initial.some(v=>!v||typeof v!=='object'||Array.isArray(v)))throw new Error('시작 데이터는 JSON 객체의 배열이어야 해요.');ensureSize([initial]);
  if((options.depth??0)>8)throw new Error('하위 자동화는 8단계까지 호출할 수 있어요. 서로 다시 호출하는 연결을 확인해주세요.');
  if(options.resume&&(execution.workflowId!==flow.id||execution.mode!==options.mode))throw new Error('이 실행 기록과 워크플로 또는 실행 모드가 달라요.');
  if(options.onlyNode&&!flow.nodes.some(n=>n.id===options.onlyNode))throw new Error('시험 실행할 단계를 찾을 수 없어요.');
  if(options.onlyNode&&flow.nodes.find(n=>n.id===options.onlyNode)?.type==='n8n-nodes-base.stickyNote')throw new Error('메모는 실행하는 단계가 아니에요. 처리 단계를 선택해주세요.');
  const unsupported=flow.nodes.find(n=>!n.disabled&&!supportedTypes.has(n.type));if(unsupported)throw new Error(`${unsupported.name}: 이 단계는 아직 독립 실행 엔진에서 지원하지 않아요.`);
  const byName=new Map(flow.nodes.map(n=>[n.name,n])),incoming=new Map(flow.nodes.map(n=>[n.name,0]));
  const ports=(node:FlowNode)=>node.type==='n8n-nodes-base.stickyNote'?{inputs:0,outputs:[]}:node.type===loopNodeType?{inputs:1,outputs:Array.from({length:node.onError==='continueErrorOutput'?3:2},()=> '')}:nodePorts(node);
  const outputCount=(node:FlowNode)=>node.type===loopNodeType?2:node.type==='n8n-nodes-base.respondToWebhook'&&(node.typeVersion===1.3||node.typeVersion>=1.4&&node.parameters.enableResponseOutput===true)?2:normalOutputCount(node);
  for(const [name,channels] of Object.entries(flow.connections)){if(Object.keys(channels).some(key=>key!=='main'))throw new Error('AI 하위 연결 대신 AI 도우미의 도구 설정을 사용해주세요.');for(const [index,edges] of (channels.main||[]).entries())for(const edge of edges){if(index>=ports(byName.get(name)!).outputs.length)throw new Error('연결의 출력 경로 번호가 해당 단계의 경로 수를 넘었어요.');if(edge.index>=ports(byName.get(edge.node)!).inputs)throw new Error('연결의 입력 번호가 해당 단계의 입력 수를 넘었어요.');if(edge.type&&edge.type!=='main')throw new Error('기본 데이터 연결만 사용할 수 있어요.');incoming.set(edge.node,(incoming.get(edge.node)||0)+1);}}
  const plan:StructuredPlan|undefined=flow.nodes.some(node=>node.type===loopNodeType)?compileLoopPlan(flow):undefined;
  const counts=new Map(incoming),queue=flow.nodes.filter(n=>!counts.get(n.name)),ordered:FlowNode[]=[];
  while(queue.length){const node=queue.shift()!;ordered.push(node);for(const edges of flow.connections[node.name]?.main||[])for(const edge of edges){counts.set(edge.node,counts.get(edge.node)!-1);if(!counts.get(edge.node))queue.push(byName.get(edge.node)!);}}
  if(!plan&&ordered.length!==flow.nodes.length)throw new Error('순환 연결이 있어요. 단계 연결이 시작점으로 되돌아가지 않도록 바꿔주세요.');
  const maxSteps=options.maxSteps??(plan?20000:200);if(!Number.isSafeInteger(maxSteps)||maxSteps<1||maxSteps>1000000||flow.nodes.filter(node=>node.type!=='n8n-nodes-base.stickyNote').length>maxSteps)throw new Error('한 번에 실행할 수 있는 단계 수를 넘었어요.');
  const roots=flow.nodes.filter(n=>triggerTypes.has(n.type)&&!n.disabled);if(roots.length!==1)throw new Error('시작 단계를 하나 연결해주세요.');if(incoming.get(roots[0].name))throw new Error('시작 단계에는 이전 단계를 연결할 수 없어요.');
  const expressionMode=options.expressionMode??(execution.trigger?(execution.trigger==='manual'?'test':'production'):roots[0].type==='n8n-nodes-base.manualTrigger'?'test':'production');
  const cached=new Map(execution.runs.filter(r=>r.status==='success'||r.status==='skipped'||r.continued).map(r=>[r.executionKey||r.nodeId,r]));execution.runs=[];execution.status='running';delete execution.error;
  const loopContexts=new Map<string,LoopContext>(),loopLineages=new Map<string,{remaining:ItemSourceRef[][];processed:ItemSourceRef[][]}>(),runIndexes=new Map<string,number>(),completed=new Map<string,NodeRun>();let steps=0,halted=false;
  const traceRecord=(record:NodeRun):TraceRecord=>({key:record.executionKey||record.nodeId,name:record.name,output:record.output,inputRefs:record.inputRefs,outputPairedItems:record.outputPairedItems,outputSourceRefs:record.outputSourceRefs,inputPortCounts:record.inputPortCounts});
  const lookupTrace=(key:string)=>{const record=completed.get(key);return record?traceRecord(record):undefined;};
  const visibleRuns=(scope:string)=>execution.runs.filter(record=>{
   if(record.status==='skipped'||record.status==='error'&&!record.continued)return false;
   const origin=String(record.executionScope||'root');return origin===scope||scope.startsWith(origin+'/');
  });
  const connectedOutput=(source:string,target:string):number=>{
   const found:number[]=[];
   for(const [output,edges] of (flow.connections[source]?.main||[]).entries()){
    const queue=edges.filter((_,i)=>!plan?.feedbackKeys.has(`${source}:${output}:${i}`)).map(edge=>edge.node),visited=new Set<string>();
    while(queue.length){const name=queue.shift()!;if(name===target){found.push(output);break;}if(visited.has(name))continue;visited.add(name);for(const [slot,next] of (flow.connections[name]?.main||[]).entries())for(const [i,edge] of next.entries())if(!plan?.feedbackKeys.has(`${name}:${slot}:${i}`))queue.push(edge.node);}
   }
   return found.length===1?found[0]:0;
  };
  const expressionContext=(node:FlowNode,input:Json[],scope:string,runIndex:number,inputByPort:Json[][],inputRefs:ItemSourceRef[][]):ExpressionContext=>({
   input,inputByPort,runIndex,params:node.parameters,execution:{id:execution.id,mode:expressionMode},
   linkedItem:(name,index=0)=>{if(!byName.has(name))throw new Error(`${name}: 참조한 단계 이름을 찾을 수 없어요.`);return lookupItemSource(name,index,inputRefs,lookupTrace).item;},
   node:(name,outputIndex,requestedRun)=>{
    const referenced=byName.get(name);if(!referenced)throw new Error(`${name}: 참조한 단계 이름을 찾을 수 없어요.`);
    const available=visibleRuns(scope).filter(record=>record.nodeId===referenced.id);
    const record=requestedRun===undefined||requestedRun===-1?available.sort((a,b)=>String(b.executionScope||'root').length-String(a.executionScope||'root').length).find(Boolean):available.find(record=>(record.runIndex??0)===requestedRun);
    if(!record)return {items:[],isExecuted:false};
    // Prefer the most recent activation among records in the nearest visible scope.
    const nearest=String(record.executionScope||'root'),selected=requestedRun===undefined||requestedRun===-1?available.filter(r=>String(r.executionScope||'root')===nearest).at(-1)!:record;
    const branch=outputIndex??connectedOutput(name,node.name);if(!Number.isSafeInteger(branch)||branch<0||branch>=selected.output.length)throw new Error('참조할 이전 단계의 출력 경로 번호를 확인해주세요.');
    return {items:selected.output[branch],isExecuted:true};
   }
  });
  if(plan)execution.loopContexts={};
  const subflow=async(node:FlowNode,input:Json[],key:string):Promise<Json[][]|null>=>{
   const p=node.parameters;if(p.options?.waitForSubWorkflow===false)throw new Error('하위 자동화는 완료를 기다리는 실행을 지원해요.');if(p.mode&&p.mode!=='once'&&p.mode!=='each')throw new Error('하위 자동화는 전체 입력 또는 항목마다 실행을 선택해주세요.');
   const state=execution.subflows??={};
   if(!state[key]){
    const groups=p.mode==='each'?input.map(item=>[item]):[input];
    state[key]={jobs:groups.map(items=>{let child:Flow|undefined;if(!p.source||p.source==='database'){const id=runtime.resolve(p.workflowId?.value||p.workflowId,items[0]||{});if(typeof id!=='string'||!id)throw new Error('호출할 하위 자동화 ID를 선택해주세요.');child=options.getFlow?.(id);}else if(p.source==='parameter')child=validateFlow(runtime.json(p.workflowJson||p.workflowJSON,items[0]||{}));else throw new Error('하위 자동화는 저장된 ID 또는 JSON 설정으로 실행해주세요.');if(!child)throw new Error('호출할 하위 자동화를 찾을 수 없어요.');const mapping=p.workflowInputs?.value;const payload=mapping?items.map(item=>({...item,...resolved(mapping,item,runtime.contextFor(item))})):items;return {flow:clone(child),input:clone(payload)};})};
    await checkpoint();
   }
   const output:Json[]=[],pairs:ItemPair[][]=[];
   for(const [jobIndex,job] of state[key].jobs.entries()){
    runtime.canceled();if(job.execution?.status!=='success'){
     const child=await executeFlow(job.flow,job.input,{...options,onWebhookResponse:undefined,webhookResponseMode:undefined,expressionMode,depth:(options.depth??0)+1,onlyNode:undefined,executionId:job.execution?.id||randomUUID(),resume:job.execution,credential:async id=>{const credential=await options.credential(id);runtime.register(credential.data);return credential;},onProgress:async progress=>{job.execution=progress;await checkpoint();}});job.execution=child;
     if(child.status==='waiting'){execution.status='waiting';execution.pendingNodeId=node.id;execution.pendingExecutionKey=key;execution.resumeAt=child.resumeAt;await checkpoint();return null;}
     if(child.status!=='success')throw new Error(child.error||'하위 자동화를 완료하지 못했어요.');
    }
    const items:Json[]=job.execution.output||[],childRecords=new Map<string,TraceRecord>(job.execution.runs.map((r:NodeRun)=>{const record=traceRecord(r);return [record.key,record];})),last=job.execution.runs.filter((r:NodeRun)=>r.status!=='skipped').at(-1) as NodeRun|undefined,childStart=job.flow.nodes.find((n:FlowNode)=>triggerTypes.has(n.type)&&!n.disabled);
    const refs=last?last.output.flatMap((group:Json[],output:number)=>group.map((_:Json,item:number)=>[{run:last.executionKey||last.nodeId,output,item}])):[];
    for(const [itemIndex,item] of items.entries()){
     let parents:ItemPair[]=[];if(p.mode==='each')parents=[{item:jobIndex}];else if(input.length===1)parents=[{item:0}];else if(childStart){try{parents=lookupItemSources(childStart.name,itemIndex,refs,id=>childRecords.get(id)).map(source=>({item:source.ref.item}));}catch{/* Preserve old results; unknown origins must never be guessed. */}}
     output.push(item);pairs.push(parents);
    }
    if(output.length>maxItems)throw new Error('하위 자동화 결과는 5,000개 항목까지 처리할 수 있어요.');
   }
   if(execution.pendingNodeId===node.id&&(!execution.pendingExecutionKey||execution.pendingExecutionKey===key)){delete execution.pendingNodeId;delete execution.pendingExecutionKey;delete execution.resumeAt;}return [attachItemPairs(output,pairs)];
  };
  const runNode=async(node:FlowNode,input:Json[],key:string,produce?:(input:Json[],refs:ItemSourceRef[][])=>Json[][],replay?:(input:Json[],refs:ItemSourceRef[][])=>void,context:{scope:string;inputByPort:Json[][];inputRefs:ItemSourceRef[][];outputSourceRefs?:ItemSourceRef[][][]}={scope:'root',inputByPort:[input],inputRefs:input.map(()=>[])}):Promise<NodeRun|null>=>{
   runtime.canceled();if(++steps>maxSteps)throw new Error('반복 실행 단계 수 제한에 도달했어요. 종료 조건이나 처리할 항목 수를 확인해주세요.');const t=Date.now(),runIndex=runIndexes.get(node.id)||0;runIndexes.set(node.id,runIndex+1);
   // Keep received data in the record; execution uses the first item of each port.
   // Pins and disabled nodes bypass execution settings, as they do not execute.
   const once=node.executeOnce===true&&!node.disabled&&!(options.mode==='learning'&&Object.hasOwn(flow.pinData||{},node.name));
   const activeByPort=context.inputByPort.map(group=>once?group.slice(0,1):group),activePositions:number[]=[];let offset=0;
   for(const group of context.inputByPort){for(let item=0;item<(once?Math.min(1,group.length):group.length);item++)activePositions.push(offset+item);offset+=group.length;}
   const activeInput=activeByPort.flat(),activeRefs=activePositions.map(index=>context.inputRefs[index]);
   const receivedPair=(index:number):ItemPair=>{if(context.inputByPort.length<2)return {item:index};let start=0;for(const [port,group] of context.inputByPort.entries()){if(index<start+group.length)return {item:index-start,input:port};start+=group.length;}throw new Error('항목 출처 정보가 올바르지 않아요.');};
   runtime.expressionContext=expressionContext(node,activeInput,context.scope,runIndex,activeByPort,activeRefs);
   const visible=visibleRuns(context.scope).sort((a,b)=>String(a.executionScope||'root').length-String(b.executionScope||'root').length),visibleKeys=new Set(visible.map(r=>r.executionKey||r.nodeId)),ancestors=new Map<string,NodeRun>(),queue=activeRefs.flat(),visited=new Set<string>();
   for(let cursor=0;cursor<queue.length;cursor++){
    const ref=queue[cursor],id=refKey(ref);if(visited.has(id))continue;visited.add(id);if(visited.size>MAX_TRACE_REFS)throw new Error('항목 출처 추적 한도를 넘었어요. 흐름을 나눠 필요한 결과를 직접 선택해주세요.');
    const record=completed.get(ref.run);if(!record)continue;ancestors.set(ref.run,record);let parents:ItemSourceRef[]=[];try{parents=sourceParents(ref,traceRecord(record));}catch{/* Older saved records may have no ancestry; references then fail explicitly. */}
    for(const parent of parents){if(queue.length>=MAX_TRACE_EDGES)throw new Error('항목 출처 추적 한도를 넘었어요. 흐름을 나눠 필요한 결과를 직접 선택해주세요.');queue.push(parent);}
   }
   runtime.codeContext={execution:runtime.expressionContext.execution,runIndex,params:node.parameters,inputRefs:activeRefs,nodeRuns:[...[...ancestors.values()].filter(r=>!visibleKeys.has(r.executionKey||r.nodeId)).map(r=>({...traceRecord(r),runIndex:r.runIndex??0,linkOnly:true})),...visible.map(r=>({...traceRecord(r),runIndex:r.runIndex??0,defaultOutputIndex:connectedOutput(r.name,node.name)}))]};
   const outputPairs=(record:NodeRun,applied=true)=>record.output.map(group=>{
    const pairedInput=applied?activeInput:input;
    const explicit=getItemPairs(group);const pairs=explicit??group.map(item=>{
     if(record.pinned||node.type===loopNodeType)return [];
     if(pairedInput.length===1)return [{item:0}];
     const matches=pairedInput.flatMap((original,index)=>original===item?[{item:index}]:[]);return matches;
    });
    // Known per-item nodes keep the order even though their output objects are fresh.
    const local=explicit===undefined&&!record.pinned&&(node.disabled||triggerTypes.has(node.type)||['n8n-nodes-base.wait','n8n-nodes-base.noOp','n8n-nodes-base.set'].includes(node.type))&&group.length===pairedInput.length?group.map((_,item)=>[{item}]):pairs;
    const normalized=applied?local.map(parents=>parents.map(pair=>receivedPair(activePositions[pairInputIndex(pair,activeByPort.length>1?activeByPort.map(items=>items.length):undefined,activeInput.length)]))):local;
    attachItemPairs(group,normalized);for(const parents of normalized)for(const pair of parents)pairInputIndex(pair,record.inputPortCounts,input.length);return normalized;
   });
   const alwaysOutput=(record:NodeRun)=>{
    if(record.status!=='success'||node.alwaysOutputData!==true||record.output[0]?.length)return;
    const parents=context.inputByPort.flatMap((group,input)=>group.map((_,item)=>({item,input})));
    record.output[0]=attachItemPairs([{}],[parents]);record.outputPairedItems??=record.output.map(group=>group.map(()=>[]));record.outputPairedItems[0]=[parents];
    // Structured loop source overrides must also describe the synthetic item.
    if(record.outputSourceRefs)record.outputSourceRefs[0]=[clone(context.inputRefs.flat())];
   };
   let record:NodeRun;
   if(cached.has(key)){record={...clone(cached.get(key)!),input:clone(input),runIndex,executionScope:context.scope,inputRefs:clone(context.inputRefs)};if(context.inputByPort.length>1)record.inputPortCounts=context.inputByPort.map(group=>group.length);else delete record.inputPortCounts;if(record.status==='success'&&!record.pinned&&!node.disabled&&input.length)replay?.(activeInput,activeRefs);if(context.outputSourceRefs)record.outputSourceRefs=clone(context.outputSourceRefs);if(!record.outputPairedItems)record.outputPairedItems=outputPairs(record,!record.continued);alwaysOutput(record);execution.runs.push(record);completed.set(key,record);}
   else {
    record={nodeId:node.id,name:node.name,input:clone(input),output:[[]],status:'success',duration:0,runIndex,executionScope:context.scope,inputRefs:clone(context.inputRefs),...(context.inputByPort.length>1?{inputPortCounts:context.inputByPort.map(group=>group.length)}:{}),...(plan?{executionKey:key}:{})};
    try {
     if(!input.length&&node.type!=='n8n-nodes-base.merge')record.status='skipped';
     else if(node.disabled)record.output=[attachItemPairs(context.inputByPort[0]||[],(context.inputByPort[0]||[]).map((_,item)=>[{item}]))];
     else if(options.mode==='learning'&&Object.prototype.hasOwnProperty.call(flow.pinData||{},node.name)){
      const pinned=flow.pinData[node.name];if(!Array.isArray(pinned))throw new Error('고정 데이터는 JSON 항목 배열이어야 해요.');record.output=[pinned.map((v:any)=>{const item=v?.json!==undefined?v.json:v;if(!item||typeof item!=='object'||Array.isArray(item))throw new Error('고정 데이터의 항목은 JSON 객체여야 해요.');return clone(item);})];(record as any).pinned=true;
     }
     else if(node.type==='n8n-nodes-base.wait'){
      const delay=waitDelay(node,activeInput[0]||{},runtime.contextFor(activeInput[0]||{}));
      if(execution.pendingNodeId===node.id&&(!execution.pendingExecutionKey||execution.pendingExecutionKey===key)&&execution.resumeAt){if(new Date(execution.resumeAt).getTime()>Date.now()){execution.status='waiting';halted=true;await checkpoint();return null;}delete execution.pendingNodeId;delete execution.pendingExecutionKey;delete execution.resumeAt;record.output=[activeInput];}
      else if(delay>0){execution.status='waiting';execution.pendingNodeId=node.id;execution.pendingExecutionKey=key;execution.resumeAt=new Date(Date.now()+delay).toISOString();halted=true;await checkpoint();return null;}else record.output=[activeInput];
     }else {
      const tries=node.retryOnFail?Number(node.maxTries??3):1,delay=Number(node.waitBetweenTries??1000);if(!Number.isInteger(tries)||tries<1||tries>10||!Number.isFinite(delay)||delay<0||delay>60000)throw new Error('재시도 횟수는 1~10회, 간격은 0~60초로 설정해주세요.');
      for(let attempt=1;attempt<=tries;attempt++){record.attempts=attempt;try{const output=produce?produce(activeInput,activeRefs):node.type==='n8n-nodes-base.executeWorkflow'?await subflow(node,activeInput,key):await runtime.node(node,activeInput);if(output===null){halted=true;return null;}record.output=output;if(!produce&&node.type!=='n8n-nodes-base.executeWorkflow')Object.assign(record,clone(runtime.nodeDetails));break;}catch(error){runtime.canceled();if(attempt===tries)throw error;await pause(delay,options.signal);}}
     }
     runtime.canceled();ensureSize(record.output);
     if(node.onError==='continueErrorOutput'){while(record.output.length<outputCount(node))record.output.push([]);record.output.push([]);}
     record.outputPairedItems=outputPairs(record);if(context.outputSourceRefs)record.outputSourceRefs=clone(context.outputSourceRefs);alwaysOutput(record);ensureSize(record.output);
    }catch(error){record.status='error';record.error=runtime.sanitize(error instanceof Error?error.message:'단계를 실행하지 못했어요.');
     if(!options.signal?.aborted&&(node.continueOnFail||node.onError==='continueRegularOutput'||node.onError==='continueErrorOutput')){record.continued=true;const errorItems=attachItemPairs(input.map(item=>({...item,error:record.error})),input.map((_,index)=>[receivedPair(index)]));record.output=node.onError==='continueErrorOutput'?[...Array.from({length:outputCount(node)},()=>[]),errorItems]:[errorItems];record.outputPairedItems=outputPairs(record,false);delete record.outputSourceRefs;}
     else {execution.status=options.signal?.aborted?'canceled':'error';execution.error=record.error;}
    }
    record.duration=Date.now()-t;execution.runs.push(record);completed.set(key,record);await checkpoint();if(record.status==='error'&&!record.continued){halted=true;return record;}
   }
   if(record.status!=='skipped')execution.output=record.output.flat();
   if(node.id===options.onlyNode)halted=true;return record;
  };
  type TracedItems={items:Json[];refs:ItemSourceRef[][]};
  const outputRefs=(record:NodeRun,port:number):ItemSourceRef[][]=>(record.output[port]||[]).map((_,item)=>[{run:record.executionKey||record.nodeId,output:port,item}]);
  const outputRegion=(source:FlowNode,output:number,parent:LoopRegion):LoopRegion=>{
   const names=new Set(parent.ordered.map(node=>node.name)),descendants=new Set<string>(),queue=(flow.connections[source.name]?.main[output]||[]).map(edge=>edge.node);
   for(let cursor=0;cursor<queue.length;cursor++){
    const name=queue[cursor];if(!names.has(name)||descendants.has(name))continue;descendants.add(name);
    for(const [port,edges] of (flow.connections[name]?.main||[]).entries())for(const [index,edge] of edges.entries())if(!plan?.feedbackKeys.has(`${name}:${port}:${index}`))queue.push(edge.node);
   }
   return {...parent,ordered:parent.ordered.filter(node=>descendants.has(node.name))};
  };
  const runRegion=async(region:LoopRegion,seeds:{target:string;input:number;items:Json[];refs:ItemSourceRef[][]}[],scope:string):Promise<TracedItems>=>{
   const values=new Map<string,Map<number,TracedItems>>(),feedback:TracedItems={items:[],refs:[]};
   const deliver=(target:string,input:number,items:Json[],refs:ItemSourceRef[][])=>{if(items.length!==refs.length)throw new Error('항목 출처 수가 입력 항목 수와 달라요.');const slots=values.get(target)||new Map<number,TracedItems>(),existing=slots.get(input)||{items:[],refs:[]};slots.set(input,{items:[...existing.items,...clone(items)],refs:[...existing.refs,...clone(refs)]});ensureSize(byName.get(target)?.type==='n8n-nodes-base.merge'?[slots.get(input)!.items]:[[...slots.values()].flatMap(v=>v.items)]);values.set(target,slots);};
   for(const seed of seeds)deliver(seed.target,seed.input,seed.items,seed.refs);
   for(const node of region.ordered){
    if(node.type==='n8n-nodes-base.stickyNote')continue;
    if(halted)break;runtime.canceled();const slots=values.get(node.name),count=Math.max(ports(node).inputs,...[...(slots?.keys()||[])].map(i=>i+1)),inputByPort:Json[][]=node.id===roots[0].id?[clone(initial)]:Array.from({length:count},(_,i)=>slots?.get(i)?.items||[]),inputRefs:ItemSourceRef[][]=node.id===roots[0].id?initial.map(()=>[]):Array.from({length:count},(_,i)=>slots?.get(i)?.refs||[]).flat(),input=inputByPort.flat(),key=plan?`${scope}/${node.id}`:node.id;
    let output:Json[][],outputRecord:NodeRun|undefined;
    const loop=plan?.loops.get(node.name);
    if(loop&&!node.disabled&&!(options.mode==='learning'&&Object.hasOwn(flow.pinData||{},node.name))){
     let loopInput=input,loopRefs=inputRefs,iteration=0;output=[[],[]];
     while(!halted){
      const callKey=`${key}/call${iteration}`,context:{scope:string;inputByPort:Json[][];inputRefs:ItemSourceRef[][];outputSourceRefs?:ItemSourceRef[][][]}={scope,inputByPort:[loopInput],inputRefs:loopRefs};
      const advance=(activeInput:Json[],activeRefs:ItemSourceRef[][])=>{
       const reset=runtime.resolve(node.parameters.options?.reset??false,activeInput[0]||{});if(typeof reset!=='boolean')throw new Error('반복 초기화에는 true 또는 false를 설정해주세요.');
       const previous=loopContexts.get(node.id),fresh=!previous||reset,lineage=fresh?{remaining:activeInput.map((_,item)=>[{run:callKey,output:0,item,side:'input' as const}]),processed:[] as ItemSourceRef[][]}:clone(loopLineages.get(node.id)!);
       if(!fresh)lineage.processed.push(...clone(activeRefs));
       const result=nextBatch(previous,activeInput,Number(runtime.resolve(node.parameters.batchSize??1,activeInput[0]||{})),reset);
       context.outputSourceRefs=result.context.done?[clone(lineage.processed),[]]:[[],lineage.remaining.splice(0,result.output[1].length)];
       loopLineages.set(node.id,lineage);loopContexts.set(node.id,result.context);execution.loopContexts[node.id]=clone(result.context);return result.output;
      };
      const record=await runNode(node,loopInput,callKey,advance,advance,context);if(!record||halted)break;outputRecord=record;output=record.output;
      if(!output[1]?.length||record.status==='error'||record.pinned)break;
      // alwaysOutputData also activates a Loop's otherwise-empty done output.
      // Execute that acyclic region now, with separate durable activation keys.
      if(node.alwaysOutputData===true&&output[0]?.length){
       const branch=outputRegion(node,0,region),names=new Set(branch.ordered.map(step=>step.name)),branchSeeds:{target:string;input:number;items:Json[];refs:ItemSourceRef[][]}[]=[];
       for(const [target,inputs] of values)if(names.has(target))for(const [input,data] of inputs)branchSeeds.push({target,input,items:data.items,refs:data.refs});
       for(const [index,edge] of (flow.connections[node.name]?.main[0]||[]).entries()){
        if(plan?.feedbackKeys.has(`${node.name}:0:${index}`)){if(region.owner===edge.node){feedback.items.push(...clone(output[0]));feedback.refs.push(...outputRefs(record,0));}}
        else branchSeeds.push({target:edge.node,input:edge.index,items:output[0],refs:outputRefs(record,0)});
       }
       const returned=await runRegion(branch,branchSeeds,`${key}/output0${iteration}`);feedback.items.push(...returned.items);feedback.refs.push(...returned.refs);if(halted)break;
      }
      const refs=outputRefs(record,1),direct=loop.feedback.source===node.name?{items:clone(output[1]),refs}: {items:[],refs:[]};
      const returned=await runRegion(loop.body,loop.entry.filter(edge=>edge.target!==node.name).map(edge=>({target:edge.target,input:edge.input,items:output[1],refs})),`${key}/batch${iteration}`);
      if(halted)break;loopInput=[...direct.items,...returned.items];loopRefs=[...direct.refs,...returned.refs];ensureSize([loopInput]);
      if(!loopInput.length){output=[[],[]];break;}iteration++;
     }
    }else {const record=await runNode(node,input,key,undefined,undefined,{scope,inputByPort,inputRefs});if(!record)break;outputRecord=record;output=record.output;}
    if(halted)break;
    for(const [outputIndex,edges] of (flow.connections[node.name]?.main||[]).entries())for(const [edgeIndex,edge] of edges.entries()){
     const items=output[outputIndex]||[],refs=outputRecord?outputRefs(outputRecord,outputIndex):[];
     if(plan?.feedbackKeys.has(`${node.name}:${outputIndex}:${edgeIndex}`)){if(region.owner===edge.node){feedback.items.push(...clone(items));feedback.refs.push(...clone(refs));}continue;}
     if(loop&&outputIndex===1)continue;
     deliver(edge.node,edge.index,items,refs);
    }
   }
   return feedback;
  };
  await runRegion(plan?.root||{ordered},[],'root');
  if((['waiting','error','canceled'] as Execution['status'][]).includes(execution.status))return runtime.sanitize(execution);
  execution.status='success';delete execution.resumeAt;delete execution.pendingNodeId;delete execution.pendingExecutionKey;await checkpoint();return runtime.sanitize(execution);
 }catch(error){execution.status=options.signal?.aborted?'canceled':'error';execution.error=runtime.sanitize(error instanceof Error?error.message:'워크플로를 실행할 수 없어요.');await checkpoint();return runtime.sanitize(execution);}
}
