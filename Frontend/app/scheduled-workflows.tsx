import {useEffect,useRef,useState} from 'react';
import {AlertCircle,ArrowRight,CalendarClock,Plus,RefreshCw,Search,X} from 'lucide-react';
import {request} from '../components/flow/connection-panel';
import {scheduleDescription,scheduleDateNote,type ScheduleSpec} from '../lib/schedule-spec';
import {ScheduleSettings,type ManagedSchedule} from './schedule-settings';
import './scheduled-workflows.css';

type WorkflowSummary={id:string;version:number;config:{name:string;purpose:string;schedule?:ScheduleSpec|null}};
type ScheduledWorkflow={agent:WorkflowSummary;schedule:ManagedSchedule|null};
type Status='active'|'paused'|'unconfigured';
const statusLabels:Record<Status,string>={active:'활성',paused:'중지',unconfigured:'설정 확인 필요'};

function statusOf({agent,schedule}:ScheduledWorkflow):Status {
 if(!schedule||schedule.agent_version!==undefined&&schedule.agent_version!==agent.version)return 'unconfigured';
 return schedule.active?'active':'paused';
}
function nextExecution(row:ScheduledWorkflow) {
 const status=statusOf(row);
 if(status==='paused')return '예약 중지됨';
 if(status==='unconfigured')return '예약 설정 확인 필요';
 const schedule=row.schedule!,value=new Date(schedule.next_run_at);
 if(Number.isNaN(value.valueOf()))return '실행 시각 확인 필요';
 try{return new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'long',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(value);}
 catch{return '실행 시각 확인 필요';}
}

export function ScheduledWorkflows({onOpen,onCreate,onCanLeaveChange}:{onOpen:(id:string)=>void;onCreate:()=>void;onCanLeaveChange?:(guard:()=>boolean)=>void}) {
 const [workflows,setWorkflows]=useState<ScheduledWorkflow[]>([]),[agents,setAgents]=useState<WorkflowSummary[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const [query,setQuery]=useState(''),[filter,setFilter]=useState<'all'|Status>('all');
 const [adding,setAdding]=useState(false),[manageId,setManageId]=useState(''),[busy,setBusy]=useState(false),[dirty,setDirty]=useState(false),[notice,setNotice]=useState('');
 const generation=useRef(0);
 const activity=useRef({busy:false,dirty:false}),leaveCallback=useRef(onCanLeaveChange);leaveCallback.current=onCanLeaveChange;
 function dirtyChanged(value:boolean){activity.current.dirty=value;setDirty(value);}
 function busyChanged(value:boolean){activity.current.busy=value;setBusy(value);}
 function canLeave(){return !activity.current.busy&&(!activity.current.dirty||window.confirm('저장하지 않은 예약 일정 변경이 있습니다. 변경 내용을 버리고 이동할까요?'));}
 useEffect(()=>{onCanLeaveChange?.(canLeave);},[busy,dirty,onCanLeaveChange]);
 useEffect(()=>{
  const beforeUnload=(event:BeforeUnloadEvent)=>{if(activity.current.busy||activity.current.dirty){event.preventDefault();event.returnValue='';}};
  window.addEventListener('beforeunload',beforeUnload);
  return()=>{window.removeEventListener('beforeunload',beforeUnload);leaveCallback.current?.(()=>true);};
 },[]);
 async function load() {
  if(!canLeave())return;
  const token=++generation.current;setLoading(true);setError('');
  try {
   const [result,listed]=await Promise.all([
    request<{workflows:ScheduledWorkflow[]}>('/api/agent-builder/schedules'),
    request<{agents:WorkflowSummary[]}>('/api/agent-builder/agents'),
   ]);
   if(!Array.isArray(result?.workflows)||!Array.isArray(listed?.agents))throw new Error('예약 목록 응답을 확인하지 못했습니다. 다시 시도해주세요.');
   if(token===generation.current){setWorkflows(result.workflows);setAgents(listed.agents);}
  }catch(err){if(token===generation.current)setError((err as Error).message);}
  finally{if(token===generation.current)setLoading(false);}
 }
 useEffect(()=>{void load();return()=>{generation.current++;};},[]);
 const counts={active:0,paused:0,unconfigured:0};
 for(const row of workflows)counts[statusOf(row)]++;
 const search=query.trim().toLocaleLowerCase();
 const matches=workflows.filter(row=>(filter==='all'||statusOf(row)===filter)&&(row.agent.config.name+' '+row.agent.config.purpose).toLocaleLowerCase().includes(search));
 const rank:Record<Status,number>={active:0,paused:1,unconfigured:2};
 matches.sort((a,b)=>rank[statusOf(a)]-rank[statusOf(b)]||
  (statusOf(a)==='active'?(Date.parse(a.schedule!.next_run_at)||0)-(Date.parse(b.schedule!.next_run_at)||0):0)||a.agent.config.name.localeCompare(b.agent.config.name,'ko'));
 const available=agents.filter(agent=>!workflows.some(row=>row.agent.id===agent.id));
 const managedRow=workflows.find(row=>row.agent.id===manageId);
 const managedAgent=managedRow?.agent??agents.find(agent=>agent.id===manageId);
 function resetManager(){dirtyChanged(false);setAdding(false);setManageId('');}
 function closeManager(){if(canLeave())resetManager();}
 function toggleAdding(){if(!canLeave())return;dirtyChanged(false);setAdding(!adding);setManageId('');setNotice('');}
 function openManager(id:string){if(id===manageId&&!adding)return;if(!canLeave())return;dirtyChanged(false);setAdding(false);setManageId(id);setNotice('');}
 function selectWorkflow(id:string){if(id===manageId||!canLeave())return;dirtyChanged(false);setManageId(id);}
 function navigate(action:()=>void){if(onCanLeaveChange||canLeave())action();}
 function changed(schedule:ManagedSchedule|null) {
  if(!managedAgent)return;
  setWorkflows(prior=>schedule?prior.some(row=>row.agent.id===managedAgent.id)?prior.map(row=>row.agent.id===managedAgent.id?{...row,schedule}:row):[...prior,{agent:managedAgent,schedule}]:prior.filter(row=>row.agent.id!==managedAgent.id));
  setNotice(schedule?managedAgent.config.name+'의 예약 설정을 저장했습니다.':managedAgent.config.name+'의 예약을 해제했습니다. 워크플로우는 유지됩니다.');
  if(schedule)setAdding(false);else resetManager();
 }
 return <section className="scheduled-workflows" aria-labelledby="schedules-heading">
  <div className="entry-page-heading"><div><h1 id="schedules-heading">예약 관리</h1><p>워크플로우에 연결한 일정과 자동 실행 상태를 관리하세요. 업무 내용은 워크플로우에서 수정할 수 있습니다.</p></div><div className="schedule-page-actions"><button type="button" className="as-button" disabled={loading||busy} onClick={toggleAdding}><Plus size={15}/>기존 워크플로우에 예약 추가</button><button type="button" className="as-button" disabled={busy} onClick={()=>navigate(onCreate)}>새 예약 워크플로우</button><button type="button" className="as-button" aria-label="예약 목록 새로고침" disabled={loading||busy} onClick={()=>void load()}><RefreshCw size={15}/></button></div></div>
  {notice&&<p className="schedule-page-notice" role="status">{notice}</p>}
  {loading?<p className="entry-loading" role="status">예약 목록을 불러오고 있습니다.</p>:error?<div className="as-error" role="alert"><AlertCircle size={16}/><span>{error}</span><button type="button" onClick={()=>void load()}>다시 시도</button></div>:<>
   {(adding||managedAgent)&&<div className="schedule-manager"><div className="schedule-manager-heading"><h2>{adding?'기존 워크플로우에 예약 추가':managedAgent?.config.name+' · 일정 관리'}</h2><button type="button" className="as-button" aria-label="예약 설정 닫기" disabled={busy} onClick={closeManager}><X size={16}/></button></div>
    {adding&&<><label className="schedule-workflow-picker">예약할 워크플로우<select aria-label="예약할 워크플로우" value={manageId} disabled={busy} onChange={event=>selectWorkflow(event.target.value)}><option value="">워크플로우를 선택하세요</option>{available.map(agent=><option key={agent.id} value={agent.id}>{agent.config.name}</option>)}</select></label>{available.length===0&&<p className="schedule-manager-help">예약을 추가할 워크플로우가 없습니다. 새 워크플로우를 만들거나 아래에서 기존 예약을 수정하세요.</p>}</>}
    {managedAgent&&<><ScheduleSettings key={managedAgent.id} workflowId={managedAgent.id} workflowVersion={managedAgent.version} schedule={managedRow?.schedule??null} onChange={changed} onBusyChange={busyChanged} onDirtyChange={dirtyChanged}/><button type="button" className="as-button schedule-detail-link" disabled={busy} onClick={()=>navigate(()=>onOpen(managedAgent.id))}>예약 자료·실행 확인<ArrowRight size={14}/></button></>}
   </div>}
   <div className="schedule-overview" aria-label="예약 현황"><span>전체 <strong>{workflows.length}</strong></span><span>활성 <strong>{counts.active}</strong></span><span>중지 <strong>{counts.paused}</strong></span>{counts.unconfigured>0&&<span>설정 확인 필요 <strong>{counts.unconfigured}</strong></span>}</div>
   {workflows.length>0?<>
    <div className="entry-list-tools schedule-list-tools"><label><Search size={17} aria-hidden="true"/><input aria-label="예약 워크플로우 검색" placeholder="이름이나 업무 내용 검색" value={query} onChange={event=>setQuery(event.target.value)}/></label><select aria-label="예약 상태 필터" value={filter} onChange={event=>setFilter(event.target.value as 'all'|Status)}><option value="all">전체 상태</option><option value="active">활성</option><option value="paused">중지</option><option value="unconfigured">설정 확인 필요</option></select></div>
    {matches.length>0?<div className="schedule-table-container"><table className="schedule-table"><caption className="sr-only">예약 워크플로우 목록</caption><thead><tr><th scope="col">워크플로우</th><th scope="col">반복 일정</th><th scope="col">상태</th><th scope="col">다음 실행</th><th scope="col"><span className="sr-only">관리</span></th></tr></thead><tbody>{matches.map(row=>{
     const {agent,schedule}=row,status=statusOf(row),spec=schedule?.spec??agent.config.schedule;
     return <tr key={agent.id} data-schedule-id={agent.id}><td data-label="워크플로우"><h2>{agent.config.name}</h2><p>{agent.config.purpose}</p></td><td data-label="반복 일정"><span>{spec?scheduleDescription(spec):'일정 설정 필요'}</span><small>서울 표준시</small>{spec&&scheduleDateNote(spec)&&<small>{scheduleDateNote(spec)}</small>}</td><td data-label="상태"><span className={'schedule-status '+status}>{statusLabels[status]}</span></td><td data-label="다음 실행"><span>{nextExecution(row)}</span></td><td><div className="schedule-row-actions"><button type="button" className="as-button" aria-label={agent.config.name+' 일정 관리'} disabled={busy} onClick={()=>openManager(agent.id)}>일정 관리</button><button type="button" className="as-button" aria-label={agent.config.name+' 예약 자료·실행 확인'} disabled={busy} onClick={()=>navigate(()=>onOpen(agent.id))}>상세 확인<ArrowRight size={14}/></button></div></td></tr>;
    })}</tbody></table></div>:<div className="entry-empty"><Search size={24}/><h2>검색 결과가 없습니다.</h2><p>이름이나 상태 필터를 바꿔 확인하세요.</p><button type="button" className="as-button" onClick={()=>{setQuery('');setFilter('all');}}>검색 조건 초기화</button></div>}
   </>:<div className="entry-empty"><CalendarClock size={28}/><h2>등록한 예약이 없습니다.</h2><p>기존 워크플로우에 일정을 추가하거나, 예약 실행할 새 워크플로우를 만드세요.</p>{!adding&&<button type="button" className="as-button" onClick={toggleAdding}>기존 워크플로우에 예약 추가</button>}</div>}
  </>}
 </section>;

}
