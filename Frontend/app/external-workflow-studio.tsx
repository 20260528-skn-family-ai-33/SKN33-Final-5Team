import {useEffect,useMemo,useRef,useState,type FormEvent} from 'react';
import {AlertCircle,ArrowLeft,Check,Loader2,MessageSquare,Play,RefreshCw,Save,Settings2,Trash2,Unplug} from 'lucide-react';
import {request} from '../components/flow/connection-panel';
import {NodeEditor} from '../components/flow/node-editor';
import {WorkflowSettings} from '../components/flow/workflow-settings';
import {FileInput} from '../components/flow/file-input';
import {DataView} from '../components/flow/data-view';
import {WorkflowPreview,type Workflow as PreviewWorkflow} from './workflow-preview';
import {clone,deleteNode,nodeInfo,nodePorts,renameNode,validateFlow,type Execution,type Flow,type FlowNode,type Json} from '../../shared/workflow';
import type {CredentialPublic} from '../lib/api-contracts';
import {missingStartFiles} from '../lib/run-readiness';
import './agent-studio.css';
import './external-workflow-studio.css';

type ConversationTurn={role:'user'|'assistant';content:string};
type DesignResult={workflow?:Flow;summary?:string;checklist?:string[];questions?:{question:string;choices?:string[]}[];requirements?:{id:string;description:string;evidence?:string;status:string;nodeIds?:string[]}[]};
type DesignSession={id:string;revision:number;conversation:ConversationTurn[];result?:DesignResult|null};
type WebhookInfo={url:string;active:boolean;authentication:string;httpMethod:string;responseMode:string};

function previewOf(flow:Flow):PreviewWorkflow {
 const ids=new Map(flow.nodes.map(node=>[node.name,node.id]));
 const byName=new Map(flow.nodes.map(node=>[node.name,node]));
 const edges:PreviewWorkflow['edges']=[];
 for(const [source,channels] of Object.entries(flow.connections))for(const outputs of Object.values(channels))outputs.forEach((items,index)=>{
  for(const item of items){const from=ids.get(source),to=ids.get(item.node);if(from&&to)edges.push({source:from,target:to,branch:byName.get(source)?.type==='n8n-nodes-base.if'?(index===0?'yes':'no'):'next'});}
 });
 const nodes=flow.nodes.map(node=>({id:node.id,tool:node.type==='n8n-nodes-base.if'?'condition':nodeInfo(node)?.category==='시작'?'read_table':nodeInfo(node)?.category==='AI'?'agent_node':'filter_sort',label:node.name,bindings:[],settings:node.parameters}));
 return {entry:flow.nodes.find(node=>nodePorts(node).inputs===0)?.id||flow.nodes[0]?.id||'',nodes,edges};
}

function editedNode(flow:Flow,node:FlowNode):Flow {
 const current=flow.nodes.find(item=>item.id===node.id);
 if(!current)throw new Error('수정할 작업을 찾을 수 없습니다.');
 if(!node.name.trim()||flow.nodes.some(item=>item.id!==node.id&&item.name===node.name))throw new Error('작업 이름은 비어 있거나 다른 작업과 같을 수 없습니다.');
 const renamed=current.name===node.name?clone(flow):renameNode(flow,node.id,node.name);
 renamed.nodes=renamed.nodes.map(item=>item.id===node.id?clone(node):item);
 if(renamed.intentReview)renamed.intentReview={...renamed.intentReview,reviewed:false};
 return renamed;
}

function runInput(text:string,fileInput:Json[]|null):Json[] {
 if(fileInput)return fileInput;
 let value:unknown;
 try{value=JSON.parse(text);}catch{throw new Error('시작 데이터에 올바른 JSON을 입력해주세요.');}
 const items=Array.isArray(value)?value:[value];
 if(!items.length||items.length>5000||items.some(item=>!item||typeof item!=='object'||Array.isArray(item)))throw new Error('시작 데이터는 객체 또는 객체 목록으로 입력해주세요.');
 return items as Json[];
}

function assertReviewed(flow:Flow):void {
 if(!flow.intentReview)return;
 const requirements=flow.intentReview.requirements;
 if(!flow.intentReview.reviewed||!Array.isArray(requirements)||requirements.some((item:{status:string;nodeIds?:string[]})=>item.status!=='mapped'||!Array.isArray(item.nodeIds)||!item.nodeIds.length||item.nodeIds.some(id=>!flow.nodes.some(node=>node.id===id))))throw new Error('초안 설계 근거에서 요구사항과 실제 단계를 확인해주세요.');
}

export function ExternalWorkflowStudio({workflowId,onBack,onServiceConnect,onCanLeaveChange}:{workflowId:string;onBack:()=>void;onServiceConnect:()=>void;onCanLeaveChange?:(guard:()=>boolean)=>void}) {
 const [flow,setFlow]=useState<Flow|null>(null),[saved,setSaved]=useState<Flow|null>(null),[allFlows,setAllFlows]=useState<Flow[]>([]);
 const [credentials,setCredentials]=useState<CredentialPublic[]>([]),[loading,setLoading]=useState(true),[busy,setBusy]=useState('');
 const [dirty,setDirty]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [selectedId,setSelectedId]=useState<string|null>(null),[nodeOpen,setNodeOpen]=useState(false),[settingsOpen,setSettingsOpen]=useState(false);
 const [prompt,setPrompt]=useState(''),[conversation,setConversation]=useState<ConversationTurn[]>([]),[session,setSession]=useState<{id:string;revision:number}|null>(null),[designResult,setDesignResult]=useState<DesignResult|null>(null);
 const [inputText,setInputText]=useState('{}'),[fileInput,setFileInput]=useState<Json[]|null>(null),[fileBusy,setFileBusy]=useState(false),[execution,setExecution]=useState<Execution|null>(null);
 const [history,setHistory]=useState<Execution[]|null>(null),[webhookInfo,setWebhookInfo]=useState<WebhookInfo|null>(null);
 const flowRef=useRef<Flow|null>(null),sessionRef=useRef<{id:string;revision:number}|null>(null),promptRef=useRef<HTMLTextAreaElement|null>(null);
 const preview=useMemo(()=>flow?previewOf(flow):null,[flow]);
 const willDeactivate=!!(saved?.active&&flow&&JSON.stringify([saved.nodes,saved.connections,saved.settings])!==JSON.stringify([flow.nodes,flow.connections,flow.settings]));
 const selected=flow?.nodes.find(node=>node.id===selectedId);
 const requirements=Array.isArray(flow?.intentReview?.requirements)?flow!.intentReview.requirements as {id:string;description:string;evidence?:string;status:string;nodeIds:string[]}[]:[];
 const reviewReady=requirements.every(item=>item.status==='mapped'&&Array.isArray(item.nodeIds)&&item.nodeIds.length>0&&item.nodeIds.every(id=>flow?.nodes.some(node=>node.id===id)));

 useEffect(()=>{
  let current=true;
  setLoading(true);setError('');setFlow(null);flowRef.current=null;setSaved(null);setDirty(false);setSelectedId(null);setExecution(null);setHistory(null);setWebhookInfo(null);setConversation([]);setSession(null);sessionRef.current=null;
  void request<{workflows:Flow[]}>('/api/workflows').then(result=>{
   if(!current)return;
   const found=result.workflows.find(item=>item.id===workflowId);
   setAllFlows(result.workflows);
   if(!found){setError('워크플로우를 찾을 수 없습니다. 작업 라이브러리에서 다시 확인해주세요.');return;}
   setFlow(found);flowRef.current=found;setSaved(found);
  }).catch(cause=>{if(current)setError((cause as Error).message);}).finally(()=>{if(current)setLoading(false);});
  void request<{credentials:CredentialPublic[]}>('/api/credentials').then(result=>{if(current)setCredentials(result.credentials||[]);}).catch(()=>{});
  return()=>{current=false;};
 },[workflowId]);

 useEffect(()=>{
  onCanLeaveChange?.(()=>!dirty||window.confirm('저장하지 않은 변경이 있습니다. 화면을 나갈까요?'));
  return()=>onCanLeaveChange?.(()=>true);
 },[dirty,onCanLeaveChange]);

 useEffect(()=>{
  if(!execution||!['queued','running'].includes(execution.status))return;
  let stopped=false;
  const timer=setInterval(()=>{void request<{execution:Execution}>('/api/executions?id='+encodeURIComponent(execution.id)).then(result=>{if(!stopped)setExecution(result.execution);}).catch(cause=>{if(!stopped)setError((cause as Error).message);});},900);
  return()=>{stopped=true;clearInterval(timer);};
 },[execution?.id,execution?.status]);

 function change(next:Flow){
  const before=flowRef.current;
  if(before&&next.intentReview&&JSON.stringify([before.nodes,before.connections])!==JSON.stringify([next.nodes,next.connections]))next={...next,intentReview:{...next.intentReview,reviewed:false}};
  flowRef.current=next;setFlow(next);setDirty(true);setWebhookInfo(null);setNotice('');setError('');
 }

 async function save(value=flowRef.current):Promise<Flow> {
  if(!value)throw new Error('워크플로우를 불러오는 중입니다.');
  const checked=validateFlow(clone(value));
  const result=await request<{workflow:Flow}>('/api/workflows',checked);
  setAllFlows(items=>[result.workflow,...items.filter(item=>item.id!==result.workflow.id)]);
  setSaved(result.workflow);
  if(flowRef.current===value){flowRef.current=result.workflow;setFlow(result.workflow);setDirty(false);}
  setNotice(value.active&&!result.workflow.active?'변경 내용을 저장했습니다. 자동 실행이 해제되었으니 설정을 확인한 뒤 다시 켜주세요.':'워크플로우를 저장했습니다.');
  return result.workflow;
 }

 async function saveClick(){if(busy)return;setBusy('저장 중');setError('');try{await save();}catch(cause){setError((cause as Error).message);}finally{setBusy('');}}

 async function ensureSession(currentFlow:Flow){
  if(sessionRef.current)return sessionRef.current;
  let restored:DesignSession|undefined;
  try{const id=localStorage.getItem('a4b-chat-session-'+workflowId);if(id)restored=await request<DesignSession>('/api/assistant/sessions/'+encodeURIComponent(id));}catch{localStorage.removeItem('a4b-chat-session-'+workflowId);}
  const next=restored||await request<DesignSession>('/api/assistant/sessions',{currentWorkflow:currentFlow});
  const identity={id:next.id,revision:next.revision};sessionRef.current=identity;setSession(identity);setConversation(next.conversation||[]);setDesignResult(next.result||null);
  localStorage.setItem('a4b-chat-session-'+workflowId,next.id);
  return identity;
 }

 async function restartConversation(){if(busy||!flowRef.current)return;setBusy('대화 준비 중');setError('');try{
  const next=await request<DesignSession>('/api/assistant/sessions',{currentWorkflow:flowRef.current});
  const identity={id:next.id,revision:next.revision};sessionRef.current=identity;setSession(identity);setConversation(next.conversation||[]);setDesignResult(null);setPrompt('');
  localStorage.setItem('a4b-chat-session-'+workflowId,next.id);
  setNotice('현재 설계를 기준으로 새 대화를 시작했습니다.');
 }catch(cause){setError((cause as Error).message);}finally{setBusy('');}}

 async function sendPrompt(event:FormEvent<HTMLFormElement>){
  event.preventDefault();const content=prompt.trim(),current=flowRef.current;
  if(!current||!content||busy)return;
  setBusy('설계 중');setError('');setNotice('');
  try{
   const identity=await ensureSession(current);
   const snapshot=JSON.stringify(current);
   const result=await request<DesignSession>('/api/assistant/sessions/'+encodeURIComponent(identity.id),{prompt:content,credentialId:'a4b-server-model',revision:identity.revision,currentWorkflow:current});
   const updated={id:result.id,revision:result.revision};sessionRef.current=updated;setSession(updated);setConversation(result.conversation||[]);setDesignResult(result.result||null);setPrompt('');
   if(result.result?.workflow){
    if(JSON.stringify(flowRef.current)!==snapshot){setNotice('답변을 기다리는 동안 설계가 바뀌었습니다. 새 제안을 자동 반영하지 않았습니다.');return;}
    const proposed=result.result.workflow;
    const next=validateFlow({...proposed,id:current.id,active:false,...(proposed.intentReview?{intentReview:{...proposed.intentReview,reviewed:false}}:{})});
    change(next);
    setSelectedId(null);
    setNotice('대화에서 만든 초안을 반영했습니다. 단계와 요구사항을 확인한 뒤 저장해주세요.');
   }
  }catch(cause){setError((cause as Error).message);}finally{setBusy('');}
 }

 async function startRun(mode:'learning'|'live',override?:Flow){
  if(busy||fileBusy)return;
  const current=override||flowRef.current;if(!current)return;
  setBusy(mode==='live'?'실제 실행 시작 중':'시험 실행 시작 중');setError('');
  try{
   assertReviewed(current);
   const input=runInput(inputText,fileInput);
   const missing=missingStartFiles(current,input);if(missing.length)throw new Error(missing.join('\n'));
   const ready=dirty||override?await save(current):current;
   const result=await request<{executionId:string;execution:Execution}>('/api/runs',{workflowId:ready.id,mode,input});
   setExecution(result.execution);setHistory(items=>items?[result.execution,...items.filter(item=>item.id!==result.execution.id)]:items);setNotice(mode==='live'?'실제 실행을 시작했습니다. 외부 서비스 동작을 실행합니다.':'시험 실행을 시작했습니다.');
  }catch(cause){setError((cause as Error).message);}finally{setBusy('');}
 }

 async function toggleActive(){if(busy||!flowRef.current)return;const target=!flowRef.current.active;setBusy('자동 실행 설정 중');setError('');try{
  if(target)assertReviewed(flowRef.current);
  const current=dirty?await save():flowRef.current;
  if(current.active===target){setNotice(target?'자동 실행을 켰습니다.':'변경 내용을 저장하면서 자동 실행이 꺼졌습니다.');return;}
  const result=await request<{workflow:Flow}>('/api/activation',{workflowId:current.id,active:target});
  flowRef.current=result.workflow;setFlow(result.workflow);setSaved(result.workflow);setDirty(false);setWebhookInfo(null);setNotice(result.workflow.active?'자동 실행을 켰습니다.':'자동 실행을 껐습니다.');
 }catch(cause){setError((cause as Error).message);}finally{setBusy('');}}

 function applyNode(node:FlowNode):boolean{try{const current=flowRef.current;if(!current)throw new Error('워크플로우를 불러오는 중입니다.');change(editedNode(current,node));setNodeOpen(false);return true;}catch(cause){setError((cause as Error).message);return false;}}

 async function reload(){if(dirty&&!window.confirm('저장하지 않은 변경을 버리고 다시 불러올까요?'))return;setBusy('새로고침 중');setError('');try{
  const result=await request<{workflows:Flow[]}>('/api/workflows');const found=result.workflows.find(item=>item.id===workflowId);if(!found)throw new Error('워크플로우를 찾을 수 없습니다.');
  setAllFlows(result.workflows);flowRef.current=found;setFlow(found);setSaved(found);setDirty(false);setNotice('최신 설계를 불러왔습니다.');
 }catch(cause){setError((cause as Error).message);}finally{setBusy('');}}

 async function loadHistory(){if(busy)return;setBusy('기록 불러오는 중');setError('');try{
  const result=await request<{executions:Execution[]}>('/api/executions');
  setHistory((result.executions||[]).filter(item=>item.workflowId===workflowId));
 }catch(cause){setError((cause as Error).message);}finally{setBusy('');}}

 async function showWebhook(){if(busy||!flowRef.current)return;setBusy('Webhook 주소 확인 중');setError('');try{
  const current=dirty?await save():flowRef.current;
  const result=await request<WebhookInfo>('/api/webhook?workflowId='+encodeURIComponent(current.id));
  setWebhookInfo(result);
 }catch(cause){setError((cause as Error).message);}finally{setBusy('');}}

 async function cancelExecution(){if(!execution||!['queued','running','waiting'].includes(execution.status)||busy)return;setBusy('실행 중지 중');setError('');try{
  await request('/api/executions/cancel',{id:execution.id});
  const result=await request<{execution:Execution}>('/api/executions?id='+encodeURIComponent(execution.id));
  setExecution(result.execution);
 }catch(cause){setError((cause as Error).message);}finally{setBusy('');}}

 async function retryExecution(){if(!execution||!['error','canceled'].includes(execution.status)||busy)return;setBusy('재시도 중');setError('');try{
  const result=await request<{execution:Execution}>('/api/executions/retry',{id:execution.id});
  setExecution(result.execution);setHistory(items=>items?[result.execution,...items]:items);
 }catch(cause){setError((cause as Error).message);}finally{setBusy('');}}

 async function removeFlow(){if(busy||!flowRef.current)return;if(!window.confirm('이 워크플로우를 삭제할까요? 자동 실행은 중지되며 기존 실행 기록은 보관됩니다.'))return;
  setBusy('삭제 중');setError('');try{
   await request('/api/workflows?id='+encodeURIComponent(workflowId),undefined,'DELETE');
   setDirty(false);onCanLeaveChange?.(()=>true);onBack();
  }catch(cause){setError((cause as Error).message);}finally{setBusy('');}
 }

 if(loading)return <main className="entry-workspace-main external-studio" aria-label="외부 서비스 워크플로우 설계"><p role="status">워크플로우를 불러오고 있습니다.</p></main>;
 if(!flow)return <main className="entry-workspace-main external-studio" aria-label="외부 서비스 워크플로우 설계"><div className="external-studio-heading"><h1>워크플로우를 열 수 없습니다</h1><button className="as-button" onClick={onBack}><ArrowLeft/>작업 라이브러리</button></div><p role="alert">{error}</p></main>;

 return <main className="entry-workspace-main as-app external-studio" aria-label="외부 서비스 워크플로우 설계">
  <div className="external-studio-heading"><div><button className="as-text-button" onClick={onBack}><ArrowLeft size={15}/>작업 라이브러리</button><p className="as-eyebrow">외부 서비스 워크플로우</p><h1>{flow.name}</h1><p>대화로 흐름을 수정하고 각 작업의 연결을 확인하세요.</p></div><div className="as-design-actions"><span className={'as-status'+(flow.active?' ready':'')}>{flow.active?'자동 실행 중':'초안'}</span><button className="as-button" onClick={()=>void reload()} disabled={!!busy}><RefreshCw/>새로고침</button><button className="as-button as-primary" onClick={()=>void saveClick()} disabled={!!busy||!dirty}><Save/>{busy==='저장 중'?'저장 중…':'저장'}</button></div></div>
  {error&&<p className="external-studio-alert" role="alert"><AlertCircle size={17}/>{error}</p>}
  {notice&&<p className="external-studio-notice" role="status">{notice}</p>}
  {willDeactivate&&<p className="external-studio-alert" role="status">변경 내용을 저장하면 현재 자동 실행이 해제됩니다. 저장 후 다시 켜야 합니다.</p>}
  <div className="external-studio-grid">
   <section className="as-conversation" aria-label="대화로 설계 수정"><header className="as-panel-header"><span><MessageSquare size={17}/>대화로 설계 수정</span><button type="button" className="as-text-button" onClick={()=>void restartConversation()} disabled={!!busy}>대화 새로 시작</button></header><div className="as-messages" role="log" aria-label="설계 대화">
    {!conversation.length&&<div className="as-welcome"><h2>무엇을 바꿀까요?</h2><p>작업 순서, 조건, 연결할 서비스를 설명하면 설계 초안을 제안합니다.</p></div>}
    {conversation.map((turn,index)=><article className={'as-message '+turn.role} key={index}><span className="as-message-label">{turn.role==='user'?'나':'설계 도우미'}</span><p>{turn.content}</p></article>)}
    {designResult?.summary&&<p className="external-studio-summary">{designResult.summary}</p>}
    {!!designResult?.checklist?.length&&<div className="external-studio-checklist"><strong>실행 전에 확인할 내용</strong><ul>{designResult.checklist.map((item,index)=><li key={index}>{item}</li>)}</ul></div>}
    {!!designResult?.questions?.length&&<div className="external-studio-checklist"><strong>추가로 알려주세요</strong><ul>{designResult.questions.map((item,index)=><li key={index}>{item.question}</li>)}</ul></div>}
   </div><form className="as-composer" onSubmit={event=>void sendPrompt(event)}><textarea ref={promptRef} aria-label="수정할 내용" value={prompt} onChange={event=>setPrompt(event.target.value)} placeholder="예: API 응답에서 필요한 값만 고르고 Slack으로 보내줘" maxLength={4000} disabled={!!busy}/><div className="as-composer-footer"><small>확인 질문에는 이어서 답해주세요.</small><button className="as-button as-primary" type="submit" disabled={!!busy||prompt.trim().length<5}>{busy==='설계 중'?<Loader2 className="animate-spin"/>:<Check/>}답변 보내기</button></div></form></section>
   <section className="as-workspace" aria-label="워크플로우 설계 미리보기"><WorkflowPreview workflow={preview} selectedId={selectedId} onSelect={node=>setSelectedId(node.id)}/></section>
  </div>
  <section className="external-studio-settings" aria-label="워크플로우 설정"><div className="external-studio-section-heading"><div><h2>설계와 연결</h2><p>작업을 선택해 세부 설정을 확인하고 바꿀 수 있습니다.</p></div><div className="as-design-actions"><button className="as-button" onClick={()=>{promptRef.current?.focus();promptRef.current?.scrollIntoView({block:'center'});}}>설계 수정</button><button className="as-button" onClick={()=>setSettingsOpen(true)}><Settings2/>자동화 설정</button><button className="as-button" onClick={onServiceConnect}><Unplug/>서비스 연결</button></div></div>
   <label className="external-studio-name">워크플로우 이름<input aria-label="워크플로우 이름" value={flow.name} onChange={event=>change({...flow,name:event.target.value})} maxLength={120}/></label>
   <div className="external-studio-steps" aria-label="작업 단계 목록"><h3>작업 단계</h3><div>{flow.nodes.map((node,index)=><button type="button" key={node.id} className="external-studio-step" onClick={()=>{setSelectedId(node.id);setNodeOpen(true);}} aria-label={node.name+' 설정'}><span>{index+1}</span><strong>{node.name}</strong><small>{nodeInfo(node)?.category||'작업'}</small></button>)}</div></div>
   {selected?<div className="external-studio-selected"><div><strong>{selected.name}</strong><p>{nodeInfo(selected)?.description||'가져온 작업입니다. 설정을 확인해주세요.'}</p></div><button className="as-button" onClick={()=>setNodeOpen(true)}>작업 설정</button></div>:<p className="external-studio-empty">위 흐름에서 작업을 선택하면 설정을 확인할 수 있습니다.</p>}
   {flow.intentReview&&<div className="external-studio-review"><h3>요구사항 반영 확인</h3>{requirements.length?<ul>{requirements.map(item=><li key={item.id}><strong>{item.description}</strong><span>{item.nodeIds?.map(id=>flow.nodes.find(node=>node.id===id)?.name||'삭제된 작업').join(' → ')||'연결된 작업 없음'}</span></li>)}</ul>:<p>연결할 요구사항이 있는지 설계를 확인해주세요.</p>}<label><input type="checkbox" checked={!!flow.intentReview.reviewed} disabled={!reviewReady} onChange={event=>change({...flow,intentReview:{...flow.intentReview,reviewed:event.target.checked}})}/> 단계와 연결이 요청 내용을 반영하는지 확인했습니다.</label>{!reviewReady&&<p role="alert">반영되지 않은 요구사항이나 삭제된 작업이 있어 저장할 수 없습니다. 대화에서 수정해주세요.</p>}</div>}
   <div className="external-studio-danger"><button type="button" className="as-text-button" onClick={()=>void removeFlow()} disabled={!!busy}><Trash2 size={15}/>워크플로우 삭제</button></div>
  </section>
  <section className="external-studio-run" aria-label="워크플로우 실행"><div className="external-studio-section-heading"><div><h2>실행 확인</h2><p>시험 실행은 외부 전송을 수행하지 않습니다. 실제 실행은 연결된 서비스에 작업을 요청합니다.</p></div><div className="as-design-actions">{flow.nodes.some(node=>node.type==='n8n-nodes-base.webhook')&&<button className="as-button" onClick={()=>void showWebhook()} disabled={!!busy}>Webhook 주소</button>}<button className="as-button" onClick={()=>void toggleActive()} disabled={!!busy}>{flow.active?'자동 실행 끄기':'자동 실행 켜기'}</button></div></div>{webhookInfo&&<div className="external-studio-webhook"><strong>{webhookInfo.active?'Webhook 사용 중':'자동 실행을 켜면 사용할 수 있습니다.'}</strong><code>{webhookInfo.httpMethod} {webhookInfo.url}</code><small>주소에 인증 정보가 포함될 수 있습니다. 필요한 서비스에만 전달하세요.</small></div>}<details><summary>시작 데이터 또는 파일 입력</summary><div className="external-studio-input"><FileInput onChange={setFileInput} onBusyChange={setFileBusy}/><label>시작 데이터 (JSON)<textarea aria-label="시작 데이터 (JSON)" value={inputText} onChange={event=>setInputText(event.target.value)} disabled={!!fileInput||!!busy} rows={4}/></label></div></details><div className="as-design-actions"><button className="as-button" onClick={()=>void startRun('learning')} disabled={!!busy||fileBusy}><Play/>시험 실행</button><button className="as-button as-primary" onClick={()=>void startRun('live')} disabled={!!busy||fileBusy}><Play/>실제 실행</button><button className="as-button" onClick={()=>void loadHistory()} disabled={!!busy}>최근 실행 기록</button></div>
   {history&&<div className="external-studio-history" aria-label="최근 실행 기록">{history.length?<ul>{history.slice(0,20).map(item=><li key={item.id}><button className="as-text-button" onClick={()=>setExecution(item)}>{item.id.slice(0,8)} · {item.mode==='live'?'실제':'시험'} · {item.status} · {new Date(item.startedAt).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})}</button></li>)}</ul>:<p>최근 실행 기록이 없습니다.</p>}</div>}
   {execution&&<div className="external-studio-result" role="status"><div className="external-studio-result-heading"><h3>실행 결과 · {({success:'완료',error:'실패',queued:'대기',running:'실행 중',waiting:'입력 대기',canceled:'중지'} as Record<string,string>)[execution.status]}</h3>{['queued','running','waiting'].includes(execution.status)?<button className="as-button" onClick={()=>void cancelExecution()} disabled={!!busy}>실행 중지</button>:['error','canceled'].includes(execution.status)?<button className="as-button" onClick={()=>void retryExecution()} disabled={!!busy}>재시도</button>:null}</div><p>실행 ID {execution.id.slice(0,8)} · {execution.mode==='live'?'실제 실행':'시험 실행'}</p>{execution.error&&<p role="alert">{execution.error}</p>}<ol>{execution.runs.map((item,index)=><li key={index}><strong>{item.name}</strong> · {item.status==='success'?'완료':item.status==='error'?'실패':'건너뜀'}{item.error&&<p role="alert">{item.error}</p>}</li>)}</ol>{execution.runs.at(-1)?.output?.[0]&&<DataView items={execution.runs.at(-1)!.output[0]} executionId={execution.id} runIndex={execution.runs.length-1}/>}</div>}
  </section>
  <NodeEditor node={selected} run={execution?.runs.findLast(item=>item.nodeId===selected?.id)} open={nodeOpen} onClose={()=>setNodeOpen(false)} onApply={applyNode} onDelete={()=>{if(selected){change(deleteNode(flow,selected.id));setSelectedId(null);setNodeOpen(false);}}} onRun={node=>{if(applyNode(node))void startRun('learning',flowRef.current!);}} beginner credentials={credentials} onConnect={onServiceConnect} timezone={String(flow.settings?.timezone||'Asia/Seoul')} workflows={allFlows} currentFlowId={flow.id} execution={execution}/>
  <WorkflowSettings open={settingsOpen} onClose={()=>setSettingsOpen(false)} flow={flow} workflows={allFlows} onApply={settings=>change({...flow,settings})}/>
 </main>;
}
