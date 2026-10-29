import {useEffect,useRef,useState} from 'react';
import {ArrowRight,Workflow,History,Layers,Settings2,Plus,Search,RefreshCw,AlertCircle} from 'lucide-react';
import AgentStudio from './agent-studio';
import {request} from '../components/flow/connection-panel';
import './entry-app.css';
import LandingPage from './landing-page';
import {IntroDemo} from './intro-demo';
export type EntryRoute='intro'|'preview'|'workflows'|'history'|'models'|'nodes'|'workflow'|'run';
type AgentSummary={id:string;version:number;model:string;updated_at:string;config:{name:string;purpose:string;tool_ids:string[];workflow?:{nodes:unknown[]}|null}};
type RunSummary={id:string;status:string;agent_version:number;created_at?:string;started_at?:string;error?:string;approval_status?:string|null};
function routeFromLocation():EntryRoute {const u=new URL(location.href),s=u.searchParams.get('screen');return ['intro','preview','workflows','history','models','nodes','workflow','run'].includes(s||'')?s as EntryRoute:u.searchParams.has('service')?'run':'intro';}
function date(value?:string){if(!value)return '저장 시각 없음';const d=new Date(value);return Number.isNaN(d.valueOf())?'저장 시각 확인 필요':d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});}
function Mark(){return <><span className="entry-mark">A</span><strong>A4B</strong></>;}
export default function EntryApp(){
 const [route,setRoute]=useState<EntryRoute>(routeFromLocation),[navigationKey,setNavigationKey]=useState(0);
 const guard=useRef<()=>boolean>(()=>true);
 const routeRef=useRef(route);routeRef.current=route;
 const context=useRef(location.search);
 const historyIndex=useRef(Number(window.history.state?.a4bIndex||0));
 const updateContext=()=>{context.current=location.search;historyIndex.current=Number(window.history.state?.a4bIndex||0);};
 useEffect(()=>{window.history.replaceState({...window.history.state,a4bIndex:historyIndex.current},'',location.href);const pop=()=>{const next=routeFromLocation(),internal=(r:string)=>['nodes','workflow','run'].includes(r),before=new URLSearchParams(context.current),after=new URLSearchParams(location.search);
  const remount=!internal(routeRef.current)||!internal(next)||before.get('service')!==after.get('service')||before.get('run')!==after.get('run');
  const targetIndex=Number(window.history.state?.a4bIndex||0);
  if(remount&&internal(routeRef.current)&&!guard.current()){const delta=historyIndex.current-targetIndex;if(delta)window.history.go(delta);return;}
  if(remount)setNavigationKey(n=>n+1);historyIndex.current=targetIndex;
  context.current=location.search;setRoute(next);
 };window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[]);
 function navigate(next:EntryRoute,service?:string,fresh=false,runId?:string,previewNode=false){
  if(fresh){localStorage.removeItem('a4b-builder-session');localStorage.removeItem('a4b-selected-agent');}
  const url=new URL(location.href);url.searchParams.set('screen',next);url.searchParams.delete('service');url.searchParams.delete('run');url.searchParams.delete('previewNode');if(previewNode)url.searchParams.set('previewNode','ai');if(runId)url.searchParams.set('run',runId);if(service)url.searchParams.set('service',service);
  historyIndex.current++;window.history.pushState({a4bIndex:historyIndex.current},'',url);context.current=location.search;setRoute(next);setNavigationKey(n=>n+1);window.scrollTo({top:0,behavior:'instant'});
 }
 const preview=(node=false)=>navigate('preview',undefined,false,undefined,node);
 if(route==='preview')return <IntroDemo node={new URLSearchParams(location.search).has('previewNode')} onView={preview} onHome={()=>navigate('intro')} onLibrary={()=>navigate('nodes')} onStart={()=>navigate('workflows')}/>;
 const newFlow=()=>navigate('workflow',undefined,true);
 if(['nodes','workflow','run'].includes(route))return <AgentStudio key={navigationKey} onCanLeaveChange={fn=>{guard.current=fn;}} onLocationChange={updateContext} onNavigate={next=>navigate(next)}/>;
 if(route==='intro')return <LandingPage onStart={()=>navigate('workflows')} onPreview={preview} onRoute={next=>navigate(next)}/>;
 return <WorkspaceHome route={route as 'workflows'|'history'|'models'} navigate={(next,service,runId)=>navigate(next,service,false,runId)} newFlow={newFlow}/>;
}
function WorkspaceHome({route,navigate,newFlow}:{route:'workflows'|'history'|'models';navigate:(next:EntryRoute,service?:string,runId?:string)=>void;newFlow:()=>void}){
 const [agents,setAgents]=useState<AgentSummary[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState(''),[query,setQuery]=useState('');
 const [connection,setConnection]=useState<any>(null),[accounts,setAccounts]=useState<{id:string;name:string;provider:string}[]>([]),[runRows,setRunRows]=useState<{agent:AgentSummary;run:RunSummary}[]>([]),[historyErrors,setHistoryErrors]=useState<string[]>([]);
 const generation=useRef(0);
 async function load(){const token=++generation.current;setLoading(true);setError('');setHistoryErrors([]);setRunRows([]);try{
  if(route==='models'){const [c,stored]=await Promise.all([request('/api/agent-builder/connection'),request('/api/credentials')]);if(token!==generation.current)return;setConnection(c.connection);setAccounts(stored.credentials||[]);return;}
  const a=await request('/api/agent-builder/agents');if(token!==generation.current)return;setAgents(a.agents||[]);
  if(route==='history'){const rows=await Promise.allSettled((a.agents||[]).map(async(agent:AgentSummary)=>{const result=await request('/api/agent-builder/agent?id='+encodeURIComponent(agent.id));return {agent,runs:result.tests||[]};}));if(token!==generation.current)return;setRunRows(rows.flatMap(r=>r.status==='fulfilled'?r.value.runs.map((run:RunSummary)=>({agent:r.value.agent,run})):[]).sort((a,b)=>(Date.parse(b.run.started_at||b.run.created_at||'')||0)-(Date.parse(a.run.started_at||a.run.created_at||'')||0)));setHistoryErrors(rows.flatMap((r,i)=>r.status==='rejected'?[(a.agents[i] as AgentSummary).config.name]:[]));}
 }catch(e){if(token===generation.current)setError((e as Error).message);}finally{if(token===generation.current)setLoading(false);}}
 useEffect(()=>{setQuery('');void load();return()=>{generation.current++;};},[route]);
 const matches=agents.filter(a=>(a.config.name+' '+a.config.purpose).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
 return <div className="entry-app"><header className="entry-header workspace-entry-header"><button className="entry-brand" onClick={()=>navigate('intro')} aria-label="서비스 소개로 이동"><Mark/></button><span className="entry-breadcrumb">/ <span>내 작업 공간</span></span><nav><span className="entry-local">로컬 작업 공간</span><button className="as-button as-primary" onClick={newFlow}><Plus size={15}/>새 워크플로우</button></nav></header><div className="entry-workspace-shell"><aside className="entry-sidebar"><nav aria-label="작업 공간">{([{route:'workflows',label:'워크플로우',Icon:Workflow},{route:'history',label:'실행 기록',Icon:History},{route:'nodes',label:'노드 라이브러리',Icon:Layers},{route:'models',label:'모델 설정',Icon:Settings2}] as const).map(({route:r,label,Icon})=><button key={r} aria-current={route===r?'page':undefined} className={route===r?'active':''} onClick={()=>navigate(r)}><Icon size={18}/>{label}</button>)}</nav><div className="entry-sidebar-bottom"><div><strong>흐름을 만드는 공간</strong><p>대화와 노드를 함께<br/>사용해 설계하세요.</p></div><button onClick={()=>navigate('intro')}>서비스 소개</button></div></aside><main className="entry-workspace-main"><div className="entry-page-heading"><div><h1>{route==='workflows'?'워크플로우':route==='history'?'실행 기록':'모델 설정'}</h1><p>{route==='workflows'?'대화로 만들고 직접 다듬은 흐름을 관리하세요.':route==='history'?'저장한 워크플로우의 실행 상태와 결과를 확인하세요.':'서버에 준비된 모델과 저장된 연결 프로필을 확인하세요.'}</p></div><button className="as-button" disabled={loading} onClick={()=>void load()} aria-label="목록 새로고침"><RefreshCw size={15}/></button></div>{error&&<div className="as-error" role="alert"><AlertCircle size={16}/><span>{error}</span><button onClick={()=>void load()}>다시 시도</button></div>}{loading&&<p className="entry-loading" role="status">{route==='history'?'실행 기록을 불러오고 있습니다.':'작업 공간을 불러오고 있습니다.'}</p>}
 {route==='workflows'&&<><section className="entry-list-hero"><div><h2>어떤 흐름을 만들어 볼까요?</h2><p>빈 캔버스에서 시작해도, 대화로 설계해도 좋습니다.</p></div><button className="as-button as-primary" onClick={newFlow}>새 워크플로우</button></section><div className="entry-list-tools"><label><Search size={18}/><input aria-label="워크플로우 검색" value={query} onChange={e=>setQuery(e.target.value)} placeholder="워크플로우 검색"/></label><span>전체 {agents.length}</span></div><div className="entry-workflow-grid">{matches.map(a=><article className="entry-workflow-card" key={a.id}><div><Workflow size={20}/><span className="as-status">저장 버전 {a.version}</span></div><h2>{a.config.name}</h2><p>{a.config.purpose}</p><footer><small>노드 {a.config.workflow?.nodes.length??a.config.tool_ids.length}개 · {date(a.updated_at)}</small><button onClick={()=>navigate('workflow',a.id)}>흐름 열기<ArrowRight size={14}/></button><button onClick={()=>navigate('run',a.id)}>실행 보기</button></footer></article>)}</div>{!loading&&!error&&!matches.length&&<section className="entry-empty"><Workflow size={26}/><h2>{query?'검색 결과가 없습니다.':'첫 워크플로우를 만들어보세요.'}</h2><p>{query?'이름이나 설명을 바꿔 검색하세요.':'업무와 자료를 설명하면 흐름을 구성할 수 있습니다.'}</p>{!query&&<button className="as-button" onClick={newFlow}>새 워크플로우</button>}</section>}</>}
 {route==='history'&&<>{historyErrors.length>0&&<div className="as-error" role="alert">일부 기록을 불러오지 못했습니다: {historyErrors.join(', ')}. 새로고침하여 다시 확인하세요.</div>}<div className="entry-history-list">{runRows.map(({agent:a,run})=><article key={run.id}><div><h2>{a.config.name}</h2><p>설정 v{run.agent_version} · {date(run.started_at||run.created_at)}</p></div><span className={'entry-run-status '+run.status}>{({succeeded:'완료',failed:'실패',running:'실행 중',needs_input:'답변 대기'})[run.status]||run.status}</span><button className="as-button" onClick={()=>navigate('run',a.id,run.id)}>실행 확인</button>{run.error&&<p className="entry-history-error">{run.error}</p>}</article>)}</div>{!loading&&!error&&!runRows.length&&<section className="entry-empty"><History size={26}/><h2>아직 실행 기록이 없습니다.</h2><p>저장한 워크플로우를 실행하면 여기에 표시됩니다.</p><button className="as-button" onClick={()=>navigate('workflows')}>워크플로우로 이동</button></section>}</>}
 {route==='models'&&<><section className="entry-model-card"><div><Settings2 size={22}/><span className="as-status">{connection?'서버 프로필 준비됨':'서버 프로필 없음'}</span></div><h2>{connection?.model||'모델 프로필을 준비해주세요.'}</h2><p>{connection?'서버에 설정된 연결을 설계와 실행에 사용합니다.':'현재 서버에서 제공하는 모델 프로필이 없습니다. 서버의 모델 설정을 확인하세요.'}</p><p className="entry-model-note">이 화면에서는 API 키를 입력하거나 표시하지 않습니다.</p></section><section className="entry-model-accounts"><h2>저장된 연결 프로필</h2>{accounts.length?accounts.map(a=><article key={a.id}><strong>{a.name}</strong><span>{a.provider}</span></article>):<p>저장된 연결 프로필이 없습니다.</p>}</section></>}
 </main></div></div>;
}
