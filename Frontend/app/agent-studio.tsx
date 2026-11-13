import {WorkflowPreview,type Workflow} from './workflow-preview';
import {NodeInspector,RuleSummary} from './node-inspector';
import {StudioMessage} from './studio-message';
import {GmailSummaryResult,isGmailSlackSummaryWorkflow} from './gmail-summary-result';
import {RunAnswer} from './run-answer';
import {RunWorkflowDiagram} from './run-workflow-diagram';
import {RunFileDownload, suggestedRunFilename} from './run-file-download';
import {toolDescription} from './tool-copy';
import {FileInspection,type InspectedFile} from './file-inspection';
import {ReceiptQuestion, receiptFileTypes, type ReceiptAction} from './receipt-question';
import {DocumentAnalysisSummary} from './document-analysis-summary';
import {DocumentReviewPanel} from './document-review-panel';
import type {DocumentReview,ReviewableAsset} from '../lib/document-review';
import type {DocumentAnalysis} from '../lib/api-contracts';
import {NodeLibrary,type LibraryNode,type ToolInfo} from './node-library';
import {nodeSelectionPool} from './node-selection';
import {shouldRefreshAgentRuns,refreshedAgentRun,startAgentRunRefresh} from './agent-run-refresh';
import {useEffect,useRef,useState,type FormEvent,type ReactNode} from 'react';
import {ArrowLeft,ArrowUp,Check,ChevronDown,FileText,Loader2,Paperclip,Plus,Workflow as WorkflowIcon,X,Settings2,CheckCircle2,AlertCircle,Database,SlidersHorizontal,FileOutput,MessageSquare,RefreshCw} from 'lucide-react';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription} from '@/components/ui/dialog';
import {request} from '@/components/flow/connection-panel';
import './agent-studio.css';
import './run-workflow-diagram.css';
import {TaskLibraryHeader,WorkspaceSidebar} from './workspace-nav';
import {AttachUploadDialog} from './upload-library';
import {WorkspaceHeader,WorkspaceHelp} from './workspace-header';
import {ReplyReviewPanel,type ReplyReview} from './reply-review-panel';
import {GmailConnection} from './gmail-connection';
import {scheduleDescription,scheduleDateNote,type ScheduleSpec} from '../lib/schedule-spec';
import {ScheduleLibrarySourceSettings,type ScheduleLibrarySource} from './schedule-library-source';
import {ScheduleSettings} from './schedule-settings';
import {type QuestionResponse,type ClarificationQuestionData} from './clarification-question';
import {ClarificationForm,type ClarificationAnswer} from './clarification-form';
import {SlackChannelPicker} from './slack-channel-picker';


type SavedSchedule={active:boolean;next_run_at:string;spec:ScheduleSpec;agent_version:number};
type Source={id:string;asset_id:string;version:number;asset:Resource};
type Job={id:string;kind:string;status:string;due_at:string;run_id?:string;error?:string;source_name?:string;source_version?:string};
type Draft={schedule?:ScheduleSpec|null;report_destination?:string|null;workflow?:Workflow|null;name:string;purpose:string;instructions:string;tool_ids:string[];document_ids:string[];requirements:{description:string;source_id:string;quote:string}[];questions:string[];question_options?:string[][];question_responses?:(QuestionResponse|null)[];unsupported:string[];explanation:string;execution_plan?:{match:string;filters:{field:string;operator:string;compare_field:string|null;value:unknown}[];sort:{field:string;direction:string;value_type:string}[];missing_policy:string;duplicate_policy:string;duplicate_field:string|null;output_format:string}};
type Resource={original_path?:string;capabilities?:string[];parser_metadata?:{document_analysis?:DocumentAnalysis;document_review?:DocumentReview};id:string;name:string;kind:string;format:string;row_count:number;columns:string[];text:string;profile:Record<string,unknown>;warnings?:string[];engine?:string;page_count?:number;preview:Record<string,unknown>[]};
type InputRequirement={node_id?:string;kind:string;timing:string;fields:string[];ready:boolean;description?:string};
type RunFileRequirement = InputRequirement & {required_before_start:boolean; issues?:string[]; file_ids?:string[]};
type RunInputs = {schedule_request_required?:boolean;request_required?:boolean; input_requirements?:RunFileRequirement[]; source_required?:boolean; required_connections?:string[]; library_source?:ScheduleLibrarySource|null};
type Session={last_generation_request_id?:string;requirement_failure?:{kind:string;retryable:boolean}|null;execution_mode?:'manual'|'scheduled'|null;file_inspection?:InspectedFile[];tool_gap?:{gap_id:string;revision:number}|null;input_requirements?:InputRequirement[];pending_questions?:{id:string;key:string;text:string;options?:string[];response?:QuestionResponse;group?:string;reason?:string;required?:boolean;action?:ReceiptAction;supersedes_texts?:string[]}[];id:string;revision:number;model:string;credential_id:string;agent_id:string|null;base_version:number;messages:{role:string;content:string}[];draft:Draft|null;status:string;issues:string[];trace:{node:string;label:string;status:string;detail:string}[];resources:Resource[];saved_agent_id?:string};
type ConversationRecovery={before:Session;requestId:string;action:'message'|'confirm';includeSchedule?:boolean;content?:string;draftText?:string;fromComposer?:boolean;error:string;running:boolean;pollCount?:number};
type GenerationRequest={request_id:string;result_revision?:number;workflow_id?:string;action:string;session_revision:number;request_text:string;status:string;reason?:string|null};
type IncompleteWorkflow={summary?:string;questions?:{question:string}[];checklist?:string[];requirements?:{description:string;status:string}[]};

function explainIncompleteWorkflow(result:IncompleteWorkflow,request:string):string {
 const unique=(items:string[])=>[...new Set(items.map(item=>item.trim()).filter(Boolean))];
 const needsInput=unique((result.requirements||[]).filter(item=>item.status==='needs_input').map(item=>item.description));
 const unsupported=unique((result.requirements||[]).filter(item=>item.status==='unsupported').map(item=>item.description));
 const questions=unique((result.questions||[]).map(item=>item.question));
 const checklist=unique((result.checklist||[]).filter(item=>
  !needsInput.some(detail=>item.includes(detail))&&!unsupported.some(detail=>item.includes(detail))));
 const parts=['워크플로우 초안을 아직 만들지 못했어요.'];
 const summary=result.summary?.trim();
 if(summary&&summary!==request.trim()&&![...needsInput,...unsupported,...questions,...checklist].includes(summary))parts.push(summary);
 if(needsInput.length)parts.push('아직 정해지지 않은 내용:\n'+needsInput.map(item=>'• '+item).join('\n'));
 if(unsupported.length)parts.push('지원 여부를 확인할 작업:\n'+unsupported.map(item=>'• '+item).join('\n'));
 if(checklist.length)parts.push('추가 확인 사항:\n'+checklist.map(item=>'• '+item).join('\n'));
 if(questions.length)parts.push('답변해 주세요:\n'+questions.map((item,index)=>`${index+1}. ${item}`).join('\n'));
 if(parts.length===1)parts.push('필요한 자료, 처리 조건, 결과를 보낼 곳을 알려주세요.');
 return parts.join('\n\n');
}

type Agent={id:string;version:number;config:Draft;model:string;credential_id:string;updated_at:string;workflow_dependencies?:Record<string,Agent>};
function evidenceLocation(evidence:{page?:number|null;native_location?:Record<string,unknown>}|null):string {
 const loc=evidence?.native_location;
 if(loc?.sheet)return ' · '+String(loc.sheet)+(loc.cell?'!'+String(loc.cell):'');
 if(loc?.slide)return ' · 슬라이드 '+String(loc.slide)+(loc.shape_id?' · 객체 '+String(loc.shape_id):'');
 if(loc?.converted_page)return ' · 변환 PDF '+String(loc.converted_page)+'쪽';
 if(loc?.frame)return ' · 이미지 프레임 '+String(loc.frame);
 if(loc?.logical_row)return ' · '+String(loc.logical_row)+'행';
 if(loc?.body_index)return ' · 본문 객체 '+String(loc.body_index);
 return evidence?.page?' · '+String(evidence.page)+'쪽':'';
}
function usesTool(agent:Agent,tool:string,seen=new Set<string>()):boolean{if(seen.has(agent.id))return false;seen.add(agent.id);return agent.config.tool_ids.includes(tool)||Object.values(agent.workflow_dependencies||{}).some(child=>usesTool(child,tool,seen));}
function gmailReadLimit(agent:Agent|null):number|undefined {
 for(const node of agent?.config.workflow?.nodes||[])if(node.tool==='email.list'&&node.settings.operation==='read_messages'){
  try{const limit=JSON.parse(String(node.settings.arguments_json||'{}')).max_results;return typeof limit==='number'?limit:undefined;}catch{return undefined;}
 }
 for(const child of Object.values(agent?.workflow_dependencies||{})){const limit=gmailReadLimit(child);if(limit!==undefined)return limit;}
}
type TestRun={library_source_files?:{space:string;team_id?:string|null;asset_id:string;execution_asset_id?:string;name:string;version:string}[];public_projection?:{history_preview?:boolean;evidence_returned:number;evidence_total:number;evidence_truncated:boolean;quote_truncated:boolean};config_snapshot?:{name?:string;workflow?:Workflow|null};reviews?:ReplyReview[];approval_status?:string|null;id:string;status:string;answer:string;error?:string;error_type?:string;steps:Session['trace'];rows:Record<string,unknown>[];artifact:{name:string;content:string;mime:string;encoding?:'base64'}|null;agent_version:number;trigger?:string;report_path?:string;source_version?:number;attempts?:{number:number;status:string;error?:string;started_at:string;finished_at?:string}[];question?:{question:string;node_id:string}|null;evidence?:{file_name:string;file_id?:string;page?:number|null;native_location?:Record<string,unknown>;text?:string;quote?:string}[]};
const toolDetails:Record<string,{label:string;description:string;Icon:typeof Database}>={read_table:{label:'표 파일 읽기',description:'연결 자료의 실제 행을 읽습니다.',Icon:Database},filter_sort:{label:'조건에 따라 정리',description:'조건·정렬·예외 정책을 적용합니다.',Icon:SlidersHorizontal},write_result:{label:'결과 파일 만들기',description:'CSV·JSON 결과를 만듭니다.',Icon:FileOutput}};
const samples:{label:string;text:string}[]=[];
async function asBase64(file:File){return new Promise<string>((accept,reject)=>{const reader=new FileReader();reader.onload=()=>accept(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('파일을 읽지 못했습니다.'));reader.readAsDataURL(file);});}
function download(artifact:{name:string;content:string;mime:string;encoding?:'base64'}){const url=URL.createObjectURL(new Blob([artifact.encoding==='base64'?Uint8Array.from(atob(artifact.content),c=>c.charCodeAt(0)):artifact.content],{type:artifact.mime}));const a=document.createElement('a');a.href=url;a.download=artifact.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function downloadToolGap(session:Session){
 const result=await request('/api/agent-builder/tool_gap?id='+encodeURIComponent(session.id)+'&revision='+session.revision);
 download({name:'tool-gap-'+session.id+'.json',mime:'application/json',content:JSON.stringify(result.tool_gap,null,2)});
}
export default function AgentStudio({onNavigate,onExternalFlow,onLocationChange,onCanLeaveChange,initialPrompt,initialFile}:{initialPrompt?:string;initialFile?:File;onLocationChange?:()=>void;onCanLeaveChange?:(fn:()=>boolean)=>void;onNavigate?:(next:'home'|'workflows'|'schedules'|'history'|'assets'|'connections')=>void;onExternalFlow?:(id:string)=>void}={}){
 const initialScreen=new URLSearchParams(location.search).get('screen');
 const [screen,setScreen]=useState<'nodes'|'workflow'|'run'|'schedule'>(initialScreen==='nodes'||initialScreen==='run'||initialScreen==='workflow'||initialScreen==='schedule'?initialScreen:new URLSearchParams(location.search).has('service')?'run':'workflow');
 const screenRef=useRef(screen);screenRef.current=screen;
 const externalPromptContext=useRef('');
 const scheduleDirtyRef=useRef(false),internalHistoryIndex=useRef(Number(window.history.state?.a4bIndex||0));
 const editorGrid=useRef<HTMLDivElement>(null);


 const [libraryVisited,setLibraryVisited]=useState(initialScreen==='nodes');
 const [selectedNodeId,setSelectedNodeId]=useState<string|null>(null);
 const [editorPanel,setEditorPanel]=useState<'conversation'|'canvas'|'settings'>('conversation');
 const [settingsOpen,setSettingsOpen]=useState(false);
 function showEditorPanel(panel:typeof editorPanel){
  setEditorPanel(panel);
  if(panel==='settings')setSettingsOpen(true);
 }
 const [library,setLibrary]=useState<LibraryNode[]>([]),[presets,setPresets]=useState<LibraryNode[]>([]),[catalog,setCatalog]=useState<ToolInfo[]>([]),[chosenRefs,setChosenRefs]=useState<string[]>([]),[chosenSnapshots,setChosenSnapshots]=useState<LibraryNode[]>([]);
 const nodeChoices=nodeSelectionPool([...presets,...library],chosenSnapshots,chosenRefs);
 function go(next:'nodes'|'workflow'|'run'|'schedule',record=false){if(next!==screen)window.scrollTo({top:0,behavior:'instant'});if(next==='nodes')setLibraryVisited(true);setScreen(next);const url=new URL(location.href);url.searchParams.set('screen',next);if(next!=='run')url.searchParams.delete('run');if(record&&url.href!==location.href)window.history.pushState({...window.history.state,a4bIndex:Number(window.history.state?.a4bIndex||0)+1,returnToDesign:next==='nodes'&&screen==='workflow'},'',url);else window.history.replaceState(window.history.state,'',url);internalHistoryIndex.current=Number(window.history.state?.a4bIndex||0);onLocationChange?.();}
 function confirmSettingsExit(){return !scheduleDirtyRef.current||window.confirm('저장하지 않은 자료함 연결 또는 예약 설정이 있습니다. 이동하면 변경 내용이 사라집니다. 이동할까요?');}
 function clearSettingsDirty(){scheduleDirtyRef.current=false;setScheduleSourceDirty(false);setScheduleSettingsDirty(false);}
 function switchScreen(next:'nodes'|'workflow'|'run'|'schedule'){if(next!==screen){if(!confirmSettingsExit())return;clearSettingsDirty();}go(next,true);}
 useEffect(()=>{onLocationChange?.();});
 useEffect(()=>{const pop=()=>{const next=new URLSearchParams(location.search).get('screen');
  if(next==='nodes'||next==='workflow'||next==='run'||next==='schedule'){
   if(next!==screenRef.current&&!confirmSettingsExit()){
    const delta=internalHistoryIndex.current-Number(window.history.state?.a4bIndex||0);
    if(delta)window.history.go(delta);else{const url=new URL(location.href);url.searchParams.set('screen',screenRef.current);window.history.replaceState(window.history.state,'',url);onLocationChange?.();}return;
   }
   if(next!==screenRef.current)clearSettingsDirty();

   if(next==='nodes')setLibraryVisited(true);setScreen(next);internalHistoryIndex.current=Number(window.history.state?.a4bIndex||0);
  }
 };window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[]);
 async function refreshLibrary(){const result=await request('/api/agent-builder/nodes');setLibrary(result.nodes);setPresets(result.defaults);}

 const [agents,setAgents]=useState<Agent[]>([]),[credentialId,setCredentialId]=useState(''),[model,setModel]=useState('');
 const [session,setSession]=useState<Session|null>(null),[selected,setSelected]=useState<Agent|null>(null),[agentResources,setAgentResources]=useState<Resource[]>([]),[text,setText]=useState(initialPrompt ?? ''),[busy,setBusy]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 const [externalMessages,setExternalMessages]=useState<Session['messages']>([]);
 useEffect(()=>{
  if(initialPrompt!==undefined&&window.history.state?.initialPrompt===initialPrompt){
   const {initialPrompt:consumed,...state}=window.history.state;
   window.history.replaceState(state,'',location.href);
  }
 },[initialPrompt]);
 const initialFilePending=useRef(initialFile);
 const [initialFileReady,setInitialFileReady]=useState(!initialFile);
 const initialPromptPending=useRef(!!initialPrompt?.trim());
 const [pendingMessage,setPendingMessage]=useState('');
 const [recovery,setRecovery]=useState<ConversationRecovery|null>(null);
 const [initialLoadFailed,setInitialLoadFailed]=useState(false);
 const [libraryBusy,setLibraryBusy]=useState(false),[libraryDirty,setLibraryDirty]=useState(false);
 const [scheduleSourceDirty,setScheduleSourceDirty]=useState(false);
 const [scheduleSettingsDirty,setScheduleSettingsDirty]=useState(false);
 const [clarificationDirty,setClarificationDirty]=useState(false);
 scheduleDirtyRef.current=scheduleSourceDirty||scheduleSettingsDirty;

 const [runInputs,setRunInputs] = useState<RunInputs>({});
 const [gmailConnected,setGmailConnected] = useState(false);
 const [slackConnection,setSlackConnection] = useState<{agentId:string;connected:boolean}|null>(null);
 function applyRunInputs(result:RunInputs & {resources:Resource[]}) {
  setAgentResources(result.resources);
  setRunInputs({schedule_request_required:result.schedule_request_required,request_required:result.request_required, input_requirements:result.input_requirements, source_required:result.source_required, required_connections:result.required_connections, library_source:result.library_source});
 }
 const [resource,setResource]=useState<Resource|null>(null),[testText,setTestText]=useState(''),[testRun,setTestRun]=useState<TestRun|null>(null);
 const [runFiles,setRunFiles]=useState<Resource[]>([]),[evidence,setEvidence]=useState<{file_name:string;file_id?:string;page?:number|null;native_location?:Record<string,unknown>;text?:string;quote?:string}|null>(null);
 const [replyDraft,setReplyDraft]=useState('');
 const [history,setHistory]=useState<TestRun[]>([]);
 const [queuedSourceRuns,setQueuedSourceRuns]=useState<string[]>([]);
 const pinnedRun=useRef(false),runDetailRequest=useRef(0);
 const [schedule,setSchedule]=useState<SavedSchedule|null>(null),[source,setSource]=useState<Source|null>(null),[jobs,setJobs]=useState<Job[]>([]),[scheduleNotice,setScheduleNotice]=useState('');
 const sourceInput=useRef<HTMLInputElement>(null);
 const resultPanel=useRef<HTMLDivElement>(null);
 const runFileInput=useRef<HTMLInputElement>(null);
 const fileInput=useRef<HTMLInputElement>(null),end=useRef<HTMLDivElement>(null),input=useRef<HTMLTextAreaElement>(null),inFlight=useRef(false),generation=useRef(0);
 useEffect(()=>()=>{generation.current++;},[]);
 const [attachMode,setAttachMode]=useState<'choose'|'library'|null>(null);
 const [saveReviewOpen,setSaveReviewOpen]=useState(false),[includeSchedule,setIncludeSchedule]=useState(true);
 async function refresh(){const [a,n,t,l]=await Promise.all([request('/api/agent-builder/agents'),request('/api/agent-builder/nodes'),request('/api/agent-builder/catalog'),request('/api/agent-builder/connection?profile=1')]);setLibrary(n.nodes);setPresets(n.defaults);setCatalog(t.tools.map((tool:ToolInfo)=>({...tool,description:toolDescription(tool.id,tool.description)})));setAgents(a.agents);setModel(l.connection?.model||'');setCredentialId(l.connection?.credential.id||'');}

 useEffect(()=>{
  let cancelled=false;
  const token=generation.current;
  const current=()=>!cancelled&&generation.current===token;
  const serviceId=new URLSearchParams(location.search).get('service');
  const sessionId=localStorage.getItem('a4b-builder-session');
  const selectedId=localStorage.getItem('a4b-selected-agent');
  async function restoreAgent(id:string){
   const result=await request('/api/agent-builder/agent?id='+encodeURIComponent(id));
   if(!current())return;
   setSelected(result.agent);setSession(null);applyRunInputs(result);
   setSchedule(result.schedule);setSource(result.source);setJobs(result.jobs||[]);
   const linkedRun=new URLSearchParams(location.search).get('run');
   let restored=linkedRun?(result.tests||[]).find((r:TestRun)=>r.id===linkedRun):result.tests[0];
   if(restored?.public_projection?.history_preview){
    const detail=await request('/api/agent-builder/run?id='+encodeURIComponent(restored.id));
    if(!current())return;
    restored=detail.run;
   }
   setTestRun(restored||null);setHistory(result.tests||[]);pinnedRun.current=!!linkedRun;
   if(linkedRun&&!restored)setError('지정한 실행 기록을 찾을 수 없습니다. 실행 기록에서 다시 선택하세요.');
   localStorage.setItem('a4b-selected-agent',id);
   localStorage.removeItem('a4b-builder-session');
  }
  void (async()=>{
   try{
    await refresh();
    if(!current())return;
    // An explicit service link takes precedence over a remembered conversation.
    if(serviceId)await restoreAgent(serviceId);
    else if(sessionId){
     const s=await request('/api/agent-builder/session?id='+encodeURIComponent(sessionId));
     if(!current())return;
     if(s.status==='saved'&&s.saved_agent_id)await restoreAgent(s.saved_agent_id);
     else setSession(s);
    }else if(selectedId)await restoreAgent(selectedId);
   }catch(e){if(current()){setInitialLoadFailed(true);setError((e as Error).message);}}
   finally{if(!cancelled)setLoading(false);}
  })();
  return()=>{cancelled=true;};
 },[]);
 useEffect(()=>{const messages=end.current?.closest<HTMLElement>('.as-messages');if(messages&&(session?.messages.length||pendingMessage))messages.scrollTo?.({top:messages.scrollHeight,behavior:'smooth'});},[session?.messages.length,pendingMessage,busy]);
 function clearRunInputs(){setRunFiles([]);setReplyDraft('');setTestText('');setScheduleNotice('');pinnedRun.current=false;}
 function remember(s:Session){setSession(s);localStorage.setItem('a4b-builder-session',s.id);}
 async function recoverConversation(attempt:ConversationRecovery):Promise<Session|null>{
  const token=generation.current;
  if(attempt.fromComposer)setText(attempt.draftText??attempt.content??'');
  setRecovery(attempt);
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),15000);
  try{
   // Read history first: a running worker may publish its session just afterwards.
   const history=await request<{requests:GenerationRequest[]}>('/api/agent-builder/generation_history?id='+encodeURIComponent(attempt.before.id),undefined,undefined,controller.signal);
   let latest=await request<Session>('/api/agent-builder/session?id='+encodeURIComponent(attempt.before.id),undefined,undefined,controller.signal);
   if(token!==generation.current)return null;
   let repairActive=false;
   const outcome=history.requests.find(item=>item.request_id===attempt.requestId&&item.action===attempt.action
    &&item.session_revision===attempt.before.revision);
   if(attempt.action==='confirm'&&outcome?.status==='succeeded'&&outcome.workflow_id&&latest.status!=='saved'){
    const repair=await request<{recovered:boolean;reason?:string}>('/api/agent-builder/recover_generation',
     {id:attempt.before.id,revision:latest.revision,generationRequestId:attempt.requestId},undefined,controller.signal);
    repairActive=repair.reason==='active';
    latest=await request<Session>('/api/agent-builder/session?id='+encodeURIComponent(attempt.before.id),undefined,undefined,controller.signal);
    if(token!==generation.current)return null;
    if(repair.reason==='changed'){
     remember(latest);setRecovery(null);
     setError('다른 요청에서 대화가 변경됐습니다. 최신 대화를 확인한 뒤 이어서 진행하세요.');
     return null;
    }
    if(!repair.recovered&&!repairActive)throw new Error('저장 결과 복구를 확인하지 못했습니다.');
   }
   remember(latest);
   const applied=latest.last_generation_request_id===attempt.requestId&&latest.revision>attempt.before.revision;
   const successful=!!outcome&&['waiting_input','waiting_confirmation','succeeded'].includes(outcome.status);
   const resultRevision=outcome?.result_revision;
   const recorded=successful&&typeof resultRevision==='number'&&Number.isInteger(resultRevision)
    &&resultRevision>attempt.before.revision&&latest.revision>=resultRevision;
   const published=applied&&['ready','needs_clarification','saved'].includes(latest.status)
    &&(!outcome||outcome.status==='running'||successful);
   const delivered=attempt.action==='confirm'
    ? latest.status==='saved'&&!!latest.saved_agent_id&&(published||successful&&outcome?.workflow_id===latest.saved_agent_id)
    : recorded||published;
   // Session publication can complete before the audit record is finalized.
   if(!delivered&&(outcome?.status==='running'||repairActive)){
    const pollCount=(attempt.pollCount??0)+1;
    const running=pollCount<15;
    setRecovery({...attempt,running,pollCount:running?pollCount:0});
    setError(running?'서버에서 이전 요청을 처리하고 있습니다. 완료되면 대화를 갱신합니다.':
     '서버의 처리 완료를 아직 확인하지 못했습니다. 이 대화 이어가기로 작업 종료를 확인하거나 다른 화면으로 이동할 수 있습니다. 중복 전송을 막기 위해 입력은 보존합니다.');
    return null;
   }
   if(attempt.fromComposer)setText(delivered?'':attempt.draftText??attempt.content??'');
   setRecovery(null);
   setError(outcome&&['failed','blocked'].includes(outcome.status)?outcome.reason||attempt.error:delivered?'':attempt.error);
   return attempt.action==='confirm'&&!delivered?null:latest;
  }catch{
   if(token===generation.current){
    setRecovery({...attempt,running:false});
    setError(attempt.error+' 대화 상태를 확인하지 못했습니다. 다시 불러온 뒤 이어서 진행하세요.');
   }
   return null;
  }finally{clearTimeout(timer);}
 }
 async function reloadConversation(){
  if(!recovery||inFlight.current)return;
  const attempt=recovery;
  inFlight.current=true;setBusy('서버의 대화 상태를 확인하고 있습니다.');
  let latest:Session|null=null;
  try{latest=await recoverConversation(attempt);}
  finally{inFlight.current=false;setBusy('');}
  if(attempt.action==='confirm'&&latest?.status==='saved'&&latest.saved_agent_id){if(attempt.includeSchedule)go('schedule');await openAgent({id:latest.saved_agent_id});}
 }
 async function resumeConversation(){
  if(!recovery||recovery.running||inFlight.current||!session)return;
  const attempt=recovery,token=generation.current;
  inFlight.current=true;setBusy('이전 작업이 종료됐는지 확인하고 있습니다.');
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
  let latest:Session|null=null;
  try{
   const result=await request<{recovered:boolean;reason?:string}>('/api/agent-builder/recover_generation',
    {id:attempt.before.id,revision:session.revision,generationRequestId:attempt.requestId},undefined,controller.signal);
   if(token!==generation.current)return;
   if(result.reason==='active'){
    setError('서버에서 이전 작업을 처리 중입니다. 작업이 끝난 뒤 다시 확인하세요.');
    return;
   }
   latest=await recoverConversation({...attempt,pollCount:14});
  }catch(e){if(token===generation.current)setError('이전 작업의 종료를 확인하지 못했습니다. 입력은 보존됩니다. '+(e as Error).message);}
  finally{clearTimeout(timer);inFlight.current=false;setBusy('');}
  if(attempt.action==='confirm'&&latest?.status==='saved'&&latest.saved_agent_id){if(attempt.includeSchedule)go('schedule');await openAgent({id:latest.saved_agent_id});}
 }
 useEffect(()=>{
  if(!recovery?.running)return;
  const timer=window.setTimeout(()=>void reloadConversation(),2000);
  return()=>window.clearTimeout(timer);
 },[recovery]);
 function updateReviewedAsset(asset:ReviewableAsset){
  const next=asset as Resource;
  setResource(next);setSession(prior=>prior?{...prior,resources:prior.resources.map(item=>item.id===next.id?next:item)}:prior);
  setAgentResources(prior=>prior.map(item=>item.id===next.id?next:item));setRunFiles(prior=>prior.map(item=>item.id===next.id?next:item));
  setSource(prior=>prior?.asset.id===next.id?{...prior,asset:next}:prior);
 }
 function reset(){if(inFlight.current||recovery?.running)return;setRecovery(null);externalPromptContext.current='';setExternalMessages([]);go('workflow');window.scrollTo({top:0,behavior:'instant'});setChosenRefs([]);setChosenSnapshots([]);setSelectedNodeId(null);setEditorPanel('conversation');generation.current++;setSelected(null);setSession(null);setHistory([]);pinnedRun.current=false;setRunFiles([]);setSchedule(null);setSource(null);setJobs([]);setQueuedSourceRuns([]);setSaveReviewOpen(false);setScheduleSourceDirty(false);setScheduleSettingsDirty(false);setClarificationDirty(false);setScheduleNotice('');setText('');setTestText('');setTestRun(null);setAgentResources([]);setError('');localStorage.removeItem('a4b-builder-session');localStorage.removeItem('a4b-selected-agent');const url=new URL(location.href);url.searchParams.delete('service');url.searchParams.delete('run');url.searchParams.delete('executionMode');window.history.replaceState(window.history.state,'',url);setReplyDraft('');requestAnimationFrame(()=>input.current?.focus({preventScroll:true}));}

 async function ensureSession(){generation.current++;if(session)return session;if(!credentialId)throw new Error('설계 서비스를 준비하지 못했습니다. 잠시 후 다시 시도해주세요.');const s=await request('/api/agent-builder/sessions',{credentialId,model,nodeRefs:chosenRefs});return {...s,messages:[],draft:null,status:'empty',issues:[],trace:[],resources:[]} as Session;}
 async function send(event?:FormEvent,choice?:string,questionId?:string,answers?:ClarificationAnswer[]){
  event?.preventDefault();
  const draftText=text;
  const content=(choice??draftText).trim();
  if(inFlight.current||recovery||!content)return;
  const requestId=crypto.randomUUID();
  inFlight.current=true;
  const fromComposer=choice===undefined;
  if(fromComposer)setText('');
  setPendingMessage(content);
  setBusy('업무를 이해하고 설정을 구성하고 있습니다.');
  setError('');
  let sentSession:Session|null=null;
  try{
   const gmailSlackRequest=/(?:Gmail|지메일)/i.test(content)&&/(?:Slack|슬랙)/i.test(content);
   if(!gmailSlackRequest&&(externalPromptContext.current||/Google Sheets|구글 ?시트|스프레드시트|Slack|슬랙|외부 ?API|Webhook|웹훅|REST API|API ?연동|API에서/i.test(content))){
    const prompt=externalPromptContext.current?`${externalPromptContext.current}\n추가 답변: ${content}`:content;
    const result=await request<{workflow?:{id:string};summary?:string;questions?:{question:string}[];checklist?:string[];requirements?:{id:string;description:string;status:string}[]}>('/api/workflows/from-request',{prompt});
    if(!result.workflow){externalPromptContext.current=prompt;setExternalMessages(prior=>[...prior,{role:'user',content},{role:'assistant',content:explainIncompleteWorkflow(result,prompt)}]);return;}
    externalPromptContext.current='';
    setExternalMessages([]);
    onExternalFlow?.(result.workflow.id);
    return;
   }
   if(gmailSlackRequest&&externalPromptContext.current){externalPromptContext.current='';setExternalMessages([]);}
   const s=await ensureSession();
   sentSession=s;
   remember(s);
   const userTimezone=Intl.DateTimeFormat().resolvedOptions().timeZone;
   const result=await request('/api/agent-builder/message',{id:s.id,revision:s.revision,content,userTimezone,generationRequestId:requestId,...(answers?.some(answer=>answer.questionId)?{answers:answers.filter(answer=>answer.questionId).map(({questionId,value,custom,skip})=>({questionId,value,...(custom?{custom:true}:{}),...(skip?{skip:true}:{})}))}:{}),...(questionId?{questionId}:{})});
   remember(result);
   setFreshFiles({session:'',ids:[]});  // the message has now used every attached file
  }catch(e){
   let restored:Session|null=null;
   if(sentSession)restored=await recoverConversation({before:sentSession,requestId,action:'message',content,draftText,fromComposer,error:(e as Error).message,running:false});
   else{if(fromComposer)setText(draftText);setError((e as Error).message);}
   if(answers&&!restored)throw e;

  }finally{
   inFlight.current=false;
   setPendingMessage('');
   setBusy('');
  }
 }
 useEffect(()=>{
  if(!initialFileReady||!initialPromptPending.current||loading||initialLoadFailed||!credentialId||screen!=='workflow')return;
  initialPromptPending.current=false;
  void send();
 },[loading,initialLoadFailed,credentialId,screen,initialFileReady,session]);
 useEffect(()=>{
  if(!initialFilePending.current||loading||initialLoadFailed||!credentialId||screen!=='workflow')return;
  const file=initialFilePending.current;initialFilePending.current=undefined;
  const {initialFile:consumed,...historyState}=window.history.state||{};
  window.history.replaceState(historyState,'',window.location.href);
  void attachResource(file).then(()=>setInitialFileReady(true));
 },[loading,initialLoadFailed,credentialId,screen]);
 async function answerQuestions(answers:ClarificationAnswer[]){
  const content=answers.map(answer=>answer.text+' → '+(answer.skip?'선택사항 건너뛰기':Array.isArray(answer.value)?answer.value.join(', '):String(answer.value))).join('\n');
  if(content.length>4000)throw new Error('답변은 전체 4,000자까지 보낼 수 있습니다. 내용을 줄여주세요.');
  await send(undefined,content,undefined,answers);
 }
 // Files attached since the last message, per session: only these get a summary mid-conversation.
 const [freshFiles,setFreshFiles]=useState<{session:string;ids:string[]}>({session:'',ids:[]});
 async function attachResource(source:File|string) {
  if(inFlight.current||recovery)return;
  if(typeof source!=='string' && source.size>8*1024*1024){setError('자료는 8MB 이하로 연결하세요.');return;}
  if(typeof source!=='string' && receiptQuestion && !receiptFileTypes.split(',').some(ext => source.name.toLowerCase().endsWith(ext))) {
   setError('영수증 사진이나 PDF 파일을 선택해 주세요.');
   return;
  }
  inFlight.current=true;setBusy(typeof source==='string'?'기존 자료를 설계에 연결하고 있습니다.':'파일의 열과 내용을 확인하고 있습니다.');setError('');
  try {
   const s=await ensureSession();remember(s);
   const sessionRef={id:s.id,revision:s.revision};
   const before=new Set((s.resources||[]).map(resource=>resource.id));
   const result=typeof source==='string'
     ? await request('/api/agent-builder/attach_asset',{...sessionRef,assetId:source})
     : await request('/api/agent-builder/upload',{...sessionRef,fileName:source.name,data:await asBase64(source)});
   remember(result);
   const added=((result.resources||[]) as Resource[]).map(resource=>resource.id).filter(id=>!before.has(id));
   setFreshFiles(prior=>({session:result.id,ids:[...(prior.session===result.id?prior.ids:[]),...added]}));
  } catch(e){initialPromptPending.current=false;setError((e as Error).message);}
  finally{inFlight.current=false;setBusy('');}
 }
 function reviewSave(){if(session?.draft?.schedule){setIncludeSchedule(true);setSaveReviewOpen(true);}else void save(false);}
 async function save(includeSchedule=!!session?.draft?.schedule){
  if(!session||session.status==='saved'||inFlight.current||recovery)return;
  const withSchedule=includeSchedule&&!!session.draft?.schedule;
  setSaveReviewOpen(false);
  const requestId=crypto.randomUUID();
  generation.current++;setSelectedNodeId(null);inFlight.current=true;setBusy('검증한 설정을 저장하고 있습니다.');setError('');
  try{
   let s:Session;
   try{s=await request('/api/agent-builder/confirm',{id:session.id,revision:session.revision,generationRequestId:requestId,includeSchedule:withSchedule});}
   catch(e){
    const latest=await recoverConversation({before:session,requestId,action:'confirm',includeSchedule:withSchedule,error:(e as Error).message,running:false});
    if(!latest||latest.status!=='saved'||!latest.saved_agent_id)return;
    s=latest;
   }
   const savedId=s.saved_agent_id;
   if(!savedId)throw new Error('저장 결과를 확인하지 못했습니다. 대화를 다시 불러와 주세요.');
   remember(s);clearRunInputs();
   const savedUrl=new URL(location.href);savedUrl.searchParams.set('service',savedId);window.history.replaceState(window.history.state,'',savedUrl);

   // A successful write must not depend on refreshing unrelated lists.
   const [agentResult,listResult]=await Promise.allSettled([
    request('/api/agent-builder/agent?id='+encodeURIComponent(savedId)),refresh()
   ]);
   if(agentResult.status==='fulfilled'){
    const result=agentResult.value;setSelected(result.agent);applyRunInputs(result);
    setSchedule(result.schedule);setSource(result.source);setJobs(result.jobs||[]);setTestRun(null);setHistory([]);
    go(withSchedule?'schedule':'run');
    localStorage.setItem('a4b-selected-agent',savedId);localStorage.removeItem('a4b-builder-session');
    if(listResult.status==='rejected')setError('저장은 완료됐지만 목록을 갱신하지 못했습니다. '+String(listResult.reason?.message||listResult.reason));
   }else{
    setSelected(null);
    go(withSchedule?'schedule':'run');
    setError('저장은 완료됐지만 워크플로우를 불러오지 못했습니다. 아래 다시 불러오기를 눌러주세요.');
   }
  }catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy('');}
 }

 async function openAgent(agent:Pick<Agent,'id'>){if(inFlight.current)return;setSelectedNodeId(null);const token=++generation.current;setBusy('워크플로우를 불러오고 있습니다.');setError('');try{const result=await request('/api/agent-builder/agent?id='+encodeURIComponent(agent.id));if(token!==generation.current)return;clearSettingsDirty();setSelected(result.agent);go(screen==='schedule'?'schedule':'run');setQueuedSourceRuns([]);applyRunInputs(result);setSchedule(result.schedule);setSource(result.source);setJobs(result.jobs||[]);setTestRun(result.tests[0]||null);setHistory(result.tests||[]);pinnedRun.current=false;setScheduleNotice('');setTestText('');setRunFiles([]);setReplyDraft('');setSession(null);localStorage.setItem('a4b-selected-agent',agent.id);const url=new URL(location.href);url.searchParams.set('service',agent.id);window.history.replaceState(window.history.state,'',url);localStorage.removeItem('a4b-builder-session');}catch(e){if(token===generation.current)setError((e as Error).message);}finally{if(token===generation.current)setBusy('');}}
 async function edit(){if(!selected||inFlight.current||!confirmSettingsExit())return;clearSettingsDirty();generation.current++;setSelectedNodeId(null);inFlight.current=true;setBusy('설정 수정 대화를 시작합니다.');setError('');try{const s=await request('/api/agent-builder/sessions',{credentialId:selected.credential_id,model:selected.model,agentId:selected.id});remember({...s,messages:[],draft:selected.config,status:'empty',issues:[],trace:[],resources:agentResources});const url=new URL(location.href);url.searchParams.delete('service');window.history.replaceState(window.history.state,'',url);clearRunInputs();setSelected(null);setTestRun(null);setEditorPanel('conversation');go('workflow');requestAnimationFrame(()=>input.current?.focus());}catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy('');}}

 const requestRequired = runInputs.request_required ?? !selected?.config.schedule;
 const scheduleRequestRequired = runInputs.schedule_request_required ?? requestRequired;
 const sourceFreeSchedule = !!schedule && runInputs.source_required === false;
 const gmailRequired = !!runInputs.required_connections?.includes('gmail');
 const slackRequired = !!runInputs.required_connections?.includes('slack');
 const slackConnected = !!selected && slackConnection?.agentId===selected.id && slackConnection.connected;
 const uncertainSlackDelivery = testRun?.error_type==='SlackDeliveryUnknown' || (testRun?.error_type==='InterruptedExecution' && slackRequired);
 useEffect(()=>{
  if(!slackRequired||!selected)return;
  let active=true;
  const agentId=selected.id;
  async function refreshSlack(){
   try{
    const result=await request<{credentials:{provider:string;service?:string}[]}>('/api/credentials');
    if(active)setSlackConnection({agentId,connected:result.credentials.some(c=>c.provider==='slack'&&(c.service==='slack'||!c.service))});
   }catch{if(active)setSlackConnection({agentId,connected:false});}
  }
  void refreshSlack();window.addEventListener('focus',refreshSlack);
  return()=>{active=false;window.removeEventListener('focus',refreshSlack);};
 },[slackRequired,selected?.id]);
 const gmailSchedule = sourceFreeSchedule && gmailRequired;
 const scheduleSourceReady = (!!runInputs.library_source || (sourceFreeSchedule ? (!gmailSchedule || gmailConnected) : !!source))&&(!slackRequired||slackConnected);
 const fileRequirements = runInputs.input_requirements || [];
 const requiredFiles = fileRequirements.filter(item => item.required_before_start);
 const mixedFileKinds = new Set(fileRequirements.map(item => item.kind)).size > 1;
 const sourceChecked = source && fileRequirements.some(item => item.file_ids?.includes(source.asset_id));
 const effectiveUploads = screen==='schedule' ? (source && !sourceChecked ? [source.asset] : []) : runFiles.length?runFiles:(source && !sourceChecked?[source.asset]:[]);
 const missingFile = requiredFiles.find(item => {
  if(runInputs.library_source&&(screen==='schedule'||!runFiles.length)&&item.node_id===runInputs.library_source.target_node_id)return false;

  if (!effectiveUploads.length) return !item.ready;
  const hasFile = effectiveUploads.some(file => file.kind === item.kind || file.capabilities?.includes(item.kind) ||
   (item.kind === 'document' && file.parser_metadata?.document_analysis?.contract_version === 'a4b.document_analysis.v1' && file.text?.trim()));
  return !hasFile && !(mixedFileKinds && item.ready);
 });
 const savedRunFileIds = new Set(requiredFiles.filter(item => item.ready).flatMap(item => item.file_ids || []));
 const savedRunFileNames = [...savedRunFileIds].map(id =>
  (source?.asset.id === id ? source.asset : agentResources.find(file => file.id === id))?.name
 ).filter(Boolean);
 const fileHint = missingFile
  ? (missingFile.issues?.[0] || (missingFile.kind === 'document' ? '실행할 문서를 첨부해 주세요.' : '실행할 표 파일을 첨부해 주세요.'))
  : runFiles.length && runInputs.library_source
   ? '이번에는 첨부한 파일을 사용합니다. 저장한 자료함 연결은 유지됩니다.'
  : !runFiles.length && runInputs.library_source
   ? '연결한 자료함에서 읽습니다. 이번 실행 자료를 추가하면 자료함 대신 첨부한 파일을 사용합니다.'
  : !runFiles.length && savedRunFileNames.length
   ? '저장된 자료: ' + savedRunFileNames.join(', ') + '. 새 파일을 첨부하면 해당 종류의 자료를 교체합니다.'
   : !runFiles.length && selected?.config.document_ids.some(id=>!agentResources.some(asset=>asset.id===id))
   ? '기존 참고 파일이 삭제됐습니다. 해당 파일이 필요한 실행에는 새 자료를 추가하세요.'
   : '새 자료를 넣으면 이번 실행에 사용합니다.';
 const canStartRun = !scheduleSourceDirty && !(screen==='schedule'&&scheduleSettingsDirty) && !missingFile && (screen==='schedule'?!scheduleRequestRequired:!requestRequired||!!testText.trim()) && (!gmailRequired || gmailConnected) && (!slackRequired || slackConnected);

 const sourceReaders=fileRequirements.filter(item=>item.node_id&&selected?.config.workflow?.nodes.some(node=>node.id===item.node_id&&!node.settings.operation&&['read_table','read_document'].includes(node.tool))).map(item=>({id:item.node_id!,kind:item.kind,label:selected?.config.workflow?.nodes.find(node=>node.id===item.node_id)?.label||item.description||(item.kind==='table'?'표 파일 읽기':'문서 읽기')}));
 async function refreshScheduleSource(){
  if(!selected)return;
  const id=selected.id,token=generation.current,result=await request('/api/agent-builder/agent?id='+encodeURIComponent(id));
  if(token!==generation.current)return;
  applyRunInputs(result);setSchedule(result.schedule);setSource(result.source);setJobs(result.jobs||[]);
 }
 const librarySourceSettings=selected&&sourceReaders.length>0?<ScheduleLibrarySourceSettings key={selected.id+':'+screen} workflowId={selected.id} readers={sourceReaders} disabled={!!busy||!!recovery||libraryBusy} onDirtyChange={setScheduleSourceDirty} onBusyChange={value=>{inFlight.current=value;setBusy(value?"자료함 연결을 저장합니다.":"");}} onSaved={async changed=>{if(changed)setQueuedSourceRuns([]);await refreshScheduleSource();}}/>:null;

 async function runTest(){
  if(!selected||inFlight.current||!canStartRun)return;
  inFlight.current=true;setBusy('저장한 워크플로우를 실행하고 있습니다.');setError('');
  try{
   pinnedRun.current=false;
   const result=await request('/api/agent-builder/test',{
    id:selected.id,content:screen==='schedule'?'':testText.trim()?testText:'',fileIds:screen==='schedule'?[]:runFiles.map(f=>f.id),
    replyDraft:screen==='schedule'?undefined:replyDraft||undefined,...(screen==='schedule'?{useSchedule:true}:{})
   });
   if(result.status==='expanded'){
    pinnedRun.current=!(result.job_ids?.length);
    setQueuedSourceRuns(result.job_ids||[]);setTestRun(null);
    setScheduleNotice('새 파일 '+(result.enqueued_count||0)+'개를 실행 대기열에 등록했습니다. 처리 결과는 실행 기록에 표시됩니다.');
    await refreshScheduleSource();return;
   }
   if(result.status==='skipped'){
    pinnedRun.current=true;
    setQueuedSourceRuns([]);setTestRun(null);setScheduleNotice(result.message||'새로 처리할 파일이 없습니다.');
    await refreshScheduleSource();return;
   }
   setQueuedSourceRuns([]);
   setTestRun(result);setHistory(prior=>[result,...prior.filter(r=>r.id!==result.id)]);
   requestAnimationFrame(()=>resultPanel.current?.scrollIntoView({behavior:'smooth',block:'start'}));if(result.question)setTestText('');
  }catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy('');}
 }
 const refreshRuns=shouldRefreshAgentRuns(!!schedule,testRun,history);
 useEffect(()=>{
  if(!selected||busy||!refreshRuns)return;
  const id=selected.id,token=generation.current;
  return startAgentRunRefresh(async()=>{
   const result=await request('/api/agent-builder/agent?id='+encodeURIComponent(id));
   if(pinnedRun.current&&testRun?.id&&(result.tests||[]).some((run:TestRun)=>run.id===testRun.id&&run.public_projection?.history_preview)){
    const detail=await request('/api/agent-builder/run?id='+encodeURIComponent(testRun.id));
    result.tests=result.tests.map((run:TestRun)=>run.id===detail.run.id?detail.run:run);
   }
   return result;
  },result=>{
   setSelected(prior=>prior?.id===id&&prior.version!==result.agent.version?result.agent:prior);
   applyRunInputs(result);setSchedule(result.schedule);setSource(result.source);setJobs(result.jobs||[]);
   const queued=queuedSourceRuns.length?(result.tests||[]).find((run:TestRun)=>queuedSourceRuns.includes(run.id)):null;
   if(queued)setQueuedSourceRuns([]);
   setHistory(result.tests||[]);setTestRun(prior=>queuedSourceRuns.length?queued||prior:refreshedAgentRun(result.tests||[],prior,pinnedRun.current));
  },()=>inFlight.current||generation.current!==token);
 },[selected?.id,refreshRuns,busy,testRun?.id,queuedSourceRuns]);
 async function uploadSource(file:File){if(!selected||inFlight.current)return;if(file.size>8*1024*1024){setError('자료는 8MB 이하로 연결하세요.');return;}inFlight.current=true;setBusy('다음 실행에 사용할 데이터 소스를 갱신합니다.');setError('');try{const result=await request('/api/agent-builder/source_upload',{id:selected.id,fileName:file.name,data:await asBase64(file)});setSource(result.source);setScheduleNotice('소스 버전 '+result.source.version+' 저장 · 다음 실행부터 반영됩니다.');}catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy('');if(sourceInput.current)sourceInput.current.value='';}}

 async function testSchedule(){if(!selected||inFlight.current)return;inFlight.current=true;setBusy('시험 예약을 등록합니다.');setError('');try{const result=await request('/api/agent-builder/schedule_test',{id:selected.id});setJobs(prior=>[{...result.job,status:'queued'},...prior]);setScheduleNotice('15초 후 시험 예약을 등록했습니다. 서버 타이머가 실행하며 결과가 자동 갱신됩니다.');}catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy('');}}
 async function cancelSchedule(jobId:string){if(!selected||inFlight.current)return;inFlight.current=true;setBusy('시험 예약을 취소합니다.');setError('');try{const result=await request('/api/agent-builder/schedule_cancel',{id:selected.id,jobId});setJobs(prior=>prior.map(j=>j.id===jobId?result.job:j));setScheduleNotice('시험 예약을 취소했습니다.');}catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy('');}}
 async function uploadRun(file:File){if(!selected||inFlight.current)return;if(file.size>8*1024*1024){setError('자료는 8MB 이하로 연결하세요.');return;}if(runFiles.length>=10){setError('실행 자료는 최대 10개입니다.');return;}inFlight.current=true;setBusy('이번 실행 자료를 읽고 있습니다.');setError('');try{const result=await request('/api/agent-builder/run_upload',{id:selected.id,fileName:file.name,data:await asBase64(file)});setRunFiles(prior=>[...prior,result.asset]);}catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy('');if(runFileInput.current)runFileInput.current.value='';}}
 async function resumeRun(){if(!testRun||inFlight.current||!testText.trim())return;inFlight.current=true;setBusy('확인 답변으로 같은 실행을 이어갑니다.');setError('');try{const result=await request('/api/agent-builder/resume_run',{id:testRun.id,content:testText});setTestRun(result);setHistory(prior=>[result,...prior.filter(r=>r.id!==result.id)]);requestAnimationFrame(()=>resultPanel.current?.scrollIntoView({behavior:'smooth',block:'start'}));setTestText('');}catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy('');}}
 async function selectRun(id:string){
  if(inFlight.current)return;
  setQueuedSourceRuns([]);
  const token=generation.current,requestId=++runDetailRequest.current;
  pinnedRun.current=true;setTestRun(history.find(r=>r.id===id)||null);
  const current=()=>token===generation.current&&requestId===runDetailRequest.current&&pinnedRun.current&&!inFlight.current;
  try{const {run}=await request('/api/agent-builder/run?id='+encodeURIComponent(id));if(current())setTestRun(prior=>prior?.id===id?run:prior);}
  catch(e){if(current())setError((e as Error).message);}
 }
 async function decideReply(reviewId:string,decision:'approve'|'reject',editedReply:string){
  if(!testRun||inFlight.current)return;inFlight.current=true;setBusy('응대 결정과 최종 문장을 저장합니다.');setError('');
  try{const result=await request('/api/agent-builder/review_decision',{id:testRun.id,reviewId,decision,editedReply});setTestRun(result);setHistory(prior=>prior.map(r=>r.id===result.id?result:r));}catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy('');}
 }
 async function retryRun(){if(!testRun||inFlight.current)return;inFlight.current=true;setBusy('저장된 결과로 실패한 단계부터 재시도합니다.');setError('');try{const result=await request('/api/agent-builder/retry_run',{id:testRun.id});setTestRun(result);setHistory(prior=>prior.map(r=>r.id===result.id?result:r));}catch(e){setError((e as Error).message);}finally{inFlight.current=false;setBusy('');}}

 function canLeave(){if(inFlight.current||recovery?.running||libraryBusy)return false;return !(text.trim()||testText.trim()||replyDraft.trim()||runFiles.length||chosenRefs.length||libraryDirty||scheduleSourceDirty||scheduleSettingsDirty||clarificationDirty)||window.confirm('아직 보내지 않은 입력 또는 작업 선택이 있습니다. 이 화면을 나가면 입력이 사라집니다. 이동할까요?');}

 useEffect(()=>{onCanLeaveChange?.(canLeave);});
 function openDesignHome(){
  if(screen==='workflow'&&!selected)return;
  if(canLeave()){if(session&&!selected)go('workflow',true);else reset();}
 }
 function leave(next:'home'|'workflows'|'schedules'|'history'|'assets'|'connections'){if(canLeave())onNavigate?.(next);}
 useEffect(()=>{
  if(!(text.trim()||testText.trim()||replyDraft.trim()||runFiles.length||chosenRefs.length||libraryDirty||scheduleSourceDirty||scheduleSettingsDirty||clarificationDirty))return;
  const beforeUnload=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};
  window.addEventListener('beforeunload',beforeUnload);return()=>window.removeEventListener('beforeunload',beforeUnload);
 },[text,testText,replyDraft,runFiles.length,chosenRefs.length,libraryDirty,scheduleSourceDirty,scheduleSettingsDirty,clarificationDirty]);

 const draft=selected?.config||session?.draft,resources=selected?(source?[source.asset,...agentResources.filter(r=>r.kind==='document')]:agentResources):session?.resources||[],ready=session?.status==='ready',saved=!!selected||session?.status==='saved';
 const collecting=session?.status==='needs_clarification'&&!draft?.workflow&&!!draft?.questions.length&&!draft?.tool_ids.length;
 const lastAssistant = session?.messages.map(message => message.role).lastIndexOf('assistant');
 const receiptQuestion = !saved ? session?.pending_questions?.find(q => q.action?.type === 'attach_receipt' || q.action?.type === 'select_fields') : undefined;
 const clarificationQuestions:ClarificationQuestionData[]=saved||receiptQuestion?[]:session?.pending_questions?.length
  ? session.pending_questions.map(question=>{const index=session.draft?.questions.indexOf(question.text)??-1;return {...question,response:question.response||session.draft?.question_responses?.[index]||undefined};})
  : (session?.draft?.questions||[]).map((question,index)=>({text:question,options:session?.draft?.question_options?.[index],response:session?.draft?.question_responses?.[index]||undefined}));
  const slackChannelQuestion=clarificationQuestions.find(question=>question.key==='slack.channel_id'||/(?:Slack|슬랙).{0,20}채널/i.test(question.text));
 // Only draw the version that actually ran when browsing history.
 const runWorkflow = testRun?.config_snapshot && 'workflow' in testRun.config_snapshot
  ? testRun.config_snapshot.workflow
  : !testRun || selected?.version === testRun.agent_version ? selected?.config.workflow : undefined;
 const resultFile = testRun && (testRun.artifact || testRun.report_path) ? {
  ...testRun.artifact, name:suggestedRunFilename(testRun.artifact?.name || testRun.report_path!, testRun.config_snapshot?.name || selected?.config.name)
 } : null;
 const unverifiedFields = [...new Set((testRun?.rows || [])
  .filter(row => row.status === 'not_found_or_unverified' && typeof row.field === 'string')
  .map(row => String(row.field)))];
 const reviewCapable=!!selected&&usesTool(selected,'reply_review');
 const settingsPanel=useRef<HTMLElement>(null);
 const selectedNode=draft?.workflow?.nodes.find(n=>n.id===selectedNodeId)||null;
 useEffect(()=>{
  if(screen!=='workflow'||!editorGrid.current)return;
  const grid=editorGrid.current;
  const fit=()=>{
   const available=Math.max(260,Math.min(900,window.innerHeight-Math.max(0,grid.getBoundingClientRect().top)-20));
   const height=available+'px';
   if(grid.style.getPropertyValue('--as-editor-height')!==height)grid.style.setProperty('--as-editor-height',height);
  };
  fit();
  const observer=new ResizeObserver(fit);
  if(grid.parentElement)observer.observe(grid.parentElement);
  window.addEventListener('resize',fit);
  return()=>{observer.disconnect();window.removeEventListener('resize',fit);};
 },[screen,loading,error,busy,selected?.id,session?.base_version]);
 const panelLabels = [
  ['conversation', selected ? '설계 요약' : '업무 대화'],
  ['canvas', '처리 흐름'],
  ['settings', '설정'],
 ] as const;
 const runRequestField=<label className="as-field">{testRun?.question?'확인 질문에 답변':reviewCapable?(requestRequired?'고객 문의':'고객 문의 (선택 · 표의 문의 열 사용)'):requestRequired?'실행 요청':'추가 요청 (선택)'}<textarea placeholder={testRun?.question?"확인 질문에 답변하세요.":requestRequired?"저장한 워크플로우에 맡길 요청이나 질문을 입력하세요.":"비워 두면 저장한 설정으로 실행합니다."} data-tour="run-input" value={testText} onChange={e=>setTestText(e.target.value)} maxLength={4000} disabled={!!busy||!!recovery||libraryBusy}/>{requestRequired&&!testRun?.question&&<small>이 워크플로우는 이번에 처리할 {reviewCapable?'고객 문의':'질문이나 요청'}가 필요합니다.</small>}</label>;
 const pageNavigation=<div className="workspace-context"><button className="as-text-button" disabled={!!busy||!!recovery||libraryBusy} onClick={()=>leave(screen==='schedule'?'schedules':'workflows')}><ArrowLeft size={15}/>목록으로</button><nav className="studio-tabs" aria-label="워크플로우 작업"><button aria-current={screen==='workflow'?'page':undefined} className={screen==='workflow'?'active':''} onClick={()=>switchScreen('workflow')} disabled={!!busy||!!recovery||libraryBusy}>설계</button><button data-tour="run-tab" aria-current={screen==='run'?'page':undefined} className={screen==='run'?'active':''} onClick={()=>switchScreen('run')} disabled={!!busy||!!recovery||libraryBusy}>직접 실행</button>{selected&&<button aria-current={screen==='schedule'?'page':undefined} className={screen==='schedule'?'active':''} onClick={()=>switchScreen('schedule')} disabled={!!busy||!!recovery||libraryBusy}>예약 관리</button>}</nav>{screen==='workflow'&&<button type="button" data-tour="create-workflow" className="as-button as-primary as-new-workflow" disabled={loading||!!busy||!!recovery||libraryBusy} onClick={()=>{if(canLeave())reset();}}><Plus size={16}/>새 워크플로우</button>}</div>;

 return <div className={"as-app screen-"+screen+(selected?" has-service":"")}>
  {attachMode && !saved && screen==='workflow' && <AttachUploadDialog mode={attachMode} onClose={()=>setAttachMode(null)}
    connected={resources.map(r=>r.id)} formats={receiptQuestion ? receiptFileTypes : undefined}
    onLibrary={()=>setAttachMode('library')} onNew={()=>{setAttachMode(null);fileInput.current?.click();}}
    onSelect={id=>{setAttachMode(null);void attachResource(id);}}/>}
  <WorkspaceHeader onHome={()=>leave('home')} disabled={loading||!!busy||!!recovery||libraryBusy}/>
  {error&&<div className="as-error as-global-error" role="alert"><AlertCircle size={17}/><span>{error}</span>{recovery&&<button type="button" className="as-button" disabled={!!busy} onClick={()=>void reloadConversation()}>대화 다시 불러오기</button>}{recovery&&!recovery.running&&<button type="button" className="as-button as-secondary" disabled={!!busy} onClick={()=>void resumeConversation()}>이 대화 이어가기</button>}<button disabled={!!recovery} onClick={()=>setError('')} aria-label="오류 안내 닫기"><X size={16}/></button></div>}
  <div className="as-shell"><WorkspaceSidebar active={screen==='nodes'?'workflows':screen==='schedule'?'schedules':'workflow'} disabled={!!busy||!!recovery?.running||libraryBusy} help={<WorkspaceHelp currentTourPanel={editorPanel} onTourPanelChange={showEditorPanel} deferAutoStart={loading||!!busy||!!recovery||libraryBusy}/>} onNavigate={next=>{if(next==='workflow')openDesignHome();else leave(next);}}></WorkspaceSidebar>
  <div style={{display:screen==='nodes'?'contents':'none'}}>{libraryVisited&&<NodeLibrary loading={loading} unavailable={initialLoadFailed} header={<TaskLibraryHeader active="nodes" disabled={!!busy||!!recovery||libraryBusy} onNavigate={next=>{if(next==='workflows')leave(next);}} actions={window.history.state?.returnToDesign&&<button type="button" className="as-text-button" disabled={!!busy||!!recovery||libraryBusy} onClick={()=>switchScreen('workflow')}><ArrowLeft size={15}/>설계로 돌아가기</button>}/>} onDirtyChange={setLibraryDirty} onBusyChange={setLibraryBusy} nodes={library} defaults={presets} catalog={catalog} onSaved={refreshLibrary} onUse={n=>{if(!canLeave())return;reset();setChosenRefs([n.ref]);setChosenSnapshots([n]);go('workflow');}} onToolUse={t=>{if(!canLeave())return;reset();setText(t.name+' 기능을 사용하는 워크플로우를 만들어줘. ');go('workflow');}}/>}</div>{screen!=='nodes'&&<main className="as-main">{pageNavigation}{agents.length>0&&<details className="as-workflow-switcher"><summary>워크플로우 선택</summary><nav data-tour="saved-workflows" aria-label="저장한 워크플로우">{agents.map(a=><button key={a.id} disabled={!!busy||!!recovery||libraryBusy} onClick={()=>{if(canLeave())void openAgent(a);}} className={'as-agent-link '+(selected?.id===a.id?'active':'')}><span className="as-small-icon"><WorkflowIcon size={16}/></span><span><strong>{a.config.name}</strong><small>버전 {a.version} · {a.config.tool_ids.length}개 도구</small></span></button>)}</nav></details>}<div className="as-page-title"><div><h1>{selected?selected.config.name:session?.base_version?'워크플로우 수정':screen==='run'||screen==='schedule'?'실행할 워크플로우를 선택하세요.':'워크플로우 설계'}</h1></div>{selected&&<button data-tour="edit-workflow" className="as-button as-secondary" onClick={edit} disabled={!!busy||!!recovery||libraryBusy}><Settings2 size={16}/>설정 수정</button>}{screen==='workflow'&&!saved&&<div className="as-design-actions">
     <button type="button" className="as-button as-secondary" onClick={()=>go('nodes',true)} disabled={loading||!!busy||!!recovery||libraryBusy}>작업 찾아보기</button>
     <button className="as-button as-primary" onClick={reviewSave} disabled={!ready||!!busy||!!recovery||libraryBusy}><Check size={17}/>{session?.base_version?'새 버전 저장':'저장'}</button>
    </div>}</div>

   {screen==='workflow'&&!saved&&<div className="as-progress" aria-label="설계 진행">
    <span className={draft?'done':'current'}><i>{draft?<Check size={12}/>:'1'}</i>업무 설명</span>
    <span className={draft?'current':''}><i>2</i>설정 확인</span>
    <span><i>3</i>저장</span>
   </div>}
   {(screen==='run'||screen==='schedule')&&!selected&&loading&&<div className="as-loading" role="status"><Loader2 className="as-spin"/>저장한 워크플로우를 불러오고 있습니다.</div>}{(screen==='run'||screen==='schedule')&&!selected&&!loading&&!busy&&!initialLoadFailed&&<div className="nl-empty"><h2>저장한 워크플로우가 필요합니다.</h2><p>{agents.length?'상단에서 실행할 워크플로우를 선택하세요.':'워크플로우 설계 화면에서 먼저 업무를 설명하고 저장하세요.'}</p>{session?.status==='saved'&&session.saved_agent_id?<button className="as-button as-primary" disabled={!!busy||!!recovery||libraryBusy} onClick={()=>void openAgent({id:session.saved_agent_id!})}>저장한 워크플로우 다시 불러오기</button>:<button data-tour="create-workflow" className="as-button as-primary" onClick={()=>switchScreen('workflow')}>워크플로우 설계로 이동</button>}</div>}

    {screen==='workflow'&&!session&&chosenRefs.length>0&&<p className="nl-chosen">선택한 작업: {nodeChoices.filter(n=>chosenRefs.includes(n.ref)).map(n=>n.spec.name+' · v'+n.version).join(' / ')}</p>}
   {busy&&(screen!=='workflow'||selected)&&<div className="as-busy as-operation-progress" role="status"><Loader2 size={17} className="as-spin" aria-hidden="true"/><span>{busy}</span></div>}{screen==='workflow'&&<nav className="as-panel-switcher" aria-label={selected?'설계 보기':'편집 영역'}>
    {panelLabels.map(([id,label])=><button type="button" key={id} aria-pressed={editorPanel===id} onClick={()=>showEditorPanel(id)}>{label}</button>)}
   </nav>}
   <div ref={editorGrid} className="as-editor-grid" data-panel={editorPanel}>
   <section data-tour={screen==='run'?'run-area':'conversation'} className={'as-conversation'+(selected&&screen==='workflow'?' as-saved-design':'')} aria-label={screen==='workflow'?(selected?'설계 요약':'워크플로우 설계 대화'):'서비스 실행'}>
    <div className="as-panel-header"><span><MessageSquare size={17}/>{screen==='workflow'?(selected?'설계 요약':'업무 대화'):screen==='schedule'?'예약 관리':'직접 실행 · 결과'}</span><small>{selected?'설정 버전 '+selected.version:session?'대화 자동 보존':'대화로 설정 만들기'}</small></div>
    {selected&&screen==='workflow'&&<div className="as-design-summary">
      <div><h2>업무 목적</h2><p>{selected.config.purpose}</p></div>
      {selected.config.requirements.length>0&&<div>
        <h2>업무 조건</h2>
        <ul>{selected.config.requirements.map((requirement,index)=><li key={index}>{requirement.description}</li>)}</ul>
      </div>}
    </div>}
    {selected&&slackRequired&&!slackConnected&&<div className="as-small-muted" role="status"><p>{slackConnection?.agentId===selected.id?'Slack 계정을 연결한 뒤 실행하거나 예약할 수 있어요.':'Slack 연결 상태를 확인하고 있어요.'}</p>{slackConnection?.agentId===selected.id&&<button type="button" className="as-text-button" onClick={()=>leave('connections')}>서비스 연결 열기</button>}</div>}
    {selected?<div className="as-test-area"><div className="as-service-input"><div className="as-created"><CheckCircle2 size={20}/><div><h2>{screen==='schedule'?(schedule?"예약 설정과 사용할 자료를 확인하세요.":"이 워크플로우에 예약을 추가하세요."):requestRequired?"실행 요청과 자료":"이번 실행 자료"}</h2>{screen==='schedule'&&<p>일정은 업무 흐름과 별도로 저장됩니다. 예약을 해제해도 직접 실행할 수 있습니다.</p>}</div></div>{screen==='schedule'&&<ScheduleSettings onDirtyChange={setScheduleSettingsDirty} workflowId={selected.id} workflowVersion={selected.version} schedule={schedule} activationDisabled={!scheduleSourceReady||scheduleRequestRequired} disabled={!!busy||!!recovery||libraryBusy} onBusyChange={value=>{inFlight.current=value;setBusy(value?'예약 설정을 저장합니다.':'');}} onChange={async next=>{setSchedule(next as SavedSchedule|null);setQueuedSourceRuns([]);await refreshScheduleSource();}}/>}{screen==='schedule'&&schedule&&<div className="as-schedule-card"><div className="as-flow-heading"><strong><Database size={17}/>예약에 사용할 자료와 시험 실행</strong></div><div className="as-run-actions"><button className="as-button" onClick={testSchedule} disabled={!!busy||!!recovery||libraryBusy||!scheduleSourceReady}>15초 후 시험 예약</button></div>{gmailSchedule?<div className="as-schedule-gmail"><p className="as-small-muted">메일 조회 한도: 실행당 {gmailReadLimit(selected)||100}건. 조회한 메일을 여러 묶음으로 요약하며, 한도를 넘으면 결과에 표시합니다.</p><GmailConnection disabled={!!busy||!!recovery||libraryBusy} variant="card" onStatusChange={setGmailConnected}/></div>:runInputs.library_source?<p className="as-small-muted as-schedule-input-note">연결한 자료함의 설정에 따라 실행할 파일을 선택합니다.</p>:sourceFreeSchedule?<p className="as-small-muted as-schedule-input-note">저장한 설정과 연결된 서비스로 실행합니다.</p>:<div className="as-source-line"><div><strong>관리 데이터 소스</strong><p>{source?source.asset.name+' · 버전 '+source.version:'연결 필요'}</p></div><button className="as-button" onClick={()=>sourceInput.current?.click()} disabled={!!busy||!!recovery||libraryBusy}><RefreshCw size={14}/>{source?'파일 갱신':'파일 연결'}</button></div>}{librarySourceSettings} {scheduleNotice&&<p className="as-small-muted" role="status">{scheduleNotice}</p>}<small>현재는 이 PC의 서버에서 실행합니다. PC 또는 서버를 끄면 예약 실행도 멈춥니다.</small>{jobs.some(j=>['queued','running','skipped'].includes(j.status)||j.status==='failed'&&!j.run_id)&&<details className="as-evidence-list"><summary>예약 대기·관리 상태</summary>{jobs.filter(j=>['queued','running','skipped'].includes(j.status)||j.status==='failed'&&!j.run_id).slice(0,8).map(j=><p key={j.id}>{j.kind==='schedule_test'?'시험 예약':'정규 예약'} · {new Date(j.due_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})} · {({queued:'대기',running:'실행 중',succeeded:'완료',failed:'실패',skipped:'대상 파일 없음'} as Record<string,string>)[j.status]||j.status}{(j as any).retry_count?' · 재시도 '+(j as any).retry_count+'회':''}{j.source_name?' · '+j.source_name:''}{j.error?' · '+j.error:''}{j.kind==='schedule_test'&&j.status==='queued'&&<button className="as-text-button" disabled={!!busy||!!recovery||libraryBusy} onClick={()=>void cancelSchedule(j.id)}>시험 예약 취소</button>}</p>)}</details>}</div>}{screen==='schedule'&&scheduleRequestRequired&&<p className="as-small-muted" role="status">자동 실행에 필요한 요청을 업무 흐름의 고정 설정으로 지정한 뒤 예약을 켜세요.</p>}{screen!=='schedule'&&gmailRequired&&<GmailConnection disabled={!!busy||!!recovery||libraryBusy} variant="card" onStatusChange={setGmailConnected}/>}{screen!=='schedule'&&<div className="as-run-files"><button className="as-button" onClick={()=>runFileInput.current?.click()} disabled={!!busy||!!recovery||libraryBusy}><Paperclip size={15}/>이번 실행 자료 추가</button><small>{fileHint}</small>{runFiles.map(f=><div key={f.id}><span>{f.name}</span><button disabled={!!busy||!!recovery||libraryBusy} onClick={()=>setRunFiles(prior=>prior.filter(x=>x.id!==f.id))} aria-label={f.name+' 제거'}><X size={14}/></button></div>)}</div>}{screen!=='schedule'&&(requestRequired||testRun?.question||reviewCapable)&&runRequestField}{screen!=='schedule'&&reviewCapable&&<label className="as-field">검수할 응대 초안<textarea aria-label="검수할 응대 초안" placeholder="이미 작성한 초안을 붙여 넣으세요. 앞 작업에서 초안을 생성하거나 표로 검수할 때는 비워 두세요." value={replyDraft} onChange={e=>setReplyDraft(e.target.value)} maxLength={12000} disabled={!!busy||!!recovery||libraryBusy}/></label>}<div className="as-run-actions">{screen!=='schedule'&&testRun?.question&&<button className="as-button as-primary" onClick={resumeRun} disabled={!!busy||!testText.trim()||(slackRequired&&!slackConnected)}>답변하고 이어 실행</button>}<button className={"as-button "+(!testRun?.question?"as-primary":"")} data-tour="run-submit" onClick={runTest} disabled={!!busy||!!recovery||libraryBusy||!canStartRun||screen==='schedule'&&!schedule}>{screen==='schedule'?"예약 자료로 지금 실행":testRun?.question?"새 실행 시작":"워크플로우 실행"}</button></div>{screen==='run'&&!requestRequired&&!testRun?.question&&!reviewCapable&&<RunRequestDetails key={selected.id} hasText={!!testText.trim()}>
        <summary>추가 요청</summary>{runRequestField}
       </RunRequestDetails>}{screen==='run'&&librarySourceSettings&&<details className="as-run-source-settings"><summary>{scheduleSourceDirty?'자료함 연결 설정 · 저장 필요':'자료함 연결 설정'}</summary>{librarySourceSettings}</details>}{reviewCapable&&<RunFileDownload file={{name:'승인된_응대.csv',mime:'text/csv'}} url={'/api/agent-builder/approved_replies?id='+encodeURIComponent(selected.id)} label="승인된 응대 CSV 다운로드"/>}{screen!=='schedule'&&scheduleNotice&&<p className="as-small-muted" role="status">{scheduleNotice}</p>}{history.length>0&&<details className="as-run-history"><summary>실행 기록 ({history.length})</summary><select disabled={!!busy} aria-label="실행 기록 선택" value={testRun?.id||''} onChange={e=>void selectRun(e.target.value)}>{history.map(r=><option key={r.id} value={r.id}>{r.id.slice(0,8)} · {({succeeded:'완료',failed:'실패',running:'실행 중',needs_input:'답변 대기'} as Record<string,string>)[r.status]||r.status} · 설정 v{r.agent_version} · {r.trigger==='scheduled'?'정규 예약':r.trigger==='schedule_test'?'시험 예약':'지금 실행'}</option>)}</select><button disabled={!!busy} className="as-text-button" onClick={()=>{pinnedRun.current=false;setTestRun(history[0]);}}>최신 실행 보기</button></details>}</div>{!testRun&&runWorkflow&&<div className="as-test-result"><RunWorkflowDiagram workflow={runWorkflow}/></div>}{!testRun&&!runWorkflow&&<div className="as-service-empty"><FileOutput size={30}/><h2>실행 결과</h2><p>요청과 자료를 넣어 실행하면 결과와 단계 기록이 여기에 표시됩니다.</p></div>}{testRun&&<div data-tour="run-result" ref={resultPanel} className="as-test-result"><div className="as-result-title"><strong>{testRun.status==='succeeded'?(testRun.reviews?.length?(testRun.approval_status==='pending'?'검수 완료 · 승인 대기':'검수 완료 · 운영자 결정 저장됨'):'워크플로우 실행 완료'):testRun.status==='failed'?'실행 실패':testRun.status==='running'?'실행 중':'추가 정보 확인 필요'}</strong><span>설정 v{testRun.agent_version} · 실행 {testRun.id.slice(0,8)}</span></div>

      {(testRun.error || testRun.answer) && (testRun.error
       ? <p className="as-preserve">{testRun.error}</p>
       : testRun.status==='succeeded'&&isGmailSlackSummaryWorkflow(runWorkflow)
        ? <GmailSummaryResult answer={testRun.answer} workflow={runWorkflow}/>
        : <RunAnswer text={testRun.answer}/>)}
      {uncertainSlackDelivery&&<p role="alert" className="as-small-muted">Slack 채널에서 메시지를 확인한 뒤 필요하면 새 실행을 시작해주세요.</p>}
      {resultFile && <div className="as-run-file-bar">
        <div><small>{testRun.trigger==='schedule_test'?'시험 예약 · ':testRun.trigger==='scheduled'?'정규 예약 · ':''}결과 파일</small><strong>{resultFile.name}</strong></div>
        <RunFileDownload key={testRun.id} file={resultFile}
          url={testRun.report_path ? '/api/agent-builder/report?id='+encodeURIComponent(testRun.id) : undefined}/>
      </div>}
      <RunWorkflowDiagram workflow={runWorkflow} steps={testRun.steps} runStatus={testRun.status} waitingNodeId={testRun.question?.node_id}/>{testRun.source_version&&<p className="as-small-muted">이 실행에 사용한 소스: v{testRun.source_version}</p>}{!!testRun.library_source_files?.length&&<details className="as-evidence-list"><summary>실행에 사용한 자료함 파일 ({testRun.library_source_files.length})</summary>{testRun.library_source_files.map(file=><p key={file.space+":"+file.asset_id+":"+file.version}><strong>{file.name}</strong> · {file.space==="team"?"팀 자료함":"내 자료함"}<small className="as-source-version"> · 버전 {file.version}</small></p>)}</details>}{unverifiedFields.length>0&&<p role="status">원문에서 확인하지 못해 빈칸으로 둔 항목: {unverifiedFields.join(' · ')}</p>}{testRun.rows?.length>0&&<div className="as-table-scroll"><table><thead><tr>{Object.keys(testRun.rows[0]).map(k=><th key={k}>{("evidence" in testRun.rows[0]&&"field" in testRun.rows[0])?({file_name:"파일",field:"항목",value:"추출값",status:"확인 상태",evidence:"근거"} as Record<string,string>)[k]||k:k}</th>)}</tr></thead><tbody>{testRun.rows.slice(0,10).map((r,i)=><tr key={i}>{Object.keys(testRun.rows[0]).map(k=><td key={k}>{k==='evidence'&&r[k]?<button className="as-text-button" onClick={()=>setEvidence(r[k] as any)}>원문 확인</button>:typeof r[k]==='object'&&r[k]!==null?JSON.stringify(r[k]):k==='status'?({verified:'원문 일치',not_found_or_unverified:'누락 또는 근거 미확인'} as Record<string,string>)[String(r[k])]||String(r[k]):String(r[k]??'')}</td>)}</tr>)}</tbody></table><small>전체 {testRun.rows.length}행 · 최대 10행 미리보기</small></div>}{testRun.public_projection&&(testRun.public_projection.evidence_truncated||testRun.public_projection.quote_truncated)&&<p className="as-small-muted" role="status">근거 {testRun.public_projection.evidence_total}개 중 {testRun.public_projection.evidence_returned}개를 표시합니다. 긴 인용은 앞부분을 표시하며 전체 실행 데이터는 서버에 보관됩니다.</p>}<details className="as-evidence-list"><summary>실행에 사용한 근거 확인 ({testRun.evidence?.length||0})</summary>{testRun.evidence?.map((e,i)=><button key={i} className="as-resource" onClick={()=>setEvidence(e)}>{e.file_name}{e.page?` · ${e.page}쪽`:""}<span>원문 보기</span></button>)}</details>{testRun.status==='failed'&&!uncertainSlackDelivery&&<button className="as-button" disabled={!!busy||!!recovery||libraryBusy||(slackRequired&&!slackConnected)} onClick={retryRun}>실패 단계부터 재시도</button>}{!!testRun.reviews?.length&&<ReplyReviewPanel reviews={testRun.reviews} runId={testRun.id} disabled={!!busy||!!recovery||libraryBusy} canDecide={testRun.status==='succeeded'} onDecision={decideReply}/>}{(testRun.attempts?.length||0)>1&&<details className="as-evidence-list"><summary>실행·재개 이력 ({testRun.attempts!.length}회)</summary>{testRun.attempts!.map(a=><p key={a.number}>{a.number}차 · {({succeeded:'성공',failed:'실패',running:'실행 중',needs_input:'응답 대기'} as Record<string,string>)[a.status]||a.status}{a.error?' · '+a.error:''}</p>)}</details>}<Trace steps={testRun.steps}/></div>}</div>:<><div className="as-messages" tabIndex={0} role="region" aria-label="대화 내용">{loading?<div className="as-loading"><Loader2 className="as-spin"/>저장한 대화를 확인하고 있습니다.</div>:!pendingMessage&&!externalMessages.length&&!session?.messages.length&&!session?.file_inspection?.length?<div className="as-welcome"><span className="as-welcome-icon"><WorkflowIcon size={27}/></span><h2>어떤 일을 처리할까요?</h2><p>사용할 자료, 처리할 조건, 원하는 결과를 설명하세요.<br/>추가로 필요한 내용은 대화에서 확인합니다.</p>{samples.length>0&&<div className="as-examples">{samples.map(s=><button key={s.label} onClick={()=>{setText(s.text);input.current?.focus();}}><strong>{s.label}<ArrowUp size={14}/></strong><span>{s.text.slice(0,75)}…</span></button>)}</div>}</div>:session?.messages.map((message, i) => {

      let content = message.content;
      if (!selected && i === lastAssistant) {
        if (receiptQuestion) {
          const superseded = receiptQuestion.supersedes_texts || [];
          content = content.split('\n').filter(line => !superseded.includes(line.trim().replace(/^•\s*/, ''))).join('\n');
          content = content.replace(receiptQuestion.text, '').trim();
        } else if (clarificationQuestions.length) {
          content = content.split('\n').filter(line => !clarificationQuestions.some(q => q.text.trim() === line.trim().replace(/^•\s*/, ''))).join('\n').trim();
        }
      }
      return content ? <StudioMessage key={i} role={message.role} content={content}/> : null;
    })}
     {externalMessages.map((message,index)=><StudioMessage key={'external-'+index} role={message.role} content={message.content}/>)}
    {!selected&&session?.file_inspection?.length ? <FileInspection files={session.file_inspection}
      detailsFor={id=>{
        const asset=session.resources.find(resource=>resource.id===id);
        if(!asset)return;
        const full=asset.text?.trim() || (asset.kind==='table' && asset.row_count<=asset.preview.length);
        return {label:full?'읽은 내용 전체 보기':'자료 상세 보기',open:()=>setResource(asset)};
      }} factsFor={id=>{const asset=session.resources.find(resource=>resource.id===id);return asset?{row_count:asset.kind==='table'?asset.row_count:undefined,page_count:asset.page_count}:undefined;}}
      initial={!session.messages.some(message=>message.role==='user')} fresh={freshFiles.session===session.id?freshFiles.ids:[]} readOnly={!!receiptQuestion} disabled={!!busy||!!recovery||libraryBusy||saved}
      onUse={message=>{
        const next=text.trim()?text.trimEnd()+'\n'+message:message;
        if(next.length>4000){setError('업무 설명은 4,000자까지 입력할 수 있어요. 내용을 줄인 뒤 다시 추가해 주세요.');return;}
        setText(next);input.current?.focus();
      }}/> : null}
    {receiptQuestion?.action ? <ReceiptQuestion key={receiptQuestion.id} text={receiptQuestion.text} action={receiptQuestion.action}
      disabled={!!busy||!!recovery||libraryBusy} onAttach={()=>fileInput.current?.click()} onLibrary={()=>setAttachMode('library')} onAnswer={answer=>void send(undefined,answer,receiptQuestion.id)}/> : !selected&&slackChannelQuestion ? <SlackChannelPicker key={session?.id+'-'+slackChannelQuestion.id} disabled={!!busy||!!recovery||libraryBusy} onSelect={channelId=>send(undefined,channelId,slackChannelQuestion.id)} onReconnect={()=>leave('connections')}/> : !selected&&clarificationQuestions.length>0&&<ClarificationForm key={session?.id} questions={clarificationQuestions} disabled={!!busy||!!recovery||libraryBusy} onDirtyChange={setClarificationDirty} onSubmit={answerQuestions}/>}{session?.requirement_failure?.retryable&&<div className="as-requirement-retry"><p>입력하신 조건은 보존되어 있습니다. 설명을 다시 작성할 필요 없이 재시도할 수 있습니다.</p><button type="button" className="as-button" disabled={!!busy||!!recovery||libraryBusy} onClick={()=>void send(undefined,'앞서 전달한 조건을 유지하고 업무 흐름 생성을 다시 시도해주세요.')}>조건 그대로 다시 시도</button></div>}{pendingMessage&&<div className="as-message user"><span className="as-message-label">나</span><p>{pendingMessage}</p></div>}{busy&&<div className="as-busy as-operation-progress" role="status"><Loader2 size={17} className="as-spin" aria-hidden="true"/><span>{busy}</span></div>}<div ref={end}/></div><form className="as-composer" onSubmit={send}><textarea ref={input} aria-label="업무 설명" placeholder={collecting?'확인 질문에 답해주세요.':draft?'추가하거나 바꿀 조건을 설명하세요.':'예: 여러 자료에서 필요한 내용을 찾아 정리하는 기능이 필요해요.'} value={text} onChange={e=>setText(e.target.value)} maxLength={4000} disabled={!!busy||!!recovery||saved} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/><div className="as-composer-footer"><button data-tour="attach" type="button" className="as-text-button" onClick={()=>setAttachMode('choose')} disabled={!!busy||!!recovery||saved||!credentialId&&!session}><Paperclip size={16}/>자료 연결</button><small>Enter 전송 · Shift+Enter 줄바꿈</small><button type="submit" className="as-send" disabled={!!busy||!text.trim()||saved||!credentialId&&!session} aria-label="업무 설명 보내기"><ArrowUp size={19}/></button></div></form></>}

   </section>
   {screen==='workflow'&&<section data-tour="canvas" className="as-workspace" aria-label="워크플로우 캔버스"><WorkflowPreview workflow={draft?.workflow} selectedId={selectedNodeId} onSelect={n=>{setSelectedNodeId(n.id);showEditorPanel('settings');requestAnimationFrame(()=>settingsPanel.current?.scrollIntoView?.({block:'nearest'}));}}/></section>}
   <aside ref={settingsPanel} data-tour="settings" className="as-config" aria-label="워크플로우 설정"><button type="button" className="as-panel-header as-config-toggle" aria-expanded={settingsOpen} aria-controls="workflow-settings-detail" onClick={()=>setSettingsOpen(open=>!open)}><span><SlidersHorizontal size={17}/>{selectedNode?'작업 설정':saved?'저장한 설정':'워크플로우 설정'}</span><span className={'as-status '+(ready||saved?'ready':'')}>{saved?'저장됨':ready?'확인 가능':session?.status==='validation_failed'?'검증 실패':draft?'확인 중':'작성 전'}<ChevronDown size={15} aria-hidden="true"/></span></button><div id="workflow-settings-detail" hidden={!settingsOpen} className="as-config-body" tabIndex={0} role="region" aria-label="설정 상세">{selectedNode?<NodeInspector workflow={draft?.workflow} node={selectedNode} tool={catalog.find(t=>t.id===selectedNode.tool)} onClose={()=>setSelectedNodeId(null)}/>:<>

    {draft?<>{draft.schedule&&!saved&&<div className="as-setting-section"><label>예약 트리거</label><p>{scheduleDescription(draft.schedule)} · {draft.schedule.timezone||'시간대 확인 필요'}</p><p>저장 위치: {draft.report_destination==='reports'?'A4B 서버 보고서 보관함':'확인 필요'}</p></div>}<div className="as-setting-section"><label>이름과 역할</label><h3>{draft.name||'이름 확인 필요'}</h3><p>{draft.purpose}</p></div><div className="as-setting-section"><label>지시사항</label><details open={saved || undefined}><summary>작업 내용 자세히 보기<ChevronDown size={15}/></summary><p className="as-preserve as-instructions">{draft.instructions||'업무를 더 설명해주세요.'}</p></details></div>{draft.workflow?.nodes.filter(n=>n.tool==='filter_sort').map(n=><div className="as-setting-section" key={n.id}><label>{n.label} · 개별 실행 규칙</label><RuleSummary plan={(n.settings.execution_plan||draft.execution_plan) as Draft['execution_plan']}/></div>)}<details className="as-setting-section"><summary>처리 기능 {draft.tool_ids.length}개 보기</summary>{draft.tool_ids.map(id=>{const info=toolDetails[id];const actual=info||{label:({aggregate_table:'목록 집계',calculate_columns:'표 열 계산·이름 변경',text_transform:'문자열 바꾸기',base64_codec:'Base64 문자열 변환',text_hash:'문자열 SHA-256',date_transform:'날짜·시간 변환',text_statistics:'문자·단어 수 세기',finish:'완료 안내',read_document:'문서 읽기',search_document:'관련 내용 검색',extract_information:'정보 추출',compose_answer:'근거 기반 답변',export_result:'결과 저장',condition:'조건 분기',ask_user:'사용자 확인',agent_node:'AI 작업',reply_review:'응대 초안 검수',review_summary:'검수 보고서 정리',saved_workflow:'저장된 워크플로우 실행'} as Record<string,string>)[id]||id,description:'등록된 공통 작업 코드를 실행합니다.',Icon:FileText};const Icon=actual.Icon;return <div key={id} className="as-tool"><Icon size={17}/><div><strong>{actual.label}</strong><small>{toolDescription(id,actual.description)}</small></div><Check size={14}/></div>;})}</details><div className="as-setting-section"><label>업무 조건과 근거</label>{draft.requirements.map((r,i)=><details key={i} className="as-evidence"><summary>{r.description}<ChevronDown size={13}/></summary><small>{r.source_id==='user'?'사용자 대화':resources.find(f=>f.id===r.source_id)?.name||'연결 자료'}</small><blockquote>{r.quote}</blockquote></details>)}</div></>:<div className="as-empty-config"><FileText size={26}/><p>업무를 설명하면 처리 순서와 확인할 조건이 정리됩니다.</p></div>}
    {!selected&&session?.input_requirements?.map((item,i)=><div className="as-setting-section" key={"input-"+i}><label>{item.timing==='design'?'지금 필요한 자료':'실행 전에 필요한 자료'}</label><p>{item.description}</p>{item.fields.length>0&&<p>필요한 열: {item.fields.join(' · ')}</p>}<p>{item.ready?'연결 자료 있음 · 실제 실행 전 재확인':'파일 미연결'}</p><button type="button" className="as-text-button" disabled={!!busy||!!recovery||saved} onClick={()=>setAttachMode('choose')}>자료 연결</button></div>)}<div className="as-setting-section"><label>연결 자료 <span>{resources.length}/10</span></label>{resources.map(r=><button key={r.id} className="as-resource" onClick={()=>setResource(r)}><FileText size={17}/><span><strong>{r.name}</strong><small>{r.kind==='table'?r.row_count+'행 · '+r.columns.length+'개 열':'업무 규칙 문서'}</small></span><ChevronDown size={14}/></button>)}{!resources.length&&<p className="as-small-muted">파일 구조나 업무 규칙을 참고하려면 자료를 연결하세요.</p>}</div>
    {session && !collecting && session.issues.length>0&&<div className="as-validation" role="status">{session.issues.map((issue,i)=><p key={i}>{issue}</p>)}</div>}{draft && draft.unsupported.length>0&&<div className="as-validation"><p>요청한 작업에 필요한 기능과 연결 상태를 추가로 확인해야 합니다.</p></div>}{session?.tool_gap&&<div className="as-validation"><p>부족한 기능을 기록했습니다. 기존 도구로 가능한지 검토한 뒤 임시 도구 시험으로 연결할 수 있습니다.</p><button type="button" disabled={!!busy||!!recovery||libraryBusy} onClick={()=>downloadToolGap(session).catch(e=>setError((e as Error).message))}>부족 기능 명세 받기</button></div>}{session && session.trace.length>0&&<Trace steps={session.trace}/>}</>}</div></aside></div>
  </main>}</div><input ref={fileInput} hidden type="file" accept={receiptQuestion ? receiptFileTypes : ".csv,.tsv,.xlsx,.xls,.json,.txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.hwp,.hwpx,.jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff,.heic"} onChange={e=>{const file=e.currentTarget.files?.[0];e.currentTarget.value='';if(file)void attachResource(file);}}/>
  <input ref={sourceInput} hidden type="file" accept=".csv,.tsv,.xlsx,.xls,.json,.txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.hwp,.hwpx,.jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff,.heic" onChange={e=>{const f=e.target.files?.[0];if(f)void uploadSource(f);}}/><input ref={runFileInput} hidden type="file" accept=".csv,.tsv,.xlsx,.xls,.json,.txt,.md,.pdf,.doc,.docx,.ppt,.pptx,.hwp,.hwpx,.jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff,.heic" onChange={e=>{const f=e.target.files?.[0];if(f)void uploadRun(f);}}/><Dialog open={!!evidence} onOpenChange={v=>{if(!v)setEvidence(null);}}><DialogContent className="as-dialog"><DialogHeader><DialogTitle>실행 근거 원문</DialogTitle><DialogDescription>{evidence?.file_name}{evidenceLocation(evidence)}</DialogDescription></DialogHeader><p className="as-preserve">{evidence?.quote||evidence?.text}</p>{evidence?.file_id&&<a className="as-button" href={(testRun?.library_source_files?.some(file=>file.execution_asset_id===evidence.file_id)?"/api/agent-builder/source_original?run_id="+encodeURIComponent(testRun.id)+"&asset_id="+encodeURIComponent(evidence.file_id):"/api/agent-builder/original?id="+encodeURIComponent(evidence.file_id))+(evidence.page?"#page="+evidence.page:"")} target="_blank" rel="noreferrer">원본 파일 열기{evidence.page?` · ${evidence.page}쪽`:""}</a>}<p className="as-small-muted">파서가 읽은 원문입니다. 추출 결과의 인용은 이 원문과 일치하는지 검사합니다. 답변에 사용한 구간은 답변 전체의 정확성을 보증하지 않습니다.</p></DialogContent></Dialog>



  <Dialog open={saveReviewOpen} onOpenChange={setSaveReviewOpen}><DialogContent className="as-dialog as-save-review"><DialogHeader><DialogTitle>저장 전 예약 확인</DialogTitle><DialogDescription>요청에서 확인한 일정입니다. 예약을 함께 저장할지 확인하세요.</DialogDescription></DialogHeader>{draft?.schedule&&<><div className="as-save-review-summary"><strong>{scheduleDescription(draft.schedule)}</strong><span>서울 표준시 · Asia/Seoul</span>{scheduleDateNote(draft.schedule)&&<small>{scheduleDateNote(draft.schedule)}</small>}</div><fieldset className="as-save-schedule-choice"><legend>예약 설정</legend><label><input type="radio" name="save-schedule" value="include" checked={includeSchedule} onChange={()=>setIncludeSchedule(true)}/><span><strong>예약도 함께 저장</strong><small>예약은 꺼진 상태로 저장됩니다. 예약 관리에서 켜면 자동으로 실행됩니다.</small></span></label><label><input type="radio" name="save-schedule" value="omit" checked={!includeSchedule} onChange={()=>setIncludeSchedule(false)}/><span><strong>예약 없이 저장</strong><small>필요할 때 직접 실행하고, 나중에 예약을 추가할 수 있습니다.</small></span></label></fieldset><div className="as-save-review-actions"><button className="as-button" onClick={()=>setSaveReviewOpen(false)}>돌아가기</button><button className="as-button as-primary" disabled={!!busy||!!recovery||libraryBusy} onClick={()=>void save(includeSchedule)}>{includeSchedule?'예약과 함께 저장':'워크플로우만 저장'}</button></div></>}</DialogContent></Dialog>
  <Dialog open={!!resource} onOpenChange={v=>{if(!v)setResource(null);}}><DialogContent className="as-dialog as-resource-dialog"><DialogHeader><DialogTitle>{resource?.name}</DialogTitle><DialogDescription>{resource?.kind==='table'?'실제로 읽은 파일의 열·행과 예외를 확인합니다.':'파일에서 읽은 본문을 확인합니다.'}</DialogDescription></DialogHeader>{resource?.original_path&&!resource.parser_metadata?.document_analysis&&<a className="as-text-button" href={'/api/agent-builder/original?id='+encodeURIComponent(resource.id)} target="_blank" rel="noreferrer">원본 파일 열기</a>}<DocumentAnalysisSummary analysis={resource?.parser_metadata?.document_analysis} showText={resource?.kind==='table'} humanReviewed={resource?.parser_metadata?.document_review?.status==='completed'}/>{resource?.parser_metadata?.document_analysis&&<DocumentReviewPanel key={resource.id} assetId={resource.id} onUpdated={updateReviewedAsset} disabled={!!busy}/>}{resource?.kind==='table'?<><div className="as-table-scroll"><table><thead><tr><th>열</th><th>자료형</th><th>빈 값</th></tr></thead><tbody>{resource.columns.map(k=><tr key={k}><td>{k}</td><td>{((resource.profile[k] as any)?.types||[]).join(', ')}</td><td>{(resource.profile[k] as any)?.missing||0}</td></tr>)}</tbody></table></div><div className="as-table-scroll"><table><thead><tr>{resource.columns.map(k=><th key={k}>{({file_name:"파일",field:"항목",value:"추출값",status:"확인 상태",evidence:"근거"} as Record<string,string>)[k]||k}</th>)}</tr></thead><tbody>{resource.preview.map((r,i)=><tr key={i}>{resource.columns.map(k=><td key={k}>{k==='evidence'&&r[k]?<button className="as-text-button" onClick={()=>setEvidence(r[k] as any)}>원문 확인</button>:typeof r[k]==='object'&&r[k]!==null?JSON.stringify(r[k]):k==='status'?({verified:'원문 일치',not_found_or_unverified:'누락 또는 근거 미확인'} as Record<string,string>)[String(r[k])]||String(r[k]):String(r[k]??'')}</td>)}</tr>)}</tbody></table></div></>:<div>{!resource?.parser_metadata?.document_analysis&&resource?.engine&&<p className="as-small-muted">{resource.engine} · {resource.page_count}페이지</p>}{!resource?.parser_metadata?.document_analysis&&resource?.warnings?.map((w,i)=><p className="as-validation" key={i}>{w}</p>)}<p className="as-preserve">{resource?.text}</p></div>}</DialogContent></Dialog>
 </div>;
}
function RunRequestDetails({hasText,children}:{hasText:boolean;children:ReactNode}){
 const [open,setOpen]=useState(hasText);
 const shown=useRef(hasText);
 useEffect(()=>{
  if(hasText&&!shown.current){shown.current=true;setOpen(true);}
 },[hasText]);
 return <details className="as-run-source-settings" open={open} onToggle={event=>setOpen(event.currentTarget.open)}>{children}</details>;
}
function Trace({steps}:{steps:Session['trace']}){return <details className="as-trace"><summary>처리 단계 확인<ChevronDown size={14}/></summary><ol>{steps.map((s,i)=><li key={i}><strong>{s.label}</strong><span>{s.detail}{typeof (s as any).elapsed_ms==='number'?` · ${(s as any).elapsed_ms}ms`:''}</span>{(s as any).input_summary&&<pre>{JSON.stringify({입력:(s as any).input_summary,출력:(s as any).output_summary},null,2)}</pre>}{(s as any).parameters&&<pre>{JSON.stringify((s as any).parameters,null,2)}</pre>}{(s as any).child_steps&&<details><summary>재사용한 워크플로우 내부 단계 · 버전 {(s as any).dependency?.version}</summary><Trace steps={(s as any).child_steps}/></details>}{(s as any).bindings?.length>0&&<pre>{JSON.stringify((s as any).bindings,null,2)}</pre>}</li>)}</ol></details>;}
