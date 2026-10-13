import {startBuilderScheduler,builderScheduleHealth} from './agent-builder-scheduler';
import {handleAgentBuilder} from './agent-builder';
import {createServer, type IncomingMessage, type ServerResponse} from 'node:http';
import {readFile,stat} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
import {randomUUID,randomBytes} from 'node:crypto';
import {Store} from './store';
import {executeFlow} from './engine';
import {assertWebhookJsonSize,buildWebhookResponse,prepareWebhookResponse} from './webhook-response';
import {readWebhookRequest,WebhookRequestError} from './webhook-request';
import {webhookResponseItems,webhookResponseResolver} from './webhook-context';
import {authenticateWebhook,encodedWebhookPath,validateWebhookConfiguration,webhookAuthentication,webhookCredentialProvider,webhookParams,WebhookAuthError} from './webhook-auth';
import type {WebhookResponse} from './contracts';
import {dispatchErrorWorkflow} from './error-workflows';
import {orchestrateWorkflow,analyzeExecutionError} from './agents';
import {SqliteCheckpointSaver} from './graph-checkpointer';
import {OAuthManager,getUsableCredential} from './oauth';
import {BINARY_KEY,createBinaryFileFromBase64,decodeBinaryFile,getBinary,jsonWithoutBinary,withBinary,assertBinaryItems} from './binary';
import {runCode} from './sandbox';
import {nextRunAt,validateSchedule} from './schedule';
import {validateFlow,makeFlow,catalog,clone,type Flow,type FlowNode,type Execution,type Json} from '../lib/workflow';

const port=Number(process.env.PORT||3010);
const host=process.env.HOST||'127.0.0.1';
const store=new Store(resolve(process.env.EASYFLOW_DATA_DIR||'data'));
const agentCheckpointer=new SqliteCheckpointSaver(resolve(process.env.EASYFLOW_DATA_DIR||'data','agent-checkpoints.sqlite'));
const chatInFlight=new Set<string>();
const oauth=new OAuthManager({port});
const usableCredential=(id:string,signal?:AbortSignal)=>getUsableCredential(store.getCredential(id),updated=>store.saveCredential(updated),{signal});
const controllers=new Map<string,AbortController>();
const inFlight=new Set<string>();
type PendingWebhook={res:ServerResponse;node:FlowNode;mode:'lastNode'|'responseNode';timer?:ReturnType<typeof setTimeout>;responseError?:string};
const pendingWebhooks=new Map<string,PendingWebhook>();
const responseTimeout=Number(process.env.EASYFLOW_WEBHOOK_RESPONSE_TIMEOUT_MS??60000);
if(!Number.isInteger(responseTimeout)||responseTimeout<0||responseTimeout>2147483647)throw new Error('Webhook 응답 제한 시간은 0부터 2147483647 사이의 밀리초로 설정해주세요.');
let closing=false;
if(!store.listFlows().length)store.saveFlow(makeFlow());
// Completed node checkpoints are durable. An interrupted external request may
// already have taken effect, so restart it only after the user explicitly retries.
for(const e of store.listPendingExecutions())if(e.status==='running')store.saveExecution({...e,status:'error',error:'프로그램이 실행 도중 종료됐어요. 마지막 외부 작업의 결과를 확인한 뒤 다시 실행해주세요.'});
function json(res:ServerResponse,status:number,data:any){res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(data));}
function respondWebhook(res:ServerResponse,id:string,response:WebhookResponse):void {
 if(res.writableEnded||res.destroyed)return;const prepared=prepareWebhookResponse(response);const bytes=prepared.binary?decodeBinaryFile(prepared.binary):prepared.body===undefined?undefined:typeof prepared.body==='string'?prepared.body:JSON.stringify(prepared.body);
 const mime=prepared.headers['content-type']||'application/octet-stream',extension=/json/i.test(mime)?'json':/html/i.test(mime)?'html':/csv/i.test(mime)?'csv':/pdf/i.test(mime)?'pdf':/^text\//i.test(mime)?'txt':'bin';
 store.saveWebhookResponse(id,res.req.method==='HEAD'?Buffer.alloc(0):Buffer.isBuffer(bytes)?bytes:Buffer.from(bytes??'','utf8'),mime,prepared.binary?.fileName||`응답.${extension}`);
 res.writeHead(prepared.statusCode,{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...prepared.headers,'X-Easyflow-Execution-Id':id});res.end(bytes);
}
function releaseWebhook(id:string):PendingWebhook|undefined {const pending=pendingWebhooks.get(id);if(pending){pendingWebhooks.delete(id);if(pending.timer)clearTimeout(pending.timer);}return pending;}
function completeWebhook(id:string,response:WebhookResponse):void {const pending=pendingWebhooks.get(id);if(!pending)return;respondWebhook(pending.res,id,response);releaseWebhook(id);}
function settleWebhook(execution:Execution):void {
 const pending=pendingWebhooks.get(execution.id);if(!pending||!['success','error','canceled'].includes(execution.status))return;
 try{if(execution.status!=='success')completeWebhook(execution.id,{statusCode:500,headers:{},body:{error:execution.error||'워크플로를 완료하지 못했어요.'}});
 else if(pending.mode==='lastNode'){const items=webhookResponseItems(execution);completeWebhook(execution.id,buildWebhookResponse(pending.node,items,'lastNode',webhookResponseResolver(pending.node,execution.id,items,execution)));}
 else completeWebhook(execution.id,{statusCode:200,headers:{},body:{message:'워크플로 실행을 마쳤어요.'}});
 }catch(error){pending.responseError=(error as Error).message;completeWebhook(execution.id,{statusCode:500,headers:{},body:{error:pending.responseError}});}
}
function retainWebhook(execution:Execution,res:ServerResponse,node:FlowNode,mode:'lastNode'|'responseNode'):void {
 const pending:PendingWebhook={res,node,mode};pendingWebhooks.set(execution.id,pending);res.once('close',()=>releaseWebhook(execution.id));
 if(responseTimeout)pending.timer=setTimeout(()=>completeWebhook(execution.id,{statusCode:504,headers:{},body:{error:'응답 대기 시간이 지났어요. 자동화는 계속 실행되며 실행 기록에서 결과를 확인할 수 있어요.'}}),responseTimeout);
}
function queryValues(query:URLSearchParams,hideToken=false):Json{return Object.fromEntries([...new Set(query.keys())].filter(name=>!hideToken||name!=='token').map(name=>{const values=query.getAll(name);return [name,values.length===1?values[0]:values];}));}
async function body(req:IncomingMessage,maxBytes=1500000){let size=0;const chunks:Buffer[]=[];for await(const chunk of req){size+=chunk.length;if(size>maxBytes)throw new Error(maxBytes===1500000?'입력은 1.5MB 이하로 줄여주세요.':'파일은 총 8MB, 일반 시작 데이터는 4MB 이하로 줄여주세요.');chunks.push(chunk);}const text=Buffer.concat(chunks).toString('utf8');return text?JSON.parse(text):{};}
function getFlow(id:string){const f=store.getFlow(id);if(!f)throw new Error('워크플로를 찾을 수 없어요.');return f;}
function checkLive(flow:Flow){
 if(!flow.nodes.length)throw new Error('실행할 단계를 먼저 추가해주세요.');
 for(const n of flow.nodes.filter(n=>!n.disabled)){
  if(!catalog.some(c=>c.n8nType===n.type))throw new Error(`‘${n.name}’은 아직 지원하지 않는 종류예요.`);
  const info=catalog.find(c=>c.n8nType===n.type)!;
  const conditional=n.type==='n8n-nodes-base.webhook'?webhookCredentialProvider(n):n.type==='n8n-nodes-base.respondToWebhook'&&n.parameters.respondWith==='jwt'&&flow.nodes.some(start=>!start.disabled&&start.type==='n8n-nodes-base.webhook'&&start.parameters.responseMode==='responseNode')?'jwt':undefined;
  if((info.credentialRequired||conditional)&&!n.credentialId)throw new Error(`‘${n.name}’에서 연결 계정을 선택해주세요.`);
  if(n.credentialId){const c=store.getCredential(n.credentialId);if(conditional&&c.provider!==conditional||info.credentialProviders&&!info.credentialProviders.includes(c.provider))throw new Error(`‘${n.name}’에 사용할 앱의 계정을 선택해주세요.`);if(info.credentialService&&c.service&&info.credentialService!==c.service)throw new Error(`‘${n.name}’에 ${info.name} 계정을 선택해주세요.`);}

 }
}
function enqueue(flow:Flow,input:Json[],mode:'learning'|'live',extra:Json={},onReserved?:(execution:Execution)=>void,reservedId?:string){
 if(mode==='live')checkLive(flow);
 const e:Execution={id:reservedId??randomUUID(),workflowId:flow.id,workflowName:flow.name,status:'queued',startedAt:new Date().toISOString(),duration:0,runs:[],mode,...extra};
 if(!store.reserveExecution(e,clone(flow),input,extra.dedupeKey))return null;
 onReserved?.(e);void pump();return e;
}
async function run(e:Execution){
 const snapshot=store.getExecutionSnapshot(e.id);if(!snapshot)return;
 const controller=new AbortController();controllers.set(e.id,controller);inFlight.add(e.id);
 const pending=pendingWebhooks.get(e.id);
 const metadata={trigger:e.trigger,onlyNode:e.onlyNode,retryOf:e.retryOf,parentExecutionId:e.parentExecutionId,errorWorkflowOf:e.errorWorkflowOf,dedupeKey:e.dedupeKey};
 try{const result={...await executeFlow(snapshot.flow,snapshot.input,{mode:e.mode,signal:controller.signal,executionId:e.id,onlyNode:e.onlyNode,resume:e.status==='waiting'?e:undefined,credential:id=>usableCredential(id,controller.signal),getFlow:id=>store.getFlow(id),code:runCode,webhookResponseMode:pending?.mode,onWebhookResponse:pending?.mode==='responseNode'?response=>completeWebhook(e.id,response):undefined,onProgress:update=>{store.saveExecution({...update,...metadata});settleWebhook(update);}}),...metadata};settleWebhook(result);if(pending?.responseError){result.status='error';result.error=pending.responseError;}store.saveExecution(result);const dispatched=await dispatchErrorWorkflow(result,snapshot.flow,id=>store.getFlow(id),enqueue);if(dispatched.executionId||dispatched.notificationError)store.saveExecution({...result,...(dispatched.executionId?{errorExecutionId:dispatched.executionId}:{}),...(dispatched.notificationError?{notificationError:dispatched.notificationError}:{})});}
 catch(err){const failed:Execution={...store.getExecution(e.id)!,status:controller.signal.aborted?'canceled':'error',error:(err as Error).message};store.saveExecution(failed);settleWebhook(failed);}
 finally{controllers.delete(e.id);inFlight.delete(e.id);void pump();}
}
async function pump(){if(closing)return;for(const e of store.listPendingExecutions()){if(inFlight.size>=2)break;if(inFlight.has(e.id))continue;if(e.status==='queued'||e.status==='waiting'&&Date.parse(e.resumeAt||'')<=Date.now()){inFlight.add(e.id);void run(e);}}}
function activate(flow:Flow,active:boolean){
 if(active){checkLive(flow);const triggers=flow.nodes.filter(n=>!n.disabled&&(n.type.endsWith('.scheduleTrigger')||n.type.endsWith('.webhook')));if(triggers.length!==1)throw new Error('자동 실행에는 예약 시작 또는 Webhook 시작 단계 하나가 필요해요.');const allStarts=flow.nodes.filter(n=>!n.disabled&&(n.type.endsWith('.manualTrigger')||n.type.endsWith('.scheduleTrigger')||n.type.endsWith('.webhook')));if(allStarts.length!==1)throw new Error('시작 단계는 하나만 사용해주세요. 기존 시작 단계에서 시작 방식을 바꿀 수 있어요.');const n=triggers[0];if(n.type.endsWith('.scheduleTrigger')){validateSchedule(n,flow.settings.timezone||'Asia/Seoul');const activatedAt=Date.now();flow.schedule={triggerId:n.id,activatedAt,nextRunAt:nextRunAt({...n,scheduleAnchorAt:activatedAt},activatedAt,flow.settings.timezone||'Asia/Seoul')};}if(n.type.endsWith('.webhook')){validateWebhookConfiguration(flow,n);flow.hookToken??=randomBytes(24).toString('hex');}}
 else delete flow.schedule;
 return store.saveFlow({...flow,active});
}
const timer=setInterval(()=>{
 if(process.env.A4B_FOCUSED_DEMO==='1')return;
 for(const f of store.listFlows()){if(!f.active||!f.schedule||f.schedule.nextRunAt>Date.now())continue;const n=f.nodes.find(n=>n.id===f.schedule.triggerId);if(!n)continue;const due=f.schedule.nextRunAt;try{enqueue(f,[{timestamp:new Date(due).toISOString(),timezone:f.settings.timezone||'Asia/Seoul'}],'live',{trigger:'schedule',dedupeKey:`${f.id}/${n.id}/${due}`});f.schedule.nextRunAt=nextRunAt({...n,scheduleAnchorAt:f.schedule.activatedAt},Math.max(due,Date.now()),f.settings.timezone||'Asia/Seoul');store.saveFlow(f);}catch(err){store.saveFlow({...f,active:false,activationError:(err as Error).message});}}
 void pump();
},500);

const server=createServer(async(req,res)=>{
 try{
  const authority=req.headers.host||`127.0.0.1:${port}`;const url=new URL(req.url||'/',`http://${authority}`);const path=url.pathname;const method=req.method||'GET';
  if(path.startsWith('/api/')){
   const allowedHosts=new Set([`127.0.0.1:${port}`,`localhost:${port}`,`[::1]:${port}`]);
   if(!allowedHosts.has(authority))return json(res,403,{error:'로컬 프로그램에서만 관리할 수 있어요.'});
   if(req.headers.origin&&!allowedHosts.has(new URL(req.headers.origin).host))return json(res,403,{error:'이 프로그램에서 요청해주세요.'});
   if(!['GET','HEAD'].includes(method)&&req.headers['content-type']&&!String(req.headers['content-type']).startsWith('application/json'))return json(res,415,{error:'JSON 입력을 사용해주세요.'});
   if(path==='/api/agent-builder/schedule-health'&&method==='GET')return json(res,200,builderScheduleHealth());
   if(await handleAgentBuilder(req,res,url,store,body,json))return;
   if(path==='/api/health')return json(res,200,{ok:true,standalone:true,version:'0.2.0',credentials:store.listCredentials().length});
   if(path==='/api/webhook/response'&&method==='GET'){
    const response=store.getWebhookResponse(url.searchParams.get('executionId')||'');if(!response)return json(res,404,{error:'저장된 Webhook 응답을 찾을 수 없어요.'});const fileName=response.fileName.replace(/[\/\\\u0000-\u001f\u007f]/g,'_').slice(0,200).toWellFormed(),extension=fileName.split('.').at(-1)?.replace(/[^a-z0-9]/gi,'')||'bin';
    res.writeHead(200,{'Content-Type':response.mime,'Content-Length':response.bytes.length,'Content-Disposition':`attachment; filename="response.${extension}"; filename*=UTF-8''${encodeURIComponent(fileName).replace(/['()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase())}`,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(response.bytes);return;
   }
   if(path==='/api/workflows'){
    if(method==='GET')return json(res,200,{workflows:store.listFlows()});
    if(method==='POST'){const value=validateFlow(await body(req));if(value.intentReview){if(!value.intentReview.reviewed||!Array.isArray(value.intentReview.requirements)||value.intentReview.requirements.some((r:any)=>r.status!=='mapped'||!Array.isArray(r.nodeIds)||!r.nodeIds.length||r.nodeIds.some((id:string)=>!value.nodes.some(n=>n.id===id))))throw new Error('초안 설계 근거에서 요구사항과 실제 단계를 확인해주세요.');}const prior=store.getFlow(value.id);const same=prior&&JSON.stringify([prior.nodes,prior.connections,prior.settings])===JSON.stringify([value.nodes,value.connections,value.settings]);value.active=!!(same&&prior.active);if(value.active&&prior?.schedule)value.schedule=prior.schedule;else delete value.schedule;if(prior?.hookToken)value.hookToken=prior.hookToken;else delete value.hookToken;delete value.n8nId;return json(res,200,{workflow:store.saveFlow(value)});}
    if(method==='DELETE'){store.deleteFlow(url.searchParams.get('id')||'');return json(res,200,{ok:true});}
   }
   if(path==='/api/files'&&method==='POST'){
    const data=await body(req,12*1024*1024);const file=createBinaryFileFromBase64(data.data,{mimeType:data.mimeType||'application/octet-stream',fileName:data.fileName});return json(res,200,{input:[withBinary({},data.property||'data',file)]});
   }
   if(path==='/api/files/download'&&method==='GET'){
    const execution=store.getExecution(url.searchParams.get('executionId')||'');if(!execution)return json(res,404,{error:'파일이 저장된 실행 기록을 찾을 수 없어요.'});const integer=(key:string)=>{const raw=url.searchParams.get(key)||'0';if(!/^\d{1,6}$/.test(raw))throw new Error('파일 위치를 확인해주세요.');return Number(raw);};const record=execution.runs[integer('runIndex')];if(!record)throw new Error('파일 단계의 실행 기록을 찾을 수 없어요.');const side=url.searchParams.get('side')||'output';if(!['input','output'].includes(side))throw new Error('파일 위치를 확인해주세요.');const items=side==='input'?record.input:record.output[integer('output')];const item=items?.[integer('item')];if(!item)throw new Error('선택한 파일 결과를 찾을 수 없어요.');const file=getBinary(item,url.searchParams.get('property')||'data');const bytes=decodeBinaryFile(file);res.writeHead(200,{'Content-Type':file.mimeType,'Content-Length':bytes.length,'Content-Disposition':`attachment; filename="download.${file.fileExtension?.replace(/[^a-z0-9]/gi,'')||'bin'}"; filename*=UTF-8''${encodeURIComponent(file.fileName).replace(/['()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase())}`,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(bytes);return;
   }
   if(path==='/api/activation'&&method==='POST'){const data=await body(req);return json(res,200,{workflow:activate(getFlow(data.workflowId),!!data.active)});}
   if(path==='/api/credentials'){
    if(method==='GET')return json(res,200,{credentials:store.listCredentials()});
    if(method==='POST')return json(res,200,{credential:store.saveCredential(await body(req))});
    if(method==='DELETE'){const id=url.searchParams.get('id')||'';if(store.listFlows().some(f=>f.active&&f.nodes.some(n=>n.credentialId===id)))throw new Error('이 계정을 사용하는 자동 실행을 먼저 꺼주세요.');store.deleteCredential(id);return json(res,200,{ok:true});}
   }
   if(path==='/api/oauth/begin'&&method==='POST')return json(res,200,oauth.begin(await body(req),authority));
   if(path==='/api/oauth/callback'&&method==='GET'){
    let ok=false;let message='';try{const credential=await oauth.complete({state:url.searchParams.get('state')||undefined,code:url.searchParams.get('code')||undefined,error:url.searchParams.get('error')||undefined},authority);store.saveCredential(credential);ok=true;message='계정 연결을 마쳤어요. 이 창을 닫고 Easyflow로 돌아가세요.';}catch{message='계정을 연결하지 못했어요. Easyflow에서 로그인 정보를 확인하고 다시 연결해주세요.';}
    res.writeHead(ok?200:400,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'"});res.end(`<!doctype html><html lang="ko"><meta charset="utf-8"><title>Easyflow 계정 연결</title><style>body{background:#201c28;color:#f2eafa;font:16px system-ui;padding:72px;line-height:1.8}h1{font-size:26px;color:#ff917d}</style><h1>${ok?'연결 완료':'연결 확인이 필요해요'}</h1><p>${message}</p><script>if(window.opener)window.opener.postMessage({type:'easyflow-oauth',ok:${ok}},location.origin);history.replaceState(null,'',location.pathname);</script></html>`);return;
   }
   if(path==='/api/credentials/test'&&method==='POST'){
    const c=await usableCredential((await body(req)).id);let target='';let headers:Record<string,string>={};
    if(c.provider==='openai'){target='https://api.openai.com/v1/models';headers.Authorization=`Bearer ${c.data.apiKey||c.data.token}`;}
    if(c.provider==='slack'){target='https://slack.com/api/auth.test';headers.Authorization=`Bearer ${c.data.token||c.data.apiKey}`;}
    if(c.service==='github'){target='https://api.github.com/user';headers={Authorization:`Bearer ${c.data.token}`,Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10'};}
    if(c.service==='notion'){target='https://api.notion.com/v1/users/me';headers={Authorization:`Bearer ${c.data.token}`,'Notion-Version':'2026-03-11'};}
    if(c.service==='telegram'){if(typeof c.data.token!=='string'||!/^[0-9]+:[A-Za-z0-9_-]+$/.test(c.data.token))throw new Error('Telegram 봇 토큰 형식을 확인해주세요.');target=`https://api.telegram.org/bot${c.data.token}/getMe`;}
    if(c.service==='googleSheets')return json(res,200,{message:c.data.authType==='oauth2'?'저장된 Google 로그인과 만료 상태를 확인했어요. 사용할 시트의 접근 권한은 해당 단계 실행에서 확인하세요.':'토큰을 저장했어요. 사용할 시트의 접근 권한은 해당 단계 실행에서 확인하세요.'});
    if(!target)return json(res,200,{message:'계정을 저장했어요. 요청 단계에서 이 계정을 선택하면 사용할 수 있어요.'});
    try{const r=await fetch(target,{headers,signal:AbortSignal.timeout(10000)});const data=await r.json() as Json;if(!r.ok||data.ok===false)throw new Error();return json(res,200,{message:'계정 연결을 확인했어요.'});}catch{throw new Error('계정 연결을 확인하지 못했어요. 토큰과 서비스 권한, 네트워크 연결을 확인해주세요.');}
   }
   if(path==='/api/assistant/sessions'&&method==='POST'){const data=await body(req);return json(res,200,store.createAssistantSession(data.currentWorkflow));}
   const chatMatch=path.match(/^\/api\/assistant\/sessions\/([a-z0-9-]+)$/);
   if(chatMatch&&method==='GET')return json(res,200,store.getAssistantSession(chatMatch[1]));
   if(chatMatch&&method==='POST'){
    const data=await body(req),session=store.getAssistantSession(chatMatch[1]);
    if(chatInFlight.has(session.id))throw new Error('같은 대화의 요청을 처리 중이에요. 완료 후 다시 보내주세요.');
    if(data.revision!==session.revision)throw new Error('대화가 변경됐어요. 다시 열고 확인해주세요.');
    if(typeof data.prompt!=='string'||data.prompt.length>4000||session.conversation.length>=24)throw new Error('대화 길이 제한을 확인해주세요. 새 대화를 시작할 수 있어요.');
    const credential=store.getCredential(data.credentialId);
    chatInFlight.add(session.id);try{
    const result=await orchestrateWorkflow(data.prompt,{credential,threadId:session.id,checkpointer:agentCheckpointer,credentials:store.listCredentials(),model:data.model,conversation:session.conversation,currentWorkflow:data.currentWorkflow?validateFlow(data.currentWorkflow):session.result?.workflow||session.context,previousRequirements:session.result?.requirements});
    const key=credential.data.apiKey||credential.data.token;
    session.conversation.push({role:'user',content:key?data.prompt.split(key).join('[보호된 연결 정보]'):data.prompt},{role:'assistant',content:result.summary});session.result=result;
    return json(res,200,store.saveAssistantSession(session,data.revision));
    }finally{chatInFlight.delete(session.id);}
   }
   if(path==='/api/assistant/converse'&&method==='POST'){const data=await body(req);const result=await orchestrateWorkflow(data.prompt,{credential:store.getCredential(data.credentialId),credentials:store.listCredentials(),model:data.model,conversation:data.conversation,currentWorkflow:data.currentWorkflow});return json(res,200,result);}
   if(path==='/api/assistant/analyze-error'&&method==='POST'){const data=await body(req);const execution=store.getExecution(data.executionId);if(!execution)return json(res,404,{error:'실행 기록을 찾을 수 없어요.'});const analysis=await analyzeExecutionError(execution,{credential:store.getCredential(data.credentialId),credentials:store.listCredentials(),model:data.model});return json(res,200,{analysis});}
   if(path==='/api/assistant/generate'&&method==='POST'){const data=await body(req);const result=await orchestrateWorkflow(data.prompt,{credential:store.getCredential(data.credentialId),credentials:store.listCredentials(),model:data.model});return json(res,200,result);}
   if(path==='/api/runs'&&method==='POST'){const data=await body(req,16*1024*1024);if(data.mode!=='learning'&&data.mode!=='live')throw new Error('실행 모드를 확인해주세요.');const input=data.input??[{}];if(!Array.isArray(input)||input.length>5000||input.some((v:any)=>!v||typeof v!=='object'||Array.isArray(v)))throw new Error('시작 데이터는 객체 목록으로 입력해주세요.');assertBinaryItems(input);const e=enqueue(getFlow(data.workflowId),input,data.mode,{onlyNode:data.onlyNode,trigger:'manual'});return json(res,202,{executionId:e!.id,execution:e});}
   if(path==='/api/executions'&&method==='GET'){const id=url.searchParams.get('id');if(id){const e=store.getExecution(id);if(!e)return json(res,404,{error:'실행 기록을 찾을 수 없어요.'});return json(res,200,{execution:e});}return json(res,200,{executions:store.listExecutions()});}
   if(path==='/api/evidence-review'&&method==='GET'){const e=store.getExecution(url.searchParams.get('id')||'');if(!e)throw new Error('실행 기록을 찾을 수 없어요.');return json(res,200,{checks:e.evidenceReview||{}});}
   if(path==='/api/evidence-review'&&method==='POST'){
    const data=await body(req),e=store.getExecution(data.id);if(!e||!['success','error','canceled'].includes(e.status))throw new Error('완료된 실행 기록을 선택해주세요.');
    if(typeof data.checked!=='boolean'||![data.runIndex,data.output,data.itemIndex,data.citationIndex].every(i=>Number.isInteger(i)&&i>=0))throw new Error('근거 위치를 확인해주세요.');
    const c=e.runs[data.runIndex]?.output[data.output]?.[data.itemIndex]?.documentEvidence?.citations?.[data.citationIndex];if(!c)throw new Error('실제로 사용한 문서 근거가 없어요.');
    const key=[data.runIndex,data.output,data.itemIndex,data.citationIndex].join(':');e.evidenceReview={...(e.evidenceReview||{}),[key]:{checked:data.checked,version:c.version,checkedAt:new Date().toISOString()}};store.saveExecution(e);return json(res,200,{checks:e.evidenceReview});
   }
   if(path==='/api/executions/cancel'&&method==='POST'){const id=(await body(req)).id;const e=store.getExecution(id);if(!e)throw new Error('실행 기록을 찾을 수 없어요.');if(['queued','running','waiting'].includes(e.status)){controllers.get(id)?.abort();const canceled:Execution={...e,status:'canceled',error:'사용자가 실행을 중지했어요.'};store.saveExecution(canceled);settleWebhook(canceled);}return json(res,200,{ok:true});}
   if(path==='/api/executions/retry'&&method==='POST'){const old=store.getExecution((await body(req)).id);if(!old||!['error','canceled'].includes(old.status))throw new Error('실패하거나 중지된 실행을 선택해주세요.');const snapshot=store.getExecutionSnapshot(old.id)!;const e=enqueue(snapshot.flow,snapshot.input,old.mode,{onlyNode:old.onlyNode,trigger:'retry',retryOf:old.id});return json(res,202,{executionId:e!.id,execution:e});}
   if(path==='/api/webhook'&&method==='GET'){const f=getFlow(url.searchParams.get('workflowId')||'');const n=f.nodes.find(n=>n.type.endsWith('.webhook'));if(!n)throw new Error('Webhook 시작 단계를 먼저 추가해주세요.');const base=(process.env.EASYFLOW_PUBLIC_URL||`http://${authority}`).replace(/\/+$/,'');const auth=webhookAuthentication(n);return json(res,200,{url:`${base}/hooks/${encodeURIComponent(f.id)}/${encodedWebhookPath(n)}${auth==='easyflowToken'?`?token=${f.hookToken||''}`:''}`,active:!!f.active,authentication:auth,httpMethod:n.parameters.httpMethod??'GET',responseMode:n.parameters.responseMode??'onReceived'});}
   return json(res,404,{error:'요청한 기능을 찾을 수 없어요.'});
  }
  if(path.startsWith('/hooks/')){
   const match=/^\/hooks\/([^/]+)\/(.+)$/.exec(path);if(!match)return json(res,404,{error:'Webhook 요청 주소를 확인해주세요.'});const f=store.getFlow(decodeURIComponent(match[1]));const n=f?.nodes.find(n=>!n.disabled&&n.type==='n8n-nodes-base.webhook'&&(n.id===decodeURIComponent(match[2])||webhookParams(n,match[2])!==undefined));
   if(!f?.active||!n)return json(res,404,{error:'Webhook 자동 실행이 꺼져 있거나 요청 주소가 달라요.'});
   validateWebhookConfiguration(f,n);const authenticated=await authenticateWebhook(n,f.hookToken,req.headers,url.searchParams,id=>usableCredential(id));
   if(method!==(n.parameters.httpMethod??'GET')){res.setHeader('Allow',n.parameters.httpMethod??'GET');return json(res,405,{error:'Webhook 요청 방식을 확인해주세요.'});}
   const mode=n.parameters.responseMode??'onReceived';if(mode!=='onReceived'&&pendingWebhooks.size>=500)return json(res,503,{error:'응답을 기다리는 요청이 많아요. 잠시 후 다시 보내주세요.'});
   const received=await readWebhookRequest(req,n);
   const input:Json[]=[{body:received.body,query:queryValues(url.searchParams,true),params:webhookParams(n,match[2])??{},headers:authenticated.headers,webhookUrl:`${(process.env.EASYFLOW_PUBLIC_URL||`http://${authority}`).replace(/\/+$/,'')}${path}`,executionMode:'production',...(authenticated.jwtPayload?{jwtPayload:authenticated.jwtPayload}:{}),...(received.binary?{[BINARY_KEY]:received.binary}:{})}];
   assertBinaryItems(input);assertWebhookJsonSize(input.map(jsonWithoutBinary));
   const executionId=randomUUID();let immediate:WebhookResponse|undefined;if(mode==='onReceived'){immediate=buildWebhookResponse(n,input,'onReceived',webhookResponseResolver(n,executionId,input));prepareWebhookResponse(immediate);}
   enqueue(f,input,'live',{trigger:'webhook'},execution=>{if(immediate){if(n.parameters.options?.responseData===undefined&&!n.parameters.options?.noResponseBody&&n.parameters.responseData===undefined)immediate.body={executionId:execution.id,message:'워크플로 실행을 시작했어요.'};respondWebhook(res,execution.id,immediate);}else retainWebhook(execution,res,n,mode);},executionId);return;
  }
  if(method!=='GET'&&method!=='HEAD')return json(res,405,{error:'요청 방식을 확인해주세요.'});
  const root=resolve('dist-app');let file=resolve(root,'.'+decodeURIComponent(path));if(!file.startsWith(root+'/'))file=resolve(root,'index.html');try{if(!(await stat(file)).isFile())file=resolve(root,'index.html');}catch{file=resolve(root,'index.html');}
  const content=await readFile(file);const mime:Record<string,string>={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon'};res.writeHead(200,{'Content-Type':mime[extname(file)]||'application/octet-stream','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'});res.end(method==='HEAD'?undefined:content);
 }catch(error){if(res.writableEnded||res.destroyed)return;if(error instanceof WebhookAuthError){for(const [name,value] of Object.entries(error.headers))res.setHeader(name,value);json(res,error.status,{error:error.message});}else if(error instanceof WebhookRequestError){
   // Write the complete, length-delimited error first. Finishing it immediately
   // makes HTTP destroySoon race an upload which has not read the response yet.
   const socket=req.socket,payload=JSON.stringify({error:error.message});let drained=0;
   const discard=(chunk:Buffer)=>{drained+=chunk.length;if(drained>=1_048_576)req.pause();};
   const finish=()=>{clearTimeout(grace);req.pause();if(!res.writableEnded&&!res.destroyed)res.end();};
   const grace=setTimeout(finish,1000);grace.unref();
   const deadline=setTimeout(()=>socket.destroy(),2000);deadline.unref();
   socket.once('close',()=>{clearTimeout(grace);clearTimeout(deadline);req.removeListener('data',discard);req.removeListener('end',finish);});
   res.writeHead(error.status,{'Content-Type':'application/json; charset=utf-8','Content-Length':Buffer.byteLength(payload),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Connection':'close'});res.write(payload);
   if(req.readableEnded)finish();else{req.on('data',discard);req.once('end',finish);req.resume();}
  }else json(res,400,{error:(error as Error).message});}
});
server.listen(port,host,()=>{console.log(`A4B: http://127.0.0.1:${port}`);startBuilderScheduler(store);if(process.env.A4B_FOCUSED_DEMO!=='1')void pump();});
function shutdown(){if(closing)return;closing=true;clearInterval(timer);for(const id of pendingWebhooks.keys())completeWebhook(id,{statusCode:503,headers:{},body:{error:'프로그램이 종료되어 응답을 마치지 못했어요. 실행 기록에서 결과를 확인해주세요.'}});for(const c of controllers.values())c.abort();server.close(()=>{const finish=()=>{if(inFlight.size){setTimeout(finish,20);return;}agentCheckpointer.close();store.close();process.exit(0);};finish();});}
process.on('SIGINT',shutdown);process.on('SIGTERM',shutdown);
