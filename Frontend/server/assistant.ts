import {reviewRequirements,type RequirementReview} from './intent-review';
import {randomUUID} from 'node:crypto';
import {catalog,nodeInfo,nodePorts,validateFlow,type Flow,type FlowNode,type Json} from '../lib/workflow';
import type {CredentialPublic,CredentialSecret} from './contracts';
import {compileLoopPlan,loopNodeType} from './loops';
import {binaryNodeTypes,BINARY_KEY} from './binary';
import {readPagination} from './http-pagination';

export type ConversationTurn={role:"user"|"assistant";content:string};
export type AgentRun={role:'analysis'|'design'|'review'|'error';status:'completed'|'needs_input'|'blocked';summary:string;attempt:number};
export type AssistantResult={orchestration?:{engine:"langgraph";status:"waiting_for_answer"|"completed"};agentRuns?:AgentRun[];review?:{approved:boolean;issues:{nodeIds:string[];message:string}[]};workflow?:Flow;summary:string;checklist:string[];questions?:{question:string;choices:string[]}[];requirements?:RequirementReview[]};
export type AssistantOptions={agentContext?:unknown;credential:CredentialSecret;credentials:CredentialPublic[];fetch?:typeof fetch;signal?:AbortSignal;model?:string;conversation?:ConversationTurn[];currentWorkflow?:Flow;conversational?:boolean;previousRequirements?:RequirementReview[]};
export class DraftValidationError extends Error {}
const protectedValue='[보호된 연결 정보]';
const secretKey=/^(?:key|api[-_]?key|private[-_]?key|passphrase|authorization|password|passwd|secret|client[-_]?secret|access[-_]?token|refresh[-_]?token|token|credentials?|credential[-_]?id)$/i;
const forbiddenKey=new Set(['__proto__','prototype','constructor']);
const MAX_RESPONSE_BYTES=2_000_000;
export function modelContext(value:any,depth=0):any {if(depth>20)return '[깊이 제한]';if(Array.isArray(value))return value.map(v=>modelContext(v,depth+1));if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([k])=>!secretKey.test(k)&&!['pinData','hookToken','credentials','credentialId'].includes(k)).map(([k,v])=>[k,modelContext(v,depth+1)]));return value;}
export function mask(value:string,key:string):string{return value.split(key).join(protectedValue);}
function cleanParameters(value:any,key:string):Json {
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('AI가 단계 설정을 올바른 객체로 만들지 못했어요.');
 let entries=0;
 const visit=(v:any,depth:number,path:string):any=>{
  if(depth>32||++entries>20000)throw new Error('AI가 단계 설정을 너무 복잡하게 만들었어요. 더 작은 자동화로 요청해주세요.');
  if(typeof v==='string'){
   // JSON stored inside a parameter still contains untrusted configuration.
   const trimmed=v.trim();if(trimmed.startsWith('{')||trimmed.startsWith('[')){
    let decoded:any;try{decoded=JSON.parse(trimmed);}catch{/* Ordinary prose and expressions remain text. */}
    if(decoded&&typeof decoded==='object')visit(decoded,depth+1,path);
   }
   if(/^https?:\/\//i.test(v)){
    let url:URL|undefined;try{url=new URL(v);}catch{/* Runtime validates ordinary URLs. */}
    if(url&&(url.username||url.password||[...url.searchParams.keys()].some(k=>secretKey.test(k))))throw new Error('AI 초안에 인증 정보가 들어간 주소를 사용할 수 없어요. 연결 계정에서 설정해주세요.');
   }
   return mask(v,key);
  }
  if(Array.isArray(v))return v.map(item=>visit(item,depth+1,path));
  if(v&&typeof v==='object'){
   // HTTP auth pairs are prohibited; ordinary Set name:"token" stays valid.
   if(/(?:headerParameters|bodyParameters|queryParameters)/.test(path)&&typeof v.name==='string'&&secretKey.test(v.name))throw new Error('AI 초안에 인증 정보를 직접 넣을 수 없어요. 연결 계정에서 설정해주세요.');
   return Object.fromEntries(Object.entries(v).map(([name,item])=>{
    if(secretKey.test(name)||forbiddenKey.has(name))throw new Error('AI 초안에 비밀값이나 연결 ID를 직접 넣을 수 없어요. 연결 계정에서 설정해주세요.');
    return [mask(name,key),visit(item,depth+1,path+'.'+name)];
   }));
  }
  return v;
 };
 return visit(value,0,'parameters');
}
function cleanConnections(value:any,key:string):Flow['connections'] {
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('AI가 단계 연결을 올바르게 만들지 못했어요.');
 return Object.fromEntries(Object.entries(value).map(([source,raw]:[string,any])=>{
  if(!raw||typeof raw!=='object'||Array.isArray(raw)||Object.keys(raw).some(c=>c!=='main'))throw new Error('AI가 지원하지 않는 연결을 만들었어요. 다시 만들어주세요.');
  if(!Array.isArray(raw.main))throw new Error('AI가 단계 연결을 올바르게 만들지 못했어요.');
  const main=raw.main.map((edges:any)=>{
   if(!Array.isArray(edges))throw new Error('AI가 단계 연결을 올바르게 만들지 못했어요.');
   return edges.map((edge:any)=>{
    if(!edge||typeof edge!=='object'||Array.isArray(edge))throw new Error('AI가 단계 연결을 올바르게 만들지 못했어요.');
    return {node:typeof edge.node==='string'?mask(edge.node,key):edge.node,type:edge.type??'main',index:edge.index};
   });
  });
  return [mask(source,key),{main}];
 }));
}
function validateFileDraft(type:string,parameters:Json):void {
 if(!binaryNodeTypes.includes(type))return;
 const convert=type===binaryNodeTypes[0];
 const operations=convert?['csv','xlsx','html','rtf','toJson','toText','toBinary']:['csv','xlsx','pdf','fromJson','text','binaryToPropery','binaryToProperty'];
 if(!operations.includes(parameters.operation??'csv'))throw new Error('AI가 현재 지원하지 않는 파일 형식을 선택했어요.');
 const options=parameters.options;
 if(options!==undefined&&(!options||typeof options!=='object'||Array.isArray(options)))throw new Error('AI가 파일 옵션을 올바른 객체로 만들지 못했어요.');
 const encoding=parameters.encoding??options?.encoding??'utf8';
 if(typeof encoding!=='string'||!['utf8','utf-8'].includes(encoding.toLowerCase()))throw new Error('AI 파일 초안은 UTF-8 인코딩으로 만들어야 해요.');
 const property=parameters.binaryPropertyName??'data';
 if(typeof property!=='string'||!property.trim()||property.length>120||forbiddenKey.has(property)||property===BINARY_KEY||/[\u0000-\u001f]/.test(property))throw new Error('AI가 파일 항목 이름을 올바르게 만들지 못했어요.');
}
const webhookAccounts:Record<string,string>={basicAuth:'basic',headerAuth:'header',bearerAuth:'bearer',jwtAuth:'jwt'};
function accountRequirement(node:Pick<FlowNode,'type'|'parameters'>):{required:boolean;providers:string[];service?:string} {
 if(node.type==='n8n-nodes-base.webhook'){const provider=webhookAccounts[node.parameters.authentication];return {required:!!provider,providers:provider?[provider]:[]};}
 if(node.type==='n8n-nodes-base.respondToWebhook')return {required:node.parameters.respondWith==='jwt',providers:node.parameters.respondWith==='jwt'?['jwt']:[]};
 const info=catalog.find(item=>item.n8nType===node.type);return {required:!!info?.credentialRequired,providers:info?.credentialProviders||[],service:info?.credentialService};
}
function validateWebhookDraft(type:string,p:Json):void {
 if(!['n8n-nodes-base.webhook','n8n-nodes-base.respondToWebhook'].includes(type))return;
 if(p.options!==undefined&&(!p.options||typeof p.options!=='object'||Array.isArray(p.options)))throw new Error('AI가 응답 옵션을 올바른 객체로 만들지 못했어요.');
 if(type==='n8n-nodes-base.webhook'){
  if(!['GET','POST','PUT','PATCH','DELETE','HEAD'].includes(p.httpMethod??'GET'))throw new Error('AI가 지원하지 않는 Webhook 요청 방식을 선택했어요.');
  if(!['easyflowToken','none',...Object.keys(webhookAccounts)].includes(p.authentication??'easyflowToken'))throw new Error('AI가 지원하지 않는 Webhook 인증을 선택했어요.');
  if(!['onReceived','lastNode','responseNode'].includes(p.responseMode??'onReceived'))throw new Error('AI가 아직 지원하지 않는 Webhook 응답 방법을 선택했어요.');
  if(p.path!==undefined&&(typeof p.path!=='string'||p.path.length>200))throw new Error('AI가 요청 주소의 이름을 올바르게 만들지 못했어요.');
  if(p.responseMode==='lastNode'&&!['allEntries','firstEntryJson','firstEntryBinary','noData'].includes(p.responseData??'firstEntryJson'))throw new Error('AI가 지원하지 않는 마지막 단계 응답을 선택했어요.');
  for(const option of ['binaryData','rawBody'])if(p.options?.[option]!==undefined&&typeof p.options[option]!=='boolean')throw new Error('AI가 파일 수신 옵션을 올바르게 설정하지 못했어요.');
  const binaryName=p.options?.binaryPropertyName;if(binaryName!==undefined&&(typeof binaryName!=='string'||!binaryName.trim()||binaryName.length>120||forbiddenKey.has(binaryName)||binaryName===BINARY_KEY||/[\u0000-\u001f\u007f]/.test(binaryName)))throw new Error('AI가 받을 파일의 항목 이름을 올바르게 만들지 못했어요.');
 }else {
  if(!['allIncomingItems','firstIncomingItem','json','text','binary','jwt','noData','redirect'].includes(p.respondWith??'firstIncomingItem'))throw new Error('AI가 지원하지 않는 응답 형식을 선택했어요.');
  if(p.respondWith==='binary'&&!['automatically','set'].includes(p.responseDataSource??'automatically'))throw new Error('AI가 파일 응답의 선택 방법을 올바르게 만들지 못했어요.');
  if(p.enableResponseOutput!==undefined&&typeof p.enableResponseOutput!=='boolean')throw new Error('AI가 응답 출력 경로를 올바르게 설정하지 못했어요.');
 }
 const code=type==='n8n-nodes-base.webhook'?p.responseCode??p.options?.responseCode?.values?.customCode??p.options?.responseCode?.values?.responseCode:p.options?.responseCode;
 if(code!==undefined&&!(typeof code==='string'&&code.startsWith('='))&&(typeof code!=='number'||!Number.isInteger(code)||code<200||code>599))throw new Error('AI 응답 번호는 200~599 사이의 정수로 설정해주세요. 중간 응답은 아직 지원하지 않아요.');
}
function draftOnly(value:any,credentials:CredentialPublic[],key:string):Flow {
 if(!value||typeof value!=='object'||Array.isArray(value)||!Array.isArray(value.nodes)||!value.nodes.length||value.nodes.length>40)throw new Error('AI가 만든 단계 수를 확인해주세요. 최대 40개까지 만들 수 있어요.');
 // Rebuild root/nodes before validation: generated execution policies are untrusted.
 const nodes=value.nodes.map((node:any,index:number)=>{
  if(!node||typeof node!=='object'||Array.isArray(node)||typeof node.name!=='string'||!node.name.trim()||node.name.length>120||forbiddenKey.has(node.name))throw new Error('AI가 단계 이름을 올바르게 만들지 못했어요.');
  const info=nodeInfo(node);if(!info)throw new Error('AI가 아직 지원하지 않는 작업을 만들었어요: '+mask(node.name,key));
  const parameters=cleanParameters(node.parameters,key);validateFileDraft(node.type,parameters);validateWebhookDraft(node.type,parameters);
  const requirement=accountRequirement({type:node.type,parameters});
  const compatible=(c:CredentialPublic)=>requirement.required&&requirement.providers.includes(c.provider)&&(!requirement.service||!c.service||c.service===requirement.service);
  const requested=credentials.find(c=>c.id===node.credentialId&&compatible(c)),matches=credentials.filter(compatible);
  const credential=requested||(matches.length===1?matches[0]:undefined);
  if(node.type==='n8n-nodes-base.httpRequest')readPagination(parameters);
  if(node.type==='n8n-nodes-base.stickyNote'){const color=Number(parameters.color??1),width=Number(parameters.width??240),height=Number(parameters.height??160);if(typeof parameters.content!=='string'||parameters.content.length>20000||!Number.isInteger(color)||color<1||color>7||!Number.isFinite(width)||width<240||width>3000||!Number.isFinite(height)||height<160||height>3000)throw new Error('AI가 메모 내용 또는 크기를 올바르게 만들지 못했어요.');}
  return {id:typeof node.id==='string'?mask(node.id,key):node.id,name:mask(node.name,key),type:node.type,typeVersion:info.typeVersion,position:[120+index*230,320] as [number,number],parameters,...(credential?{credentialId:credential.id}:{})};
 });
 if(typeof value.name!=='string'||!value.name.trim()||value.name.length>200)throw new Error('AI가 자동화 이름을 올바르게 만들지 못했어요.');
 const flow=validateFlow({id:randomUUID(),name:mask(value.name,key),nodes,connections:cleanConnections(value.connections,key),active:false,settings:{executionOrder:'v1',timezone:'Asia/Seoul'}});
 const starts=flow.nodes.filter(node=>nodeInfo(node)?.category==='시작');
 if(starts.length!==1)throw new Error('AI가 시작 단계를 하나로 구성하지 못했어요. 요청을 조금 더 구체적으로 적어주세요.');
 const byName=new Map(flow.nodes.map(n=>[n.name,n])),inDegree=new Map(flow.nodes.map(n=>[n.name,0]));
 for(const [source,channels] of Object.entries(flow.connections)){
  const node=byName.get(source)!;
  if(channels.main.length>nodePorts(node).outputs.length)throw new Error('AI가 출력 경로를 잘못 연결했어요. 다시 만들어주세요.');
  for(const edges of channels.main)for(const edge of edges){
   const target=byName.get(edge.node)!;
   if(edge.type!=='main'||edge.index>=nodePorts(target).inputs)throw new Error('AI가 입력 경로를 잘못 연결했어요. 다시 만들어주세요.');
   inDegree.set(edge.node,inDegree.get(edge.node)!+1);
  }
 }
 if(inDegree.get(starts[0].name))throw new Error('AI가 시작 단계를 잘못 연결했어요. 다시 만들어주세요.');
 if(flow.nodes.some(node=>node.type===loopNodeType)){
  try{compileLoopPlan(flow);}catch(error){throw new Error('AI가 반복 연결을 올바르게 만들지 못했어요. '+mask(error instanceof Error?error.message:'반복 경로를 확인해주세요.',key));}
 }else{
  const remaining=new Map(inDegree),ready=flow.nodes.filter(n=>!remaining.get(n.name));let count=0;
  while(ready.length){const n=ready.shift()!;count++;for(const edges of flow.connections[n.name]?.main||[])for(const edge of edges){remaining.set(edge.node,remaining.get(edge.node)!-1);if(!remaining.get(edge.node))ready.push(byName.get(edge.node)!);}}
  if(count!==flow.nodes.length)throw new Error('AI가 반복 연결을 만들었어요. 반복하려면 Loop Over Items 단계의 정해진 반복 경로를 사용해주세요.');
 }
 const reachable=new Set<string>(),queue=[starts[0].name];
 while(queue.length){const name=queue.shift()!;if(reachable.has(name))continue;reachable.add(name);for(const edges of flow.connections[name]?.main||[])for(const edge of edges)queue.push(edge.node);}
 if(reachable.size!==flow.nodes.filter(node=>node.type!=='n8n-nodes-base.stickyNote').length)throw new Error('AI가 시작 단계와 연결되지 않은 단계를 만들었어요. 다시 만들어주세요.');
 return flow;
}
export async function boundedResponse(response:Response):Promise<string> {
 if(Number(response.headers.get('content-length'))>MAX_RESPONSE_BYTES){void response.body?.cancel().catch(()=>{});throw new Error('AI 응답이 2MB를 넘었어요. 더 작은 자동화로 요청해주세요.');}
 if(!response.body)return '';
 const reader=response.body.getReader(),decoder=new TextDecoder(),parts:string[]=[];let bytes=0;
 try{while(true){const {done,value}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>MAX_RESPONSE_BYTES){await reader.cancel();throw new Error('AI 응답이 2MB를 넘었어요. 더 작은 자동화로 요청해주세요.');}parts.push(decoder.decode(value,{stream:true}));}parts.push(decoder.decode());return parts.join('');}
 finally{reader.releaseLock();}
}
async function generateAssistant(prompt:string,options:AssistantOptions):Promise<AssistantResult> {
 if(typeof prompt!=='string'||prompt.trim().length<5||prompt.length>4000)throw new Error('하고 싶은 일을 5자에서 4,000자 사이로 적어주세요.');
 const conversation=options.conversation||[];
 if(conversation.length>24||conversation.some(t=>!t||!['user','assistant'].includes(t.role)||typeof t.content!=='string'||t.content.length>4000))throw new Error('대화 길이 또는 형식을 확인해주세요.');
 const model=options.model??'gpt-4o-mini';if(typeof model!=='string'||!model.trim()||model.length>120)throw new Error('모델 이름은 1자에서 120자 사이로 입력해주세요.');
 const {credential}=options;if(credential.provider!=='openai')throw new Error('AI로 만들기에는 OpenAI 연결 계정을 선택해주세요.');
 const key=credential.data.apiKey||credential.data.token;if(typeof key!=='string'||!key.trim()||/[\r\n]/.test(key))throw new Error('OpenAI API 키를 연결 계정에 등록해주세요.');
 const available=catalog.map(c=>({name:c.name,type:c.n8nType,typeVersion:c.typeVersion,description:c.description,defaults:c.parameters,fields:c.fields,inputs:c.inputs,outputs:c.outputs,credentialProviders:c.credentialProviders,credentialService:c.credentialService,...(c.n8nType==='n8n-nodes-base.webhook'?{credentialConditions:{authentication:webhookAccounts}}:c.n8nType==='n8n-nodes-base.respondToWebhook'?{credentialConditions:{respondWith:{jwt:'jwt'}}}:{})}));
 let instructions=[
  '당신은 Easyflow라는 독립 자동화 프로그램의 설계 도우미입니다. 사용자의 요청을 실행 가능한 워크플로 초안으로 만들고 한국어로 설명합니다. JSON 객체만 반환합니다.',
  '형식: {"workflow":{"name":"이름","nodes":[{"id":"고유문자열","name":"고유한단계이름","type":"지원종류","typeVersion":1,"position":[0,0],"parameters":{}}],"connections":{"시작단계이름":{"main":[[{"node":"다음단계이름","type":"main","index":0}]]}},"settings":{}},"summary":"작동 방식을 2문장으로 설명","checklist":["사용자가 실제 실행 전에 입력할 값"]}.',
  'JSON은 저장하거나 실행되지 않는 초안입니다. 지원 단계만 사용하고 기본 매개변수 구조를 그대로 따르세요. 시작 단계는 하나만 사용하세요. 조건은 맞아요 출력0, 아니에요 출력1입니다. connections[source].main 배열의 위치가 출발 출력 번호이고 edge.index는 도착 입력 번호입니다. IF의 거짓 경로는 main[1]에 놓고 도착 단계가 단일 입력이면 edge.index는 언제나 0입니다. 시작에 제공된 항목을 그대로 처리할 때는 Set으로 빈 배열이나 예시 자료를 덮어쓰지 말고 시작 단계에서 처리 단계로 바로 연결하세요. Switch는 rules.values마다 출력0부터, fallbackOutput이 extra일 때만 마지막 나머지 경로를 만듭니다.',
  `반복은 Loop Over Items(${loopNodeType}) 버전 3으로 구성합니다. done 완료는 출력0, loop 반복 묶음은 출력1입니다. 시작 → 반복 입력(index:0), 반복 출력1 → 반복 본문, 본문의 끝 → 같은 반복의 입력(index:0)으로 돌아오는 feedback 연결 하나, 반복 출력0 → 완료 후 단계로 연결하세요. 본문에 반복 밖에서 직접 들어가거나 서로 다른 반복 본문을 공유하지 마세요. 여러 복귀 경로는 Merge로 하나로 모으세요. 반복 단계의 정해진 feedback 외에는 임의 순환 연결을 만들지 마세요. batchSize는 1~5,000 정수이며 기본값은 1입니다. options.reset은 false를 기본값으로 유지하고, 무조건 reset:true로 매 반복을 초기화하지 마세요.`,
  '파일 단계는 실제 파일 결과를 만들거나 전달받은 파일 내용을 읽습니다. CSV, JSON, UTF-8 텍스트와 base64↔파일 변환, XLSX 엑셀 읽기·쓰기, PDF 텍스트 추출, HTML·RTF 표 파일 생성을 지원합니다. CSV·JSON·텍스트 내용은 UTF-8만 사용합니다. PDF 생성·스캔 문서 OCR·암호화 문서·다른 인코딩은 아직 지원하지 않습니다. 엑셀 수식은 실행하지 않고 저장된 계산 결과만 읽습니다. 한 단계 파일 총 크기는 8MB 이하, 데이터 항목은 5,000개 이하이며 CSV는 한 행에 1,000개 열까지 지원합니다. 파일 시스템 경로를 읽거나 쓰지 않습니다. 만든 파일은 실행 결과에서 다운로드합니다. 입력 파일을 읽는 extractFromFile에는 실제 파일 업로드 또는 앞 파일 생성·다운로드 단계가 필요합니다. binaryPropertyName은 기본 data이며 앞 단계의 파일 항목 이름과 일치해야 합니다. 아직 업로드되지 않은 파일을 읽었다고 설명하지 말고 checklist에 실제 입력 파일 준비·업로드와 UTF-8 형식을 안내하세요.',
  'HTTP 페이지 순회는 options.pagination.pagination에 설정합니다. paginationMode는 off, responseContainsNextURL, updateAParameterInEachRequest 중 하나입니다. 다음 주소는 nextURL: ={{ $response.body.next }}처럼 참조하세요. 이 경우 paginationCompleteWhen:other,completeExpression: ={{ !$response.body.next }}처럼 다음 주소가 없으면 참인 종료 조건도 꼭 설정하세요. 번호 변경은 parameters:{parameters:[{type:qs,name:page,value:={{ $pageCount + 1 }}}]} 구조입니다. 페이지 컨텍스트에서는 $json, 0부터 시작하는 $pageCount, 이전 응답 $response.body/headers/statusCode, 이전 요청 $request를 쓸 수 있습니다. paginationCompleteWhen은 responseIsEmpty, receiveSpecificStatusCodes(별도 statusCodesWhenComplete 문자열), other(별도 completeExpression 불리언 표현식)입니다. 기본 100페이지, limitPagesFetched:true,maxRequests:1~1000,requestInterval:0~60000입니다. 완결 전에 제한을 넘겨 잘린 결과로 성공했다고 설명하지 마세요.',
  'Webhook API는 n8n-nodes-base.webhook 버전 2.1로 시작합니다. httpMethod는 GET/POST/PUT/PATCH/DELETE/HEAD 중 하나이며 기본 GET입니다. path는 요청 주소 이름입니다. 요청 데이터는 $json.body, $json.query, $json.headers, $json.params에서 사용합니다. JSON(application/json,+json), 일반 글자(text/plain), application/x-www-form-urlencoded, XML(/xml,+xml)을 해석해 body에 담습니다. 해석하는 본문의 gzip/deflate 압축도 처리합니다. 같은 이름이 반복된 쿼리나 URLencoded 필드는 목록으로 보존합니다. multipart/form-data는 추가 글자 필드를 body에, 실제 업로드 파일을 binary에 담습니다. 파일 항목 이름은 보낸 field에서 []를 제거한 이름이며 같은 이름의 파일이 여러 개면 data0,data1처럼 0부터 번호를 붙입니다. options.binaryPropertyName을 지정하면 multipart의 모든 파일을 이 접두어0,접두어1로 받습니다. 자동 해석 MIME 이외(text/csv,PDF,HTML,application/octet-stream 등)의 본문은 기본 data 파일 하나로 받고 options.binaryPropertyName으로 이름을 바꿉니다. options.binaryData:true는 어떤 MIME이든 multipart·압축 원문까지 해석하지 않고 단일 파일로 받습니다. options.rawBody:true는 일반 해석 결과와 원문 파일 binary.data를 함께 유지하며 이 원문 이름은 binaryPropertyName과 무관합니다. multipart에서는 rawBody를 적용하지 않고 binaryData가 우선합니다. 두 옵션은 boolean true/false로 설정합니다. 파일은 개별·합계 8MiB, 해석할 일반 본문은 4MB(4,000,000바이트) 제한입니다. Webhook → extractFromFile로 CSV 등을 읽을 때 앞 수신 파일 이름을 binaryPropertyName과 맞추고 UTF-8 실제 파일을 요청으로 보내도록 안내하세요. 파일을 받은 척하지 말고 메뉴의 Webhook 주소에서 예시 요청 보내기로 파일을 선택하거나 외부 앱에서 실제 요청을 보내도록 checklist에 안내하세요. responseMode onReceived는 접수만 즉시 알리고, lastNode는 마지막 실행 단계의 완성된 결과를 돌려주며, responseNode는 n8n-nodes-base.respondToWebhook 단계에서 답합니다. lastNode의 responseData는 firstEntryJson(기본),allEntries,firstEntryBinary,noData 중 하나입니다. 첫 JSON의 특정 값만 돌려주려면 options.responsePropertyName, 파일 응답은 responseBinaryPropertyName:data를 설정합니다. 최종 응답 번호는 200~599만 지원하며 100대 중간 응답은 아직 지원하지 않습니다. Webhook 응답 번호의 기본값은 200이고 버전2 형식은 options.responseCode:{values:{responseCode:200}}입니다. 헤더는 options.responseHeaders:{entries:[{name:Content-Type,value:application/json}]}입니다. onReceived의 접수 메시지는 options.responseData, 내용 없는 접수는 options.noResponseBody:true입니다. 다른 앱에 결과를 돌려달라는 요청에는 접수 메시지만 보내는 설정을 사용하지 마세요.',
  'Respond to Webhook은 버전1.5입니다. respondWith는 allIncomingItems,firstIncomingItem,json,text,binary,jwt,noData,redirect입니다. JSON/글자 내용은 responseBody이며 첫 입력 항목의 값을 기준으로 평가합니다. JSON은 객체 또는 JSON 문자열/표현식입니다. 파일은 responseDataSource:automatically(첫 파일 자동 선택) 또는 set(별도 inputFieldName:data)로 고릅니다. 입력 결과를 감쌀 이름은 options.responseKey, 응답 번호는 options.responseCode(기본200,redirect기본307), 이동 주소는 redirectURL입니다. JWT 내용은 payload JSON 객체이며 키·알고리즘은 연결 계정에서 선택합니다. parameters.enableResponseOutput:true이면 출력0은 원래 입력, 출력1은 {response:{body,headers,statusCode}}입니다. 다른 앱에 응답하려면 앞 Webhook의 responseMode를 responseNode로 선택하고 응답 단계를 연결하세요. 한 요청에 응답은 한 번만 보내므로 이후 응답 단계는 원래 입력만 전달합니다. 스트리밍 응답은 아직 지원하지 않으므로 responseMode:streaming이나 enableStreaming을 사용하지 마세요.',
  'Webhook authentication은 easyflowToken(우리 프로그램의 요청 주소 비밀 토큰, 기본),none,basicAuth,headerAuth,bearerAuth,jwtAuth입니다. basicAuth는 basic, headerAuth는 header, bearerAuth는 bearer, jwtAuth는 jwt 연결 계정이 필요합니다. Respond respondWith:jwt도 jwt 연결 계정이 필요하며 다른 응답에는 계정이 필요 없습니다. 인증 정보는 parameters나 헤더 값에 직접 만들지 말고 사용자가 연결 계정에서 선택하도록 안내하세요. 같은 종류의 저장된 계정이 하나면 프로그램이 연결하고, 여러 계정이면 사용자가 단계 설정에서 고릅니다. 인증 없이 받기는 사용자가 명시적으로 요청할 때 선택하고, 일반 Webhook은 기본 easyflowToken을 유지합니다. 초안은 자동 실행을 켜지 않으며, 저장 후 사용자가 메뉴에서 활성화하고 요청 주소를 연결할 앱에 입력해야 합니다.',
  '메모는 n8n-nodes-base.stickyNote 타입으로 만들며 입력·출력이나 연결이 없는 주석입니다. 실행 경로를 메모에 연결하지 마세요. content는 20,000자 이하의 Markdown, color는 1~7, width는 240~3,000, height는 160~3,000입니다.',
  '데이터 표현식은 ={{ $json.항목이름 }} 또는 글 속 {{ $json.항목이름 }}을 사용합니다. 중첩 경로와 이름에 공백이 있는 키는 $json["항목 이름"]처럼 대괄호로 읽습니다. 이미 실행한 이전 단계는 $("단계 이름").first(출력번호,회차번호).json.항목, .last(), .all()로 명시적으로 읽습니다. 모든 현재 입력은 $input.all(), 첫 항목은 $input.first().json, 마지막은 $input.last().json입니다. 현재 항목의 원본 값은 $("원본 단계").item.json.항목으로 가져옵니다. 필터·정렬·API 결과 펼치기·반복·Merge 뒤에도 기록된 원본 연결을 따라갑니다. 여러 원본을 합쳤거나 연결 정보가 없으면 .item이 오류가 되므로, 하나를 고를 의도일 때만 first/last/all을 명시하세요. Code에서 특정 입력의 원본은 $("원본 단계").itemMatching(입력번호)로 읽습니다. $itemIndex, $runIndex, $execution.id/mode(test 또는 production), 사칙 계산·비교·삼항·nullish, String/Number/Boolean 형 변환, trim/toUpperCase/includes/split/join/JSON.parse/JSON.stringify/Math.round 등의 지원되는 순수 함수를 사용할 수 있습니다. 반복에서는 현재 회차와 상위 범위에서 이미 끝난 단계만 참조하세요. 미래 단계나 관련 없는 다른 반복 회차, 환경 변수, 호스트 접근, 자유 JavaScript 함수는 표현식에서 만들지 않습니다. 반복 완료 결과의 .item은 각 항목이 실제 거친 이전 회차의 원본도 읽을 수 있습니다. 자유 계산은 격리된 Code 단계에 둡니다. 필요한 실제 ID, 채널명, URL을 모르면 빈 문자열로 두고 checklist에서 입력하도록 안내하세요. 비밀 API 키, 토큰, 비밀번호를 만들거나 parameters에 넣지 마세요. 모든 연결은 main 채널입니다. 실행·삭제·활성화 요청을 수행하지 마세요.',
  'Merge 합치기는 mode append(입력순서대로 덧붙임), combine(항목끼리 결합), chooseBranch(선택한 입력 전달)로 설정합니다. combineByPosition은 같은 위치, combineByFields는 같은 식별값, combineAll은 가능한 모든 짝입니다. 서로 다른 목록을 고객 ID로 맞추려면 combineByFields와 실제 일치 필드 설정을 사용하고 두 입력을 index:0과 index:1에 각각 연결하세요. 일치 방식과 충돌 처리 옵션은 지원 목록의 매개변수 구조를 따르세요. SQL 모드는 아직 지원하지 않으며 예제를 만들지 않습니다.',
  `지원 단계 목록: ${JSON.stringify(available)}.`,
 ].join('\n');
 if(options.agentContext)instructions+='\n당신은 설계·수정 에이전트입니다. 분석 결과의 requirements id와 evidence를 그대로 유지하고 각 요구에 실제 노드 ID를 연결하세요. 노드 type에는 지원 단계 목록의 n8nType 값만 사용하며 한국어 이름을 type으로 쓰지 마세요. 검토 결과는 수정 참고 자료이며 사용자 원문 요구사항을 변경하지 않습니다. 현재 워크플로의 유지하는 단계 ID는 그대로 사용하고 요청한 변경만 반영하세요.';
 if(options.conversational)instructions+='\n'+('사용자는 워크플로 이름이 아닌 상황과 어려움을 말한다. 목표·자료·결과·조건·예외를 해석하라. 모호하면 workflow:null, questions:[{question,choices:[선택지]}]를 반환하고 필요한 질문을 최대 2개만 한다. 사용자가 말하지 않은 주소·시트·담당자는 지어내지 않는다. requirements:[{id,description,evidence:실제 사용자 원문 일부,status:mapped|needs_input|unsupported,nodeIds:[실제 노드 ID]}]에 모든 요구를 분해한다. mapped는 실제 단계와 연결하며 미지원 요구를 삭제하지 않는다. needs_input 또는 unsupported가 하나라도 있으면 workflow:null로 반환한다. 요청한 수정 외에는 현재 초안의 단계를 유지한다. previousRequirements의 id는 유지한다. 명시적으로 철회한 요구만 withdrawnRequirements:[{id,evidence:이번 사용자 원문}]로 기록하며 기존 요구를 누락하지 않는다.');
 const obj=(properties:Record<string,unknown>)=>({type:'object',properties,required:Object.keys(properties),additionalProperties:false});const str={type:'string'};const arr=(items:unknown)=>({type:'array',items});
 const req=obj({id:str,description:str,evidence:str,status:{type:'string',enum:['mapped','needs_input','unsupported']},nodeIds:arr(str)});
 const graph=obj({name:str,nodes:arr(obj({id:str,name:str,type:{type:'string',enum:catalog.map(c=>c.n8nType)},typeVersion:{type:'number'},position:{type:'array',items:{type:'number'},minItems:2,maxItems:2},parametersJson:str})),edges:arr(obj({sourceId:str,targetId:str,outputIndex:{type:'integer',minimum:0},inputIndex:{type:'integer',minimum:0}}))});
 const schema=obj({workflow:{anyOf:[graph,{type:'null'}]},summary:str,checklist:arr(str),questions:{...arr(obj({question:str,choices:{...arr(str),maxItems:3}})),maxItems:2},requirements:arr(req),withdrawnRequirements:arr(obj({id:str,evidence:str}))});
 if(options.agentContext){
  instructions=instructions.replace(/형식: .*?\n/,'');
  instructions+='\n최종 출력은 응답 스키마를 따릅니다. workflow={name,nodes,edges}이며 nodes 각 parametersJson에는 단계 설정 객체를 JSON 문자열로 직렬화합니다. 빈 설정은 "{}"입니다. edges의 sourceId와 targetId는 노드 id입니다. outputIndex는 출발 출력 번호, inputIndex는 도착 입력 번호이며 보통 0입니다. connections와 settings는 직접 생성하지 않습니다. 분석 requirements의 id, evidence를 그대로 유지하고 status와 nodeIds로 반영을 기록합니다. mapped인 모든 요구는 nodeIds가 최소 하나 있어야 합니다. 저장·전송하지 않는 제약은 결과를 화면으로 전달하는 마지막 단계 ID에 연결하세요. 조건 설정은 지원 목록의 defaults 구조를 따르고 입력 항목 이름은 사용자가 말한 이름을 그대로 사용하세요. 이미 제공된 시작 데이터는 덮어쓰지 않습니다. 화면 업로드는 수동 시작입니다. 사용자 원문에 없는 분류 기준이나 담당자를 만들지 말고 필수 정보가 없으면 workflow:null 및 추가 질문으로 반환합니다.';
 }
 let response:Response;
 try{response=await (options.fetch||fetch)('https://api.openai.com/v1/responses',{method:'POST',headers:{Authorization:`Bearer ${key}`,'Content-Type':'application/json'},signal:AbortSignal.any([AbortSignal.timeout(60000),...(options.signal?[options.signal]:[])]),body:JSON.stringify({model:model.trim(),...(model.startsWith('gpt-5')?{reasoning:{effort:'low'}}:{}),instructions,input:mask(options.conversational?JSON.stringify({responseFormat:'JSON',conversation,currentWorkflow:modelContext(options.currentWorkflow??null),previousRequirements:options.previousRequirements||[],agentContext:options.agentContext,message:prompt.trim()}):('JSON request: '+prompt.trim()),key),text:{format:options.agentContext?{type:'json_schema',name:'workflow_design',strict:true,schema}:{type:'json_object'}},store:false,max_output_tokens:8000})});}
 catch{throw new Error(options.signal?.aborted?'AI 초안 요청을 중지했어요.':'AI 서비스에 연결하지 못했어요. 인터넷 연결과 모델 이름을 확인해주세요.');}
 if(!response.ok){void response.body?.cancel().catch(()=>{});throw new Error(response.status===429?'AI 요청 한도에 도달했어요. 잠시 후 다시 만들어주세요.':'AI 초안을 만들지 못했어요. OpenAI 계정과 모델 이름을 확인해주세요.');}
 let raw:string;try{raw=await boundedResponse(response);}catch(error){if(error instanceof Error&&error.message.startsWith('AI 응답이 2MB'))throw error;throw new Error('AI 서비스 응답을 끝까지 받지 못했어요. 다시 시도해주세요.');}
 let data:Json;try{data=JSON.parse(raw);}catch{throw new Error('AI 서비스 응답을 확인하지 못했어요. 다시 시도해주세요.');}
 if(!data||typeof data!=='object'||Array.isArray(data)||data.status!=='completed'||data.error)throw new Error('AI가 초안을 끝까지 만들지 못했어요. 요청을 나눠서 다시 만들어주세요.');
 if(!Array.isArray(data.output))throw new Error('AI 서비스 응답을 확인하지 못했어요. 다시 시도해주세요.');
 const content=data.output.filter((v:any)=>v&&v.type==='message').flatMap((v:any)=>Array.isArray(v.content)?v.content:[]);
 if(content.some((v:any)=>v?.type==='refusal'))throw new Error('AI가 이 요청의 초안을 만들 수 없다고 응답했어요. 요청 내용을 바꿔주세요.');
 const texts=content.filter((v:any)=>v?.type==='output_text').map((v:any)=>v.text);
 if(!texts.length||texts.some((v:any)=>typeof v!=='string'))throw new Error('AI가 올바른 초안을 만들지 못했어요. 다시 만들어주세요.');
 try {
 let result:Json;try{result=JSON.parse(texts.join(''));}catch{throw new Error('AI가 올바른 초안을 만들지 못했어요. 다시 만들어주세요.');}
 if(!result||typeof result!=='object'||Array.isArray(result))throw new Error('AI가 올바른 초안을 만들지 못했어요. 다시 만들어주세요.');
if(options.agentContext&&result.workflow){try{
 const w=result.workflow;
 if(Array.isArray(w.edges)){
  const names=new Map(w.nodes.map((n:any)=>[n.id,n.name]));const connections:any={};
  for(const n of w.nodes){n.parameters=JSON.parse(n.parametersJson);delete n.parametersJson;}
  for(const e of w.edges){const source=names.get(e.sourceId),target=names.get(e.targetId);if(!source||!target)throw new Error('알 수 없는 단계');const c=connections[source as string]??={main:[]};while(c.main.length<=e.outputIndex)c.main.push([]);c.main[e.outputIndex].push({node:target,type:'main',index:e.inputIndex});}
  w.connections=connections;delete w.edges;
 }else{if(typeof result.workflow==='string')result.workflow=JSON.parse(result.workflow);if(typeof result.workflow.connections==='string')result.workflow.connections=JSON.parse(result.workflow.connections);for(const node of result.workflow.nodes||[])if(typeof node.parameters==='string')node.parameters=JSON.parse(node.parameters);}
}catch{throw new Error('설계 에이전트의 워크플로 JSON이 잘못됐어요.');}}
if(options.conversational){
 const currentIds=new Set(Array.isArray(result.requirements)?result.requirements.map((r:any)=>r.id):[]);
 const withdrawn=Array.isArray(result.withdrawnRequirements)?result.withdrawnRequirements:[];
 for(const prior of options.previousRequirements||[]){if(!currentIds.has(prior.id)&&!withdrawn.some((r:any)=>r?.id===prior.id&&typeof r.evidence==='string'&&r.evidence.trim()&&prompt.includes(r.evidence)))throw new Error('이전 요구사항이 누락됐어요. 철회하지 않은 요구를 유지해 다시 질문해주세요.');}
}
if(options.conversational&&result.workflow===null){
  const requirements=reviewRequirements(result.requirements,[...conversation.filter(t=>t.role==='user').map(t=>t.content),prompt]);
  if(!Array.isArray(result.questions)||result.questions.length>2||result.questions.some((q:any)=>!q||typeof q.question!=='string'||!q.question.trim()||q.question.length>500||!Array.isArray(q.choices)||q.choices.length>3||q.choices.some((v:any)=>typeof v!=='string'||v.length>200)))throw new Error('확인 질문 형식이 올바르지 않아요.');
  if(!requirements.some(r=>r.status!=='mapped'))throw new Error('초안을 만들지 못한 이유가 없어요.');
  return {summary:mask(String(result.summary||'추가 정보를 확인해주세요.'),key).slice(0,1500),checklist:[],requirements,questions:result.questions.map((q:any)=>({question:mask(q.question,key),choices:q.choices.map((v:string)=>mask(v,key))}))};
 }
 const workflow=draftOnly(result.workflow,options.credentials,key);
 const requirements=options.conversational?reviewRequirements(result.requirements,[...conversation.filter(t=>t.role==='user').map(t=>t.content),prompt],workflow):undefined;
 if(requirements?.some(r=>r.status!=='mapped'))throw new Error('미확인·미지원 요구가 남아 있어 초안을 적용할 수 없어요. 먼저 대화로 확인해주세요.');
 if(requirements)workflow.intentReview={requirements,reviewed:false,note:'노드 연결·원문 일치 검사. 의미적 완전성은 사용자 확인 필요'};
 const checklist:string[]=Array.isArray(result.checklist)?result.checklist.filter((v:any)=>typeof v==='string').slice(0,12).map((v:string)=>mask(v,key).slice(0,500)):[];
 for(const node of workflow.nodes){
  const requirement=accountRequirement(node);if(requirement.required&&!node.credentialId)checklist.push(`${node.name}: ${requirement.providers[0]==='jwt'?'JWT ':''}연결 계정을 선택해주세요.`);
  if(node.type==='n8n-nodes-base.webhook'){checklist.push(`${node.name}: 초안을 저장한 뒤 메뉴에서 자동 실행을 켜고 요청 주소를 연결할 앱에 입력해주세요. 요청 방식은 ${node.parameters.httpMethod||'GET'}이에요.`);if(workflow.nodes.some(current=>current.type===binaryNodeTypes[1])||node.parameters.options?.binaryData===true||node.parameters.options?.rawBody===true)checklist.push(`${node.name}: Webhook 주소의 ‘예시 요청 보내기’에서 실제 파일을 선택하거나 연결할 앱에서 파일 요청을 보내주세요. 원시 파일은 ‘${node.parameters.options?.binaryData!==true&&node.parameters.options?.rawBody===true?'data':node.parameters.options?.binaryPropertyName||'data'}’로 받으며 파일 개별·합계는 8MB 이하예요.${node.parameters.options?.binaryData===true?' 요청 전체를 원시 파일로 받아요.':''}${node.parameters.options?.rawBody===true?' 일반 해석 결과의 원문 파일은 data에 담아요.':''}`);}
  if(node.type==='n8n-nodes-base.respondToWebhook'){
   const hook=workflow.nodes.find(current=>current.type==='n8n-nodes-base.webhook');
   if(!hook||hook.parameters.responseMode!=='responseNode')checklist.push(`${node.name}: 다른 앱에 응답하려면 ‘요청을 받아 시작’의 응답 방법을 ‘응답 단계에서 정하기’로 선택해주세요. 실제 요청을 받았을 때만 응답을 보내요.`);
   if(node.parameters.respondWith==='binary')checklist.push(`${node.name}: 첫 번째 입력 항목에 실제 파일을 전달해주세요.${node.parameters.responseDataSource==='set'?` 파일 항목 이름은 ‘${node.parameters.inputFieldName||'data'}’이에요.`:' 첫 번째 파일을 자동으로 선택해요.'}`);
  }
  if(node.type===binaryNodeTypes[1]){
   const property=node.parameters.binaryPropertyName??'data';
   const textFormat=['csv','fromJson','text'].includes(node.parameters.operation);
   const hasWebhook=workflow.nodes.some(current=>current.type==='n8n-nodes-base.webhook');
   checklist.push(`${node.name}: ${hasWebhook?'Webhook 주소에서 실제 파일 요청을 보내거나 예시 요청의 파일 선택을 사용해주세요.':'실제 입력 파일을 업로드하거나 앞 파일 생성·다운로드 단계에서 전달해주세요.'} 파일 항목 이름은 ‘${property}’이며, 한 단계의 파일 총 크기는 8MB 이하${textFormat?'·CSV/JSON/텍스트는 UTF-8':''}여야 해요.`);
  }
  if(node.type===binaryNodeTypes[0])checklist.push(`${node.name}: 만든 파일은 실행 결과에서 다운로드하세요. 한 단계의 파일 총 크기는 8MB 이하이며 CSV·JSON·텍스트는 UTF-8로 만들어요.`);
 }
 return {workflow,requirements,summary:typeof result.summary==='string'?mask(result.summary,key).slice(0,1500):'단계를 확인하고 필요한 값을 입력한 뒤 실행해주세요.',checklist:[...new Set<string>(checklist)]};
 }catch(error){throw new DraftValidationError(error instanceof Error?error.message:'설계 응답 검증 오류');}
}

export async function generateWorkflow(prompt:string,options:AssistantOptions):Promise<{workflow:Flow;summary:string;checklist:string[]}>{const result=await generateAssistant(prompt,options);if(!result.workflow)throw new Error('초안이 없습니다.');return {...result,workflow:result.workflow};}
export async function converseWorkflow(prompt:string,options:AssistantOptions):Promise<AssistantResult>{return generateAssistant(prompt,{...options,conversational:true});}
