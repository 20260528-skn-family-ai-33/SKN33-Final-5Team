import {settleWithLimit} from '../lib/settle-with-limit';
import {useEffect,useRef,useState} from 'react';
import {ArrowRight,Workflow,History,Search,RefreshCw,AlertCircle,Pencil} from 'lucide-react';
import AgentStudio from './agent-studio';
import {TaskLibraryHeader,WorkspaceSidebar} from './workspace-nav';
import {UploadLibrary} from './upload-library';
import {WorkspaceHeader,WorkspaceHelp} from './workspace-header';
import {ServiceConnections} from './service-connections';
import {ScheduledWorkflows} from './scheduled-workflows';
import {WorkflowFolders,emptyOrganization,MAX_WORKFLOW_SELECTION,type WorkflowOrganization} from './workflow-folders';
import {WorkflowRenameDialog,renameWorkflow} from './workflow-rename-dialog';
import {request} from '../components/flow/connection-panel';
import './entry-app.css';
import {WorkspaceOverview} from './workspace-overview';
import LandingPage from './landing-page';
import {IntroDemo} from './intro-demo';
import {useAuth,SignIn} from './auth';
import VisualAutomation from './workspace';
export type EntryRoute='intro'|'preview'|'home'|'workflows'|'schedules'|'schedule'|'history'|'nodes'|'workflow'|'run'|'assets'|'connections';
type AgentSummary={id:string;version:number;model:string;updated_at:string;config:{name:string;purpose:string;tool_ids:string[];workflow?:{nodes:unknown[]}|null}};
type RunSummary={id:string;status:string;agent_version:number;created_at?:string;started_at?:string;error?:string;approval_status?:string|null};
type ExternalFlowSummary={id:string;name:string;nodes:{id:string}[];updatedAt?:string;active?:boolean;schedule?:{nextRunAt:string}};
function routeFromLocation():EntryRoute {const u=new URL(location.href),s=u.searchParams.get('screen');if(s==='models'||s==='automation')return 'workflows';return ['intro','preview','home','workflows','schedules','schedule','history','nodes','workflow','run','assets','connections'].includes(s||'')?s as EntryRoute:u.searchParams.has('service')?'run':'intro';}

function date(value?:string){if(!value)return '저장 시각 없음';const d=new Date(value);return Number.isNaN(d.valueOf())?'저장 시각 확인 필요':d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});}
export default function EntryApp(){
 const auth=useAuth();
 const [route,setRoute]=useState<EntryRoute>(routeFromLocation),[navigationKey,setNavigationKey]=useState(0);
 const clearDesign=()=>{
  localStorage.removeItem('a4b-builder-session');
  localStorage.removeItem('a4b-selected-agent');
 };
 const guard=useRef<()=>boolean>(()=>true);
 const routeRef=useRef(route);routeRef.current=route;
 const context=useRef(location.search);
 const historyIndex=useRef(Number(window.history.state?.a4bIndex||0));
 const updateContext=()=>{context.current=location.search;historyIndex.current=Number(window.history.state?.a4bIndex||0);};
 useEffect(()=>{if(new URLSearchParams(location.search).get('screen')==='models'){const legacy=new URL(location.href);legacy.searchParams.set('screen','workflows');window.history.replaceState(window.history.state,'',legacy);context.current=location.search;}window.history.replaceState({...window.history.state,a4bIndex:historyIndex.current},'',location.href);const pop=()=>{const next=routeFromLocation(),internal=(r:string)=>['nodes','workflow','run','schedule'].includes(r),before=new URLSearchParams(context.current),after=new URLSearchParams(location.search);
  const remount=!internal(routeRef.current)||!internal(next)||before.get('service')!==after.get('service')||before.get('run')!==after.get('run');
  const targetIndex=Number(window.history.state?.a4bIndex||0);
  if(remount&&(internal(routeRef.current)||routeRef.current==='assets'||routeRef.current==='schedules')&&!guard.current()){
   const delta=historyIndex.current-targetIndex;
   if(delta)window.history.go(delta);
   else {const previous=new URL(location.href);previous.search=context.current;window.history.replaceState({...window.history.state,a4bIndex:historyIndex.current},'',previous);}
   return;
  }
  if(remount){if(internal(routeRef.current)&&!internal(next))clearDesign();guard.current=()=>true;setNavigationKey(n=>n+1);}historyIndex.current=targetIndex;
  context.current=location.search;setRoute(next);
 };window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[]);
 function navigate(next:EntryRoute,service?:string,fresh=false,runId?:string,previewNode=false,initialPrompt?:string,initialFile?:File,externalId?:string){
  if((routeRef.current==='assets'||routeRef.current==='schedules')&&!guard.current())return false;
  guard.current=()=>true;
  if(fresh||next==='workflow'&&!service||['workflow','nodes','run','schedule'].includes(routeRef.current))clearDesign();
  const url=new URL(location.href);url.searchParams.set('screen',next);url.searchParams.delete('service');url.searchParams.delete('run');url.searchParams.delete('external');url.searchParams.delete('previewNode');url.searchParams.delete('executionMode');if(previewNode)url.searchParams.set('previewNode','ai');if(runId)url.searchParams.set('run',runId);if(service)url.searchParams.set('service',service);if(externalId)url.searchParams.set('external',externalId);
  historyIndex.current++;window.history.pushState({a4bIndex:historyIndex.current,initialPrompt,initialFile},'',url);context.current=location.search;setRoute(next);setNavigationKey(n=>n+1);window.scrollTo({top:0,behavior:'instant'});return true;

 }
 if(!['intro','preview'].includes(route)&&(auth.loading||auth.enabled&&!auth.user))return <SignIn onHome={()=>navigate('intro')}/>;
 const preview=(node=false)=>navigate('preview',undefined,false,undefined,node);
 if(route==='preview')return <IntroDemo node={new URLSearchParams(location.search).has('previewNode')} onView={preview} onLibrary={()=>navigate('nodes')} onStart={()=>navigate('home')}/>;
 const newFlow=()=>navigate('workflow',undefined,true);
 const startExample=(prompt:string,file?:File)=>navigate('workflow',undefined,true,undefined,false,prompt,file);
 const externalId=route==='workflow'?new URLSearchParams(location.search).get('external'):null;
 if(externalId)return <div className="entry-app"><WorkspaceHeader onHome={()=>navigate('home')}/><div className="entry-workspace-shell"><WorkspaceSidebar active="workflow" onNavigate={next=>navigate(next)} help={<WorkspaceHelp onStart={newFlow}/>}/><main className="entry-workspace-main entry-external-workflow-main"><VisualAutomation key={externalId} embedded initialWorkflowId={externalId} onServiceConnect={()=>navigate('connections')}/></main></div></div>;
 if(['nodes','workflow','run','schedule'].includes(route))return <AgentStudio key={navigationKey} onNewWorkflow={newFlow} initialPrompt={window.history.state?.initialPrompt} initialFile={window.history.state?.initialFile} onCanLeaveChange={fn=>{guard.current=fn;}} onLocationChange={updateContext} onNavigate={next=>navigate(next)} onExternalFlow={id=>navigate('workflow',undefined,false,undefined,false,undefined,undefined,id)}/>;
 if(route==='intro')return <LandingPage onStart={()=>navigate('home')}/>;
 return <div className="entry-app"><WorkspaceHeader onHome={()=>{if(route!=='home')navigate('home');}}/>
  <div className="entry-workspace-shell"><WorkspaceSidebar active={route} onNavigate={next=>{if(next!==route)navigate(next);}} help={<WorkspaceHelp onStart={newFlow} deferAutoStart={route==='home'}/>}/>
   <main className={'entry-workspace-main'+(route==='workflows'?' task-library-main':'')}>{route==='connections'?<ServiceConnections/>:route==='assets'?<UploadLibrary onCanLeaveChange={fn=>{guard.current=fn;}}/>:route==='schedules'?<ScheduledWorkflows onCanLeaveChange={fn=>{guard.current=fn;}} onOpen={id=>navigate('schedule',id)} onOpenExternal={id=>navigate('workflow',undefined,false,undefined,false,undefined,undefined,id)} onCreate={newFlow}/>:
    <WorkspaceHome route={route as 'home'|'workflows'|'history'} startExample={startExample} memberName={auth.user?.name?.trim()} historyService={route==='history'?new URLSearchParams(location.search).get('service'):null} navigate={(next,service,runId,external)=>navigate(next,service,false,runId,false,undefined,undefined,external)} newFlow={newFlow}/>}

   </main>
  </div>
 </div>;
}
function WorkspaceHome({route,historyService,navigate,newFlow,startExample,memberName}:{memberName?:string;route:'home'|'workflows'|'history';startExample:(prompt:string,file?:File)=>void;historyService:string|null;navigate:(next:EntryRoute,service?:string,runId?:string,externalId?:string)=>void;newFlow:()=>void}){
 const [agents,setAgents]=useState<AgentSummary[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState(''),[query,setQuery]=useState('');
 const [externalFlows,setExternalFlows]=useState<ExternalFlowSummary[]>([]);
 const [runRows,setRunRows]=useState<{agent:AgentSummary;run:RunSummary}[]>([]),[historyErrors,setHistoryErrors]=useState<string[]>([]);
 const [renaming,setRenaming]=useState<AgentSummary|null>(null);
 const [renameNotice,setRenameNotice]=useState('');
 const [organization,setOrganization]=useState<WorkflowOrganization>(emptyOrganization),[activeFolder,setActiveFolder]=useState('all'),[selectedWorkflows,setSelectedWorkflows]=useState<string[]>([]);
 const generation=useRef(0);
 async function load(){const token=++generation.current;setLoading(true);setError('');setHistoryErrors([]);setRunRows([]);try{

  const {agents:listed=[],organization:folders=emptyOrganization}=await request<{agents?:AgentSummary[];organization?:WorkflowOrganization}>('/api/agent-builder/agents');if(token!==generation.current)return;setAgents(listed);setOrganization(folders);setSelectedWorkflows([]);setActiveFolder(current=>current==='all'||current==='unfiled'||folders.folders.some(f=>f.id===current)?current:'all');
  if(route==='workflows')void request<{workflows?:ExternalFlowSummary[]}>('/api/workflows').then(result=>{if(token===generation.current)setExternalFlows((result.workflows||[]).filter(flow=>flow.nodes.length>1));}).catch(()=>{});
  if(route==='history'){const historyAgents=listed.filter(agent=>!historyService||agent.id===historyService);const rows=await settleWithLimit(historyAgents,4,async(agent:AgentSummary)=>{const result=await request('/api/agent-builder/agent?id='+encodeURIComponent(agent.id));return {agent,runs:result.tests||[]};},()=>token===generation.current);if(token!==generation.current)return;setRunRows(rows.flatMap(r=>r.status==='fulfilled'?r.value.runs.map((run:RunSummary)=>({agent:r.value.agent,run})):[]).sort((a,b)=>(Date.parse(b.run.started_at||b.run.created_at||'')||0)-(Date.parse(a.run.started_at||a.run.created_at||'')||0)));setHistoryErrors(rows.flatMap((r,i)=>r.status==='rejected'?[historyAgents[i].config.name]:[]));}
 }catch(e){if(token===generation.current)setError((e as Error).message);}finally{if(token===generation.current)setLoading(false);}}
 useEffect(()=>{setQuery('');setRenaming(null);setRenameNotice('');void load();return()=>{generation.current++;};},[route,historyService]);
 const historyAgent=historyService?agents.find(agent=>agent.id===historyService):undefined;
 const matches=agents.filter(a=>(activeFolder==='all'||(activeFolder==='unfiled'?!organization.assignments[a.id]:organization.assignments[a.id]===activeFolder))&&(a.config.name+' '+a.config.purpose).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
 const selectableMatches=matches.slice(0,MAX_WORKFLOW_SELECTION);
 const selectionLabel=matches.length>MAX_WORKFLOW_SELECTION?'현재 목록에서 500개 선택':'현재 목록 전체 선택';
 function toggleWorkflow(id:string,checked:boolean){
  setSelectedWorkflows(current=>{
   if(!checked)return current.filter(value=>value!==id);
   if(current.includes(id)||current.length>=MAX_WORKFLOW_SELECTION)return current;
   return [...current,id];
  });
 }
 const externalMatches=activeFolder==='all'?externalFlows.filter(flow=>flow.name.toLocaleLowerCase().includes(query.toLocaleLowerCase())):[];
 const refreshButton=<button className="as-button" disabled={loading} onClick={()=>void load()} aria-label="목록 새로고침" title="목록 새로고침"><RefreshCw size={15}/></button>;
 if(route==='home')return <WorkspaceOverview
  workflows={agents.slice().sort((a,b)=>Date.parse(b.updated_at)-Date.parse(a.updated_at)).slice(0,3)}
  memberName={memberName} loading={loading} error={error}
  onRetry={()=>void load()} onStart={startExample} onNavigate={navigate}/>;
 return <>{route==='workflows'
  ? <TaskLibraryHeader active="workflows" onNavigate={next=>{if(next==='nodes')navigate(next);}} actions={refreshButton}/>
  : <div className="entry-page-heading"><div><h1>실행 기록</h1>{historyService&&<button className="as-text-button" onClick={()=>navigate('history')}>전체 실행 기록</button>}</div>{refreshButton}</div>}{error&&<div className="as-error" role="alert"><AlertCircle size={16}/><span>{error}</span><button onClick={()=>void load()}>다시 시도</button></div>}{loading&&<p className="entry-loading" role="status">{route==='history'?'실행 기록을 불러오고 있습니다.':'작업 공간을 불러오고 있습니다.'}</p>}
 {route==='workflows'&&<>
  {renameNotice&&<p className="entry-rename-notice" role="status">{renameNotice}</p>}
  <div className="entry-list-tools" aria-label="워크플로우 목록 도구"><label><Search size={18}/><input aria-label="워크플로우 검색" value={query} onChange={e=>{setQuery(e.target.value);setSelectedWorkflows([]);}} placeholder="워크플로우 검색"/></label><span>{loading?'불러오는 중…':(matches.length+externalMatches.length)+'개'}</span></div>
  <WorkflowFolders organization={organization} onChange={setOrganization} active={activeFolder} onActive={id=>{setActiveFolder(id);setSelectedWorkflows([]);}} selected={selectedWorkflows} onMoved={()=>setSelectedWorkflows([])} disabled={loading||!!error}/>
  {!!matches.length&&<label className="workflow-select-all"><input type="checkbox" aria-label={selectionLabel} disabled={loading} checked={selectableMatches.every(a=>selectedWorkflows.includes(a.id))} onChange={e=>setSelectedWorkflows(e.target.checked?selectableMatches.map(a=>a.id):[])}/>{selectionLabel}</label>}
  {matches.length>MAX_WORKFLOW_SELECTION&&<p className="as-small-muted">한 번에 최대 500개까지 선택해 이동할 수 있습니다.</p>}
  <div className="entry-workflow-grid">{matches.map(a=><article className="entry-workflow-card" key={a.id}>
   <div><label className="workflow-card-select"><input type="checkbox" aria-label={a.config.name+' 선택'} checked={selectedWorkflows.includes(a.id)} disabled={loading||(!selectedWorkflows.includes(a.id)&&selectedWorkflows.length>=MAX_WORKFLOW_SELECTION)} onChange={e=>toggleWorkflow(a.id,e.target.checked)}/><Workflow size={20}/></label><span className="as-status">저장 버전 {a.version}</span></div>
   <div className="entry-workflow-title"><h2>{a.config.name}</h2><button type="button" aria-label={a.config.name+' 이름 변경'} title="이름 변경" disabled={loading} onClick={()=>{setRenameNotice('');setRenaming(a);}}><Pencil size={15} aria-hidden="true"/></button></div>
   <p>{a.config.purpose}</p><footer><small>작업 {a.config.workflow?.nodes.length??a.config.tool_ids.length}개 · {date(a.updated_at)}</small><button className="as-button as-secondary" onClick={()=>navigate('workflow',a.id)} aria-label={a.config.name+' 설계 열기'}>설계 열기</button><button className="as-button as-secondary" onClick={()=>navigate('history',a.id)} aria-label={a.config.name+' 실행 기록'}>실행 기록</button><button className="as-button as-secondary" onClick={()=>navigate('schedule',a.id)} aria-label={a.config.name+' 예약 관리'}>예약 관리</button><button className="as-button as-primary" onClick={()=>navigate('run',a.id)} aria-label={a.config.name+' 실행하기'}>실행하기<ArrowRight size={14}/></button></footer>

  </article>)}{externalMatches.map(flow=><article className="entry-workflow-card" key={flow.id}><div><Workflow size={20}/><span className="as-status">{flow.active?'자동 실행 중':'초안'}</span></div><div className="entry-workflow-title"><h2>{flow.name}</h2></div><p>연결된 서비스로 실행하는 워크플로우</p><footer><small>작업 {flow.nodes.length}개 · {date(flow.updatedAt)}</small><button className="as-button as-secondary" onClick={()=>navigate('workflow',undefined,undefined,flow.id)}>설계 열기</button></footer></article>)}</div>
  {renaming&&<WorkflowRenameDialog key={renaming.id} name={renaming.config.name} onClose={()=>setRenaming(null)} onSave={async name=>{
   const updated=await renameWorkflow(renaming,name);
   setAgents(current=>current.map(agent=>agent.id===updated.id?updated:agent));
   setRenameNotice('워크플로우 이름을 '+updated.config.name+'(으)로 변경했어요.');
  }}/>}
  {!loading&&!error&&!matches.length&&!externalMatches.length&&<section className="entry-empty"><Workflow size={26}/><h2>{query?'검색 결과가 없습니다.':agents.length?'이 폴더에 워크플로우가 없습니다.':'첫 워크플로우를 만들어보세요.'}</h2><p>{query?'이름이나 설명을 바꿔 검색하세요.':'업무와 자료를 설명하면 흐름을 구성할 수 있습니다.'}</p>{!query&&<button className="as-button" onClick={newFlow}>새 워크플로우</button>}</section>}
 </>}
 {route==='history'&&<>{historyErrors.length>0&&<div className="as-error" role="alert">일부 기록을 불러오지 못했습니다: {historyErrors.join(', ')}. 새로고침하여 다시 확인하세요.</div>}<div className="entry-history-list entry-run-history">{runRows.map(({agent:a,run})=><article key={run.id}><div><h2>{a.config.name}</h2><p>{run.started_at||run.created_at?date(run.started_at||run.created_at):'실행 시각 없음'}</p></div><span className={'entry-run-status '+run.status}>{({succeeded:'완료',failed:'실패',running:'실행 중',needs_input:'답변 대기'})[run.status]||run.status}</span><button className="as-button as-secondary" onClick={()=>navigate('run',a.id,run.id)}>결과 보기</button>{run.error&&<p className="entry-history-error">{run.error}</p>}</article>)}</div>{!loading&&!error&&!runRows.length&&<section className="entry-empty"><History size={26}/><h2>아직 실행 기록이 없습니다.</h2><p>저장한 워크플로우를 실행하면 여기에 표시됩니다.</p><button className="as-button" onClick={()=>historyAgent?navigate('run',historyAgent.id):navigate('workflows')}>{historyAgent?'워크플로우 실행 화면':'워크플로우로 이동'}</button></section>}</>}


 </>;
}
