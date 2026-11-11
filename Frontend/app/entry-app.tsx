import {settleWithLimit} from '../lib/settle-with-limit';
import {useEffect,useRef,useState} from 'react';
import {ArrowRight,Workflow,History,Search,RefreshCw,AlertCircle,Pencil} from 'lucide-react';
import AgentStudio from './agent-studio';
import {WorkspaceSidebar} from './workspace-nav';
import {UploadLibrary} from './upload-library';
import {WorkspaceHeader,WorkspaceHelp} from './workspace-header';
import {ServiceConnections} from './service-connections';
import {ScheduledWorkflows} from './scheduled-workflows';
import {WorkflowRenameDialog} from './workflow-rename-dialog';
import {request} from '../components/flow/connection-panel';
import './entry-app.css';
import LandingPage from './landing-page';
import {IntroDemo} from './intro-demo';
import {useAuth,SignIn} from './auth';
export type EntryRoute='intro'|'preview'|'workflows'|'schedules'|'schedule'|'history'|'nodes'|'workflow'|'run'|'assets'|'connections';
type AgentSummary={id:string;version:number;model:string;updated_at:string;config:{name:string;purpose:string;tool_ids:string[];workflow?:{nodes:unknown[]}|null}};
type RunSummary={id:string;status:string;agent_version:number;created_at?:string;started_at?:string;error?:string;approval_status?:string|null};
function routeFromLocation():EntryRoute {const u=new URL(location.href),s=u.searchParams.get('screen');if(s==='models')return 'workflows';return ['intro','preview','workflows','schedules','schedule','history','nodes','workflow','run','assets','connections'].includes(s||'')?s as EntryRoute:u.searchParams.has('service')?'run':'intro';}
function date(value?:string){if(!value)return '저장 시각 없음';const d=new Date(value);return Number.isNaN(d.valueOf())?'저장 시각 확인 필요':d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});}
export default function EntryApp(){
 const auth=useAuth();
 const [route,setRoute]=useState<EntryRoute>(routeFromLocation),[navigationKey,setNavigationKey]=useState(0);
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
  if(remount){guard.current=()=>true;setNavigationKey(n=>n+1);}historyIndex.current=targetIndex;
  context.current=location.search;setRoute(next);
 };window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[]);
 function navigate(next:EntryRoute,service?:string,fresh=false,runId?:string,previewNode=false){
  if((routeRef.current==='assets'||routeRef.current==='schedules')&&!guard.current())return false;
  guard.current=()=>true;
  if(fresh){localStorage.removeItem('a4b-builder-session');localStorage.removeItem('a4b-selected-agent');}
  const url=new URL(location.href);url.searchParams.set('screen',next);url.searchParams.delete('service');url.searchParams.delete('run');url.searchParams.delete('previewNode');url.searchParams.delete('executionMode');if(previewNode)url.searchParams.set('previewNode','ai');if(runId)url.searchParams.set('run',runId);if(service)url.searchParams.set('service',service);
  historyIndex.current++;window.history.pushState({a4bIndex:historyIndex.current},'',url);context.current=location.search;setRoute(next);setNavigationKey(n=>n+1);window.scrollTo({top:0,behavior:'instant'});return true;
 }
 if(!['intro','preview'].includes(route)&&(auth.loading||auth.enabled&&!auth.user))return <SignIn onHome={()=>navigate('intro')}/>;
 const preview=(node=false)=>navigate('preview',undefined,false,undefined,node);
 if(route==='preview')return <IntroDemo node={new URLSearchParams(location.search).has('previewNode')} onView={preview} onLibrary={()=>navigate('nodes')} onStart={()=>navigate('workflows')}/>;
 const newFlow=()=>navigate('workflow',undefined,true);
 const newScheduledFlow=newFlow;
 if(['nodes','workflow','run','schedule'].includes(route))return <AgentStudio key={navigationKey} onCanLeaveChange={fn=>{guard.current=fn;}} onLocationChange={updateContext} onNavigate={next=>navigate(next)}/>;
 if(route==='intro')return <LandingPage onStart={()=>navigate('workflows')} onPreview={preview} onRoute={next=>navigate(next)}/>;
 return <div className="entry-app"><WorkspaceHeader onNew={newFlow}/>
  <div className="entry-workspace-shell"><WorkspaceSidebar active={route} onNavigate={navigate} help={<WorkspaceHelp onStart={newFlow}/>}/>
   <main className="entry-workspace-main">{route==='connections'?<ServiceConnections/>:route==='assets'?<UploadLibrary onCanLeaveChange={fn=>{guard.current=fn;}}/>:route==='schedules'?<ScheduledWorkflows onCanLeaveChange={fn=>{guard.current=fn;}} onOpen={id=>navigate('schedule',id)} onCreate={newScheduledFlow}/>:
    <WorkspaceHome route={route as 'workflows'|'history'} historyService={route==='history'?new URLSearchParams(location.search).get('service'):null} navigate={(next,service,runId)=>navigate(next,service,false,runId)} newFlow={newFlow}/>}
   </main>
  </div>
 </div>;
}
function WorkspaceHome({route,historyService,navigate,newFlow}:{route:'workflows'|'history';historyService:string|null;navigate:(next:EntryRoute,service?:string,runId?:string)=>void;newFlow:()=>void}){
 const [agents,setAgents]=useState<AgentSummary[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState(''),[query,setQuery]=useState('');
 const [runRows,setRunRows]=useState<{agent:AgentSummary;run:RunSummary}[]>([]),[historyErrors,setHistoryErrors]=useState<string[]>([]);
 const [renaming,setRenaming]=useState<AgentSummary|null>(null);
 const [renameNotice,setRenameNotice]=useState('');
 const generation=useRef(0);
 async function load(){const token=++generation.current;setLoading(true);setError('');setHistoryErrors([]);setRunRows([]);try{

  const {agents:listed=[]}=await request<{agents?:AgentSummary[]}>('/api/agent-builder/agents');if(token!==generation.current)return;setAgents(listed);
  if(route==='history'){const historyAgents=listed.filter(agent=>!historyService||agent.id===historyService);const rows=await settleWithLimit(historyAgents,4,async(agent:AgentSummary)=>{const result=await request('/api/agent-builder/agent?id='+encodeURIComponent(agent.id));return {agent,runs:result.tests||[]};},()=>token===generation.current);if(token!==generation.current)return;setRunRows(rows.flatMap(r=>r.status==='fulfilled'?r.value.runs.map((run:RunSummary)=>({agent:r.value.agent,run})):[]).sort((a,b)=>(Date.parse(b.run.started_at||b.run.created_at||'')||0)-(Date.parse(a.run.started_at||a.run.created_at||'')||0)));setHistoryErrors(rows.flatMap((r,i)=>r.status==='rejected'?[historyAgents[i].config.name]:[]));}
 }catch(e){if(token===generation.current)setError((e as Error).message);}finally{if(token===generation.current)setLoading(false);}}
 useEffect(()=>{setQuery('');setRenaming(null);setRenameNotice('');void load();return()=>{generation.current++;};},[route,historyService]);
 const historyAgent=historyService?agents.find(agent=>agent.id===historyService):undefined;
 const matches=agents.filter(a=>(a.config.name+' '+a.config.purpose).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
 return <><div className="entry-page-heading"><div><h1>{route==='workflows'?'워크플로우':'실행 기록'}</h1><p>{route==='workflows'?'저장한 업무 흐름을 열어 수정하거나 실행하세요.':historyAgent?historyAgent.config.name+'의 이전 실행입니다.':'이전 실행의 완료 여부와 결과를 확인하세요.'}</p>{historyService&&<button className="as-text-button" onClick={()=>navigate('history')}>전체 실행 기록</button>}</div><button className="as-button" disabled={loading} onClick={()=>void load()} aria-label="목록 새로고침" title="목록 새로고침"><RefreshCw size={15}/></button></div>{error&&<div className="as-error" role="alert"><AlertCircle size={16}/><span>{error}</span><button onClick={()=>void load()}>다시 시도</button></div>}{loading&&<p className="entry-loading" role="status">{route==='history'?'실행 기록을 불러오고 있습니다.':'작업 공간을 불러오고 있습니다.'}</p>}
 {route==='workflows'&&<>
  {renameNotice&&<p className="entry-rename-notice" role="status">{renameNotice}</p>}
  <div className="entry-list-tools" aria-label="워크플로우 목록 도구"><label><Search size={18}/><input aria-label="워크플로우 검색" value={query} onChange={e=>setQuery(e.target.value)} placeholder="워크플로우 검색"/></label><span>{loading?'불러오는 중…':'전체 '+agents.length}</span></div>
  <div className="entry-workflow-grid">{matches.map(a=><article className="entry-workflow-card" key={a.id}>
   <div><Workflow size={20}/><span className="as-status">저장 버전 {a.version}</span></div>
   <div className="entry-workflow-title"><h2>{a.config.name}</h2><button type="button" aria-label={a.config.name+' 이름 변경'} title="이름 변경" disabled={loading} onClick={()=>{setRenameNotice('');setRenaming(a);}}><Pencil size={15} aria-hidden="true"/></button></div>
   <p>{a.config.purpose}</p><footer><small>작업 {a.config.workflow?.nodes.length??a.config.tool_ids.length}개 · {date(a.updated_at)}</small><button onClick={()=>navigate('workflow',a.id)} aria-label={a.config.name+' 설계 열기'}>설계 열기</button><button onClick={()=>navigate('schedule',a.id)} aria-label={a.config.name+' 예약 관리'}>예약 관리</button><button onClick={()=>navigate('history',a.id)} aria-label={a.config.name+' 실행 기록'}>실행 기록</button><button className="entry-run-link" onClick={()=>navigate('run',a.id)} aria-label={a.config.name+' 직접 실행'}>직접 실행<ArrowRight size={14}/></button></footer>
  </article>)}</div>
  {renaming&&<WorkflowRenameDialog key={renaming.id} name={renaming.config.name} onClose={()=>setRenaming(null)} onSave={async name=>{
   const result=await request<{agent:AgentSummary}>('/api/agent-builder/rename_agent',{id:renaming.id,version:renaming.version,name});
   setAgents(current=>current.map(agent=>agent.id===result.agent.id?result.agent:agent));
   setRenameNotice('워크플로우 이름을 '+result.agent.config.name+'(으)로 변경했어요.');
  }}/>}
  {!loading&&!error&&!matches.length&&<section className="entry-empty"><Workflow size={26}/><h2>{query?'검색 결과가 없습니다.':'첫 워크플로우를 만들어보세요.'}</h2><p>{query?'이름이나 설명을 바꿔 검색하세요.':'업무와 자료를 설명하면 흐름을 구성할 수 있습니다.'}</p>{!query&&<button className="as-button" onClick={newFlow}>새 워크플로우</button>}</section>}
 </>}
 {route==='history'&&<>{historyErrors.length>0&&<div className="as-error" role="alert">일부 기록을 불러오지 못했습니다: {historyErrors.join(', ')}. 새로고침하여 다시 확인하세요.</div>}<div className="entry-history-list">{runRows.map(({agent:a,run})=><article key={run.id}><div><h2>{a.config.name}</h2><p>설정 v{run.agent_version} · {date(run.started_at||run.created_at)}</p></div><span className={'entry-run-status '+run.status}>{({succeeded:'완료',failed:'실패',running:'실행 중',needs_input:'답변 대기'})[run.status]||run.status}</span><button className="as-button as-secondary" onClick={()=>navigate('run',a.id,run.id)}>실행 결과 보기</button>{run.error&&<p className="entry-history-error">{run.error}</p>}</article>)}</div>{!loading&&!error&&!runRows.length&&<section className="entry-empty"><History size={26}/><h2>아직 실행 기록이 없습니다.</h2><p>저장한 워크플로우를 실행하면 여기에 표시됩니다.</p><button className="as-button" onClick={()=>historyAgent?navigate('run',historyAgent.id):navigate('workflows')}>{historyAgent?'워크플로우 실행 화면':'워크플로우로 이동'}</button></section>}</>}


 </>;
}
