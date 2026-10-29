import {parseToolGapChoiceRequest,parseRedesignDecisionRequest} from './tool-gap-contract';
import {agentPython,backendPath,dataDirectory} from './paths';
import {builderNeedsModel} from './builder-model-requirements';
import {spawn} from 'node:child_process';
import {extname} from 'node:path';
import {existsSync} from 'node:fs';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {convertBuilderPdf} from './builder-documents';
import {analyzeBuilderDocument,analysisSupportsFormat,routingEnabled} from './document-analysis';
import {parseDocumentReviewCommand,documentPreview,originalMime} from './document-review';
import {createHash,randomUUID} from 'node:crypto';
import {localModelConnection,LOCAL_MODEL_ID} from './builder-local-model';
import type {IncomingMessage,ServerResponse} from 'node:http';
import type {Store} from './store';
import {parseCsv} from './binary';
import {readXlsx,extractPdfText} from './document-files';

const idPattern=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const locks=new Set<string>();
export function builderWorkerTimeout(action:string,env:NodeJS.ProcessEnv=process.env):number {
 const execution=['test','scheduled_run','retry_run','resume_run'].includes(action);
 const localDesign=['message','confirm'].includes(action)&&['ollama','vllm'].includes(env.A4B_MAIN_PROVIDER||'');
 const fallback=execution?900000:localDesign?600000:180000;
 const configured=Number(execution?env.A4B_WORKER_TIMEOUT_MS:localDesign?env.A4B_DESIGN_TIMEOUT_MS:undefined);
 return Number.isFinite(configured)&&configured>0&&configured<=1800000?configured:fallback;
}
export async function builderWorker(payload:Record<string,unknown>):Promise<any>{
 const python=agentPython();
 if(!existsSync(python))throw new Error('Deep Agents 실행 환경이 없습니다. Backend/scripts/setup-agent-demo.sh를 먼저 실행하세요.');
 const execution=['test','scheduled_run','retry_run','resume_run'].includes(String(payload.action));
 const runId=payload.action==='test'?randomUUID():String(payload.id||'');
 const executionToken=execution?randomUUID():undefined;
 const workerPayload=execution?{...payload,execution_token:executionToken,...(payload.action==='test'?{run_id:runId}:{})}:payload;
 return new Promise((accept,reject)=>{
  const child=spawn(python,[backendPath('agent_builder/worker.py')],{stdio:['pipe','pipe','pipe'],env:{...process.env,LANGCHAIN_TRACING_V2:'false',LANGSMITH_TRACING:'false'}});
  let stdout='',settled=false,heartbeatBusy=false;
  let heartbeat:ReturnType<typeof setInterval>|undefined;
  const clear=()=>{clearTimeout(timer);if(heartbeat)clearInterval(heartbeat);};
  const fail=async(message:string)=>{
   if(settled)return;settled=true;clear();child.kill('SIGKILL');
   if(execution)await builderWorker({action:'interrupt_run',id:runId,execution_token:executionToken}).catch(()=>{});
   reject(new Error(message));
  };
  const timeout=builderWorkerTimeout(String(payload.action));
  const timeoutMessage=execution?'실행 시간이 초과됐습니다. 실행 기록을 확인하고 실패 단계부터 재시도하세요.':'모델 설계 응답 시간이 초과됐습니다. 대화는 보존되어 있으니 서버 상태와 응답 제한 시간을 확인한 뒤 다시 시도하세요.';
  const timer=setTimeout(()=>void fail(timeoutMessage),timeout);
  if(execution){
   heartbeat=setInterval(()=>{
    if(settled||heartbeatBusy)return;heartbeatBusy=true;
    void builderWorker({action:'heartbeat_run',id:runId,execution_token:executionToken,...(payload.action==='scheduled_run'?{lease_token:payload.lease_token}:{})})
     .catch(()=>void fail('실행 소유권을 갱신하지 못했습니다. 실행 기록에서 재시도하세요.')).finally(()=>{heartbeatBusy=false;});
   },20000);heartbeat.unref();
  }
  child.on('error',()=>void fail('Python 실행 환경을 시작하지 못했습니다.'));
  child.stdout.on('data',chunk=>{stdout+=String(chunk);if(Buffer.byteLength(stdout)>12*1024*1024)void fail('에이전트 결과 크기 제한을 넘었습니다.');});
  child.stderr.resume();child.stdin.on('error',()=>{});
  child.on('close',()=>{
   if(settled)return;
   try{const data=JSON.parse(stdout.trim());if(!data.ok){void fail(data.error||'에이전트 실행에 실패했습니다.');return;}settled=true;clear();accept(data.result);}
   catch{void fail('에이전트 응답을 읽지 못했습니다. 실행 기록과 Python 환경을 확인하세요.');}
  });
  child.stdin.end(JSON.stringify({...workerPayload,directory:dataDirectory()}));
 });
}
export async function parseBuilderAsset(input:any){
 if(typeof input?.fileName!=='string'||!input.fileName.trim()||input.fileName.length>200||/[\/\\\u0000-\u001f]/.test(input.fileName))throw new Error('파일 이름을 확인하세요.');
 if(typeof input.data!=='string'||input.data.length>11200000||input.data.length%4!==0||!/^[A-Za-z0-9+/]*={0,2}$/.test(input.data))throw new Error('파일 데이터를 확인하세요.');
 const bytes=Buffer.from(input.data,'base64');if(bytes.toString('base64')!==input.data)throw new Error('파일 데이터를 확인하세요.');if(!bytes.length||bytes.length>8*1024*1024)throw new Error('파일은 8MB 이하로 연결하세요.');
 const format=extname(input.fileName).slice(1).toLowerCase();let rows:Record<string,unknown>[]=[],text='',pages:string[]=[],kind='table',documentMeta:Record<string,unknown>={};
 const routed=routingEnabled()&&analysisSupportsFormat(format)?await analyzeBuilderDocument(bytes,format):undefined;
 if(format==='csv'||format==='tsv'){
  let decoded:string;try{decoded=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{decoded=new TextDecoder('euc-kr',{fatal:true}).decode(bytes);}
  rows=parseCsv(decoded,{delimiter:format==='tsv'?'\t':','});
 }
 else if(format==='xlsx')rows=await readXlsx(bytes,{includeEmptyCells:true});
 else if(format==='json'){const value=JSON.parse(bytes.toString('utf8'));if(!Array.isArray(value)||value.length>5000||value.some(r=>!r||typeof r!=='object'||Array.isArray(r)))throw new Error('JSON은 객체 행의 배열로 입력하세요.');rows=value;}
 else if(routed||['txt','md','pdf'].includes(format)){kind='document';if(routed||format==='pdf'){const parsed=routed||await convertBuilderPdf(bytes);pages=parsed.pages;text=parsed.text;documentMeta={engine:parsed.engine,parser_version:parsed.parser_version,warnings:parsed.warnings,page_count:parsed.page_count,sections:parsed.sections,tables:parsed.tables,parser_status:parsed.parser_status,parser_metadata:parsed.parser_metadata};}else{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);pages=[text];}if(!text.trim()&&!routed)throw new Error('문서의 본문이 비어 있습니다.');if(text.length>1000000)throw new Error('문서는 1,000,000자 이내로 연결하세요.');}
 else throw new Error('지원되는 문서·이미지 또는 CSV·XLSX·JSON 표 파일을 연결하세요.');
 if(routed){text=routed.text;pages=routed.pages;documentMeta={engine:routed.engine,parser_version:routed.parser_version,warnings:routed.warnings,
  page_count:routed.page_count,sections:routed.sections,tables:routed.tables,parser_status:routed.parser_status,parser_metadata:routed.parser_metadata};}
 if(kind==='table'&&!rows.length)throw new Error('표 파일에 데이터 행이 없습니다.');
 const columns=[...new Set(rows.flatMap(row=>Object.keys(row)))];
 if(columns.length>200||columns.some(k=>!k.trim()||k.length>120||['__proto__','prototype','constructor'].includes(k)))throw new Error('열 이름 또는 열 수를 확인하세요. 최대 200개 열을 지원합니다.');
 if(rows.some(r=>Object.values(r).some(v=>v!==null&&!['string','number','boolean'].includes(typeof v))))throw new Error('표의 셀은 문자열·숫자·논리값·빈 값만 지원합니다.');
 const profile=Object.fromEntries(columns.map(k=>{const values=rows.map(r=>r[k]);const filled=values.filter(v=>v!==null&&v!==undefined&&v!=='');return [k,{types:[...new Set(filled.map(v=>typeof v))],missing:values.length-filled.length,duplicateValues:filled.length-new Set(filled.map(v=>JSON.stringify(v))).size,numericStrings:filled.filter(v=>typeof v==='string'&&v.trim()!==''&&Number.isFinite(Number(v))).length}];}));
 return {asset:{name:input.fileName,kind,format,hash:createHash('sha256').update(bytes).digest('hex'),row_count:rows.length,columns,profile,preview:rows.slice(0,3),text,pages,
  capabilities:[kind,...(kind==='table'&&routed?.text.trim()?['document']:[])],...documentMeta},rows};
}
export async function handleAgentBuilder(req:IncomingMessage,res:ServerResponse,url:URL,store:Store,readBody:(req:IncomingMessage,max?:number)=>Promise<any>,json:(res:ServerResponse,status:number,value:any)=>void):Promise<boolean>{
 if(!url.pathname.startsWith('/api/agent-builder/'))return false;
 const action=url.pathname.slice('/api/agent-builder/'.length),method=req.method||'GET';
 if(action==='document_review'&&['GET','POST'].includes(method)){
  const data=method==='GET'?{id:url.searchParams.get('id')}:await readBody(req,12000);
  if(!idPattern.test(data?.id||''))throw new Error('자료 ID를 확인하세요.');
  const payload=method==='GET'?{action:'document_review',id:data.id}:{action:'document_review',id:data.id,command:parseDocumentReviewCommand(data)};
  json(res,200,await builderWorker(payload));return true;
 }
 if(action==='document_preview'&&method==='GET'){
  const id=url.searchParams.get('id');if(!id||!idPattern.test(id))throw new Error('자료 ID를 확인하세요.');
  const {asset}=await builderWorker({action:'asset',id});
  const preview=await documentPreview(asset,url.searchParams.get('blockId'),url.searchParams.get('converted'));
  res.writeHead(200,{'Content-Type':preview.mime,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; frame-ancestors 'self'"});res.end(preview.bytes);return true;
 }
 if(action==='connection'&&method==='GET'){json(res,200,{connection:localModelConnection()});return true;}
 if(action==='report'&&method==='GET'){
  const id=url.searchParams.get('id');if(!id||!idPattern.test(id))throw new Error('실행 ID를 확인하세요.');
  const {run}=await builderWorker({action:'run',id});if(!/^reports\/[0-9a-f-]{36}\.(csv|json|md|txt)$/.test(run.report_path||''))throw new Error('저장된 보고서가 없습니다.');
  let content:Buffer;try{content=await readFile(dataDirectory(run.report_path));}catch{throw new Error('서버 보고서 파일을 찾지 못했습니다. 실행 기록을 확인하세요.');}
  res.writeHead(200,{'Content-Type':run.artifact?.mime||'application/octet-stream','Content-Disposition':`attachment; filename*=UTF-8''${encodeURIComponent(run.artifact?.name||'report.csv')}`,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(content);return true;
 }
 if(['approved_replies','review_report'].includes(action)&&method==='GET'){
  const id=url.searchParams.get('id');if(!id||!idPattern.test(id))throw new Error('항목 ID를 확인하세요.');
  let artifact;
  if(action==='approved_replies')artifact=await builderWorker({action,id});
  else {const {run}=await builderWorker({action:'run',id});if(!run.reviews?.length)throw new Error('검수 보고서가 없습니다.');artifact={name:'검수_보고서_'+id+'.json',mime:'application/json; charset=utf-8',content:JSON.stringify({id:run.id,agent_version:run.agent_version,source_version:run.source_version,created_at:run.created_at,status:run.status,reviews:run.reviews},null,2)};}
  res.writeHead(200,{'Content-Type':artifact.mime,'Content-Disposition':`attachment; filename*=UTF-8''${encodeURIComponent(artifact.name)}`,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(artifact.content);return true;
 }
 if(action==='original'&&method==='GET'){const id=url.searchParams.get('id');if(!id||!idPattern.test(id))throw new Error('자료 ID를 확인하세요.');const {asset}=await builderWorker({action:'asset',id});if(!/^builder-originals\/[a-f0-9]{64}\.[a-z0-9]+$/.test(asset.original_path||''))throw new Error('원본 파일이 없는 이전 자료입니다. 다시 연결하세요.');const content=await readFile(dataDirectory(asset.original_path));res.writeHead(200,{'Content-Type':originalMime(asset.format),'Content-Disposition':`inline; filename*=UTF-8''${encodeURIComponent(asset.name)}`,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(content);return true;}
 if(action==='nodes'&&method==='GET'){json(res,200,await builderWorker({action:'nodes'}));return true;}
 if(action==='nodes'&&method==='POST'){
  const data=await readBody(req,50000);
  if(!data?.spec||typeof data.spec!=='object'||Array.isArray(data.spec)||data.id!==undefined&&(typeof data.id!=='string'||!idPattern.test(data.id))||data.baseVersion!==undefined&&(!Number.isInteger(data.baseVersion)||data.baseVersion<0))throw new Error('노드 설정과 버전을 확인하세요.');
  json(res,200,await builderWorker({action:'save_node',spec:data.spec,id:data.id,base_version:data.baseVersion??0}));return true;
 }
 if(action==='catalog'&&method==='GET'){json(res,200,await builderWorker({action:'catalog'}));return true;}
 if(action==='agents'&&method==='GET'){json(res,200,await builderWorker({action:'agents'}));return true;}
 if(action==='tool_gap_replan_decision'&&method==='POST'){
  const data=parseRedesignDecisionRequest(await readBody(req,10000));
  if(locks.has(data.id)){json(res,409,{error:'이 대화의 이전 요청을 처리 중입니다.'});return true;}
  locks.add(data.id);
  try{res.setHeader('Cache-Control','no-store');json(res,200,await builderWorker({action:'tool_gap_replan_decide',id:data.id,
   revision:data.revision,request_id:data.requestId,proposal_id:data.proposalId,decision:data.decision}));}
  finally{locks.delete(data.id);}return true;
 }
 if(action==='tool_gap_choice'&&method==='POST'){
  const data=parseToolGapChoiceRequest(await readBody(req,10000));
  if(locks.has(data.id)){json(res,409,{error:'이 대화의 이전 요청을 처리 중입니다. 완료 후 다시 시도하세요.'});return true;}
  locks.add(data.id);
  try{res.setHeader('Cache-Control','no-store');json(res,200,await builderWorker({action:'tool_gap_choice',id:data.id,
   revision:data.revision,request_id:data.requestId,gap_id:data.gapId,state_fingerprint:data.stateFingerprint,choice:data.choice}));}
  finally{locks.delete(data.id);}
  return true;
 }
 if(action==='tool_gap'&&method==='GET'){
  const id=url.searchParams.get('id'),raw=url.searchParams.get('revision');
  if(!id||!idPattern.test(id)||!raw||!/^\d+$/.test(raw)||!Number.isSafeInteger(Number(raw)))throw new Error('대화 ID와 버전을 확인하세요.');
  res.setHeader('Cache-Control','no-store');
  json(res,200,await builderWorker({action:'tool_gap',id,revision:Number(raw)}));return true;
 }
 const data=method==='GET'?{id:url.searchParams.get('id')}:await readBody(req,['upload','run_upload','source_upload'].includes(action)?12*1024*1024:100000);
 if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('요청 형식을 확인하세요.');
 const workerAction=action==='sessions'&&method==='POST'?'new_session':action;
 if(!['new_session','session','agent','upload','message','confirm','test','run_upload','resume_run','retry_run','review_decision','run','source_upload','schedule','schedule_test','schedule_cancel'].includes(workerAction)||(['session','agent','run'].includes(workerAction)?method!=='GET':method!=='POST')){json(res,405,{error:'지원하지 않는 요청입니다.'});return true;}
 if(workerAction!=='new_session'&&!idPattern.test(data.id||''))throw new Error('항목 ID를 확인하세요.');
 const lockKey=['session','agent','run'].includes(workerAction)?undefined:workerAction==='test'?'test:'+data.id:data.id;
 if(lockKey&&locks.has(lockKey)){json(res,409,{error:'이 대화의 이전 요청을 처리 중입니다. 완료 후 다시 시도하세요.'});return true;}
 if(lockKey)locks.add(lockKey);
 try{
  let payload:any={action:workerAction,id:data.id};
  if(workerAction==='new_session'){
   if(typeof data.model!=='string'||!data.model.trim()||data.model.length>120||!/^[-\w./:]+$/.test(data.model))throw new Error('모델 이름을 확인하세요.');
   const local=localModelConnection();const credential=data.credentialId===LOCAL_MODEL_ID&&local?{...local.credential,data:{}}:store.getCredential(data.credentialId);if(credential.provider!=='openai')throw new Error('OpenAI 연결 계정을 선택하세요.');
   if(data.agentId&&!idPattern.test(data.agentId))throw new Error('에이전트 ID를 확인하세요.');
   if(data.nodeRefs!==undefined&&(!Array.isArray(data.nodeRefs)||data.nodeRefs.length>10||new Set(data.nodeRefs).size!==data.nodeRefs.length||data.nodeRefs.some((r:unknown)=>typeof r!=='string'||!/^[a-f0-9-]{36}:[1-9][0-9]{0,5}$/.test(r))))throw new Error('선택한 노드 버전을 확인하세요.');
   payload={action:workerAction,model:credential.id===LOCAL_MODEL_ID?local!.model:data.model,credential_id:credential.id,agent_id:data.agentId,node_refs:data.nodeRefs};
  }
  if(['upload','message','confirm'].includes(workerAction)){if(!Number.isInteger(data.revision)||data.revision<0)throw new Error('대화 버전을 확인하세요.');payload.revision=data.revision;}
  if(['message','test','resume_run'].includes(workerAction)){
   if(typeof data.content!=='string'||!data.content.trim()||data.content.length>4000)throw new Error('설명은 1~4,000자로 입력하세요.');payload.content=data.content;
  }
  if(workerAction==='message'&&data.questionId!==undefined){
   if(typeof data.questionId!=='string'||!/^[a-f0-9]{24}$/.test(data.questionId))throw new Error('확인 질문 ID를 확인하세요.');
   payload.question_id=data.questionId;
  }
  if(['message','confirm','test','resume_run','retry_run'].includes(workerAction)){
   const current=await builderWorker({action:workerAction==='test'?'agent':['resume_run','retry_run'].includes(workerAction)?'run':'session',id:data.id});
   const config=workerAction==='test'?current.agent.config:['resume_run','retry_run'].includes(workerAction)?current.run.config_snapshot:null;
   const needsModel=builderNeedsModel(config,workerAction==='test'?current.agent.workflow_dependencies:current.run?.workflow_dependencies);
   if(needsModel){
    const credentialId=workerAction==='test'?current.agent.credential_id:['resume_run','retry_run'].includes(workerAction)?current.run.credential_id:current.credential_id;
    const local=localModelConnection();const credential=credentialId===LOCAL_MODEL_ID&&local?{...local.credential,data:{apiKey:'local-not-a-secret',token:''}}:store.getCredential(credentialId);
    if(credential.provider!=='openai')throw new Error('모델 연결 계정을 확인하세요.');
    payload.api_key=credential.data.apiKey||credential.data.token;
   }
  }
  if(['upload','run_upload','source_upload'].includes(workerAction)){Object.assign(payload,await parseBuilderAsset(data));const relative='builder-originals/'+payload.asset.hash+'.'+payload.asset.format;await mkdir(dataDirectory('builder-originals'),{recursive:true});await writeFile(dataDirectory(relative),Buffer.from(data.data,'base64'));payload.asset.original_path=relative;payload.asset.version=1;payload.asset.processing_status=payload.asset.parser_status==='partial'?'partial':'ready';}
  if(workerAction==='test'&&data.replyDraft!==undefined){if(typeof data.replyDraft!=='string'||data.replyDraft.length>12000)throw new Error('검수 초안은 12,000자 이하로 입력하세요.');payload.reply_draft=data.replyDraft;}
  if(workerAction==='review_decision'){
   if(typeof data.reviewId!=='string'||!/^[-\w]+:\d+$/.test(data.reviewId)||data.reviewId.length>60)throw new Error('검수 항목 ID를 확인하세요.');
   if(!['approve','reject'].includes(data.decision)||typeof data.editedReply!=='string'||data.editedReply.length>12000||data.decision==='approve'&&!data.editedReply.trim())throw new Error('결정과 승인할 응대문을 확인하세요.');
   Object.assign(payload,{review_id:data.reviewId,decision:data.decision,edited_reply:data.editedReply});
  }
  if(workerAction==='schedule_cancel'){if(!idPattern.test(data.jobId||''))throw new Error('시험 예약 ID를 확인하세요.');payload.job_id=data.jobId;}
  if(workerAction==='schedule'){if(typeof data.active!=='boolean')throw new Error('예약 활성 상태를 확인하세요.');payload.active=data.active;}
  if(workerAction==='test'&&data.fileIds!==undefined){if(!Array.isArray(data.fileIds)||data.fileIds.length>10||data.fileIds.some((id:unknown)=>typeof id!=='string'||!idPattern.test(id)))throw new Error('실행 자료 ID를 확인하세요.');payload.file_ids=data.fileIds;}
  json(res,200,await builderWorker(payload));return true;
 }finally{if(lockKey)locks.delete(lockKey);}
}
