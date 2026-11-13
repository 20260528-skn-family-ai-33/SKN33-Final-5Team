import {useEffect,useRef,useState} from 'react';
import {AlertCircle,ArrowRight,CalendarClock,Plus,RefreshCw,Search,X} from 'lucide-react';
import {request} from '../components/flow/connection-panel';
import {scheduleDescription,scheduleDateNote,type ScheduleSpec} from '../lib/schedule-spec';
import {ScheduleSettings,type ManagedSchedule} from './schedule-settings';
import './scheduled-workflows.css';

type WorkflowSummary={id:string;version:number;config:{name:string;purpose:string;schedule?:ScheduleSpec|null}};
type ScheduledWorkflow={agent:WorkflowSummary;schedule:ManagedSchedule|null};
type ExternalFlow={id:string;name:string;active?:boolean;nodes:{name:string;type:string;disabled?:boolean;parameters?:{rule?:{interval?:{field?:string;triggerAtHour?:number;triggerAtMinute?:number;triggerAtDayOfWeek?:number[];triggerAtDay?:number;expression?:string;secondsInterval?:number;minutesInterval?:number;hoursInterval?:number}[]}}}[];settings?:{timezone?:string};schedule?:{nextRunAt?:number}};
type Status='active'|'paused'|'unconfigured';
type ScheduleListRow={id:string;name:string;purpose:string;description:string;timezone:string;status:Status;nextRunAt?:string|number;kind:'agent'|'external'};
const statusLabels:Record<Status,string>={active:'활성',paused:'중지',unconfigured:'설정 확인 필요'};

function statusOf({agent,schedule}:ScheduledWorkflow):Status {
 if(!schedule||schedule.agent_version!==undefined&&schedule.agent_version!==agent.version)return 'unconfigured';
 return schedule.active?'active':'paused';
}
function externalScheduleDescription(flow:ExternalFlow):string {
 const rule=flow.nodes.find(node=>!node.disabled&&node.type.endsWith('.scheduleTrigger'))?.parameters?.rule?.interval?.[0];
 if(!rule)return '일정 설정 필요';
 const time=String(rule.triggerAtHour??9).padStart(2,'0')+':'+String(rule.triggerAtMinute??0).padStart(2,'0');
 if(rule.field==='days')return '매일 '+time;
 if(rule.field==='weeks')return '매주 '+(rule.triggerAtDayOfWeek||[]).map(day=>['일','월','화','수','목','금','토'][day]+'요일').join(' · ')+' '+time;
 if(rule.field==='months')return '매월 '+(rule.triggerAtDay??1)+'일 '+time;
 if(rule.field==='cronExpression')return 'Cron '+(rule.expression||'설정 확인 필요');
 if(rule.field==='seconds'||rule.field==='minutes'||rule.field==='hours')return (rule[`${rule.field}Interval` as 'secondsInterval'|'minutesInterval'|'hoursInterval']??1)+({seconds:'초',minutes:'분',hours:'시간'}[rule.field]||'')+'마다';
 return '일정 설정 필요';
}
function formatNextAt(value:string|number|undefined):string {
 const date=new Date(value??NaN);
 if(Number.isNaN(date.valueOf()))return '실행 시각 확인 필요';
 try{return new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'long',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(date);}
 catch{return '실행 시각 확인 필요';}
}

export function ScheduledWorkflows({onOpen,onOpenExternal,onCreate,onCanLeaveChange}:{onOpen:(id:string)=>void;onOpenExternal:(id:string)=>void;onCreate:()=>void;onCanLeaveChange?:(guard:()=>boolean)=>void}) {
 const [workflows,setWorkflows]=useState<ScheduledWorkflow[]>([]),[agents,setAgents]=useState<WorkflowSummary[]>([]),[externalFlows,setExternalFlows]=useState<ExternalFlow[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState('');
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
   const [result,listed,external]=await Promise.all([
    request<{workflows:ScheduledWorkflow[]}>('/api/agent-builder/schedules'),
    request<{agents:WorkflowSummary[]}>('/api/agent-builder/agents'),
    request<{workflows:ExternalFlow[]}>('/api/workflows'),
   ]);
   if(!Array.isArray(result?.workflows)||!Array.isArray(listed?.agents)||!Array.isArray(external?.workflows))throw new Error('예약 목록 응답을 확인하지 못했습니다. 다시 시도해주세요.');
   if(token===generation.current){setWorkflows(result.workflows);setAgents(listed.agents);setExternalFlows(external.workflows);}
  }catch(err){if(token===generation.current)setError((err as Error).message);}
  finally{if(token===generation.current)setLoading(false);}
 }
 useEffect(()=>{void load();return()=>{generation.current++;};},[]);
 const externalScheduled=externalFlows.filter(flow=>flow.nodes.some(node=>!node.disabled&&node.type.endsWith('.scheduleTrigger')));
 const rows:ScheduleListRow[]=[
  ...workflows.map(row=>({id:row.agent.id,name:row.agent.config.name,purpose:row.agent.config.purpose,description:row.schedule?.spec?scheduleDescription(row.schedule.spec):row.agent.config.schedule?scheduleDescription(row.agent.config.schedule):'일정 설정 필요',timezone:'Asia/Seoul',status:statusOf(row),nextRunAt:row.schedule?.next_run_at,kind:'agent' as const})),
  ...externalScheduled.map(flow=>({id:flow.id,name:flow.name,purpose:flow.nodes.filter(node=>!node.type.endsWith('Trigger')).slice(0,3).map(node=>node.name).join(' · ')||'외부 서비스 워크플로우',description:externalScheduleDescription(flow),timezone:flow.settings?.timezone||'Asia/Seoul',status:flow.active?flow.schedule?.nextRunAt?'active' as const:'unconfigured' as const:'paused' as const,nextRunAt:flow.schedule?.nextRunAt,kind:'external' as const})),
 ];
 const counts={active:0,paused:0,unconfigured:0};
 for(const row of rows)counts[row.status]++;
 const search=query.trim().toLocaleLowerCase();
 const matches=rows.filter(row=>(filter==='all'||row.status===filter)&&(row.name+' '+row.purpose).toLocaleLowerCase().includes(search));
 const rank:Record<Status,number>={active:0,paused:1,unconfigured:2};
 matches.sort((a,b)=>rank[a.status]-rank[b.status]||
  (a.status==='active'?(new Date(a.nextRunAt??NaN).valueOf()||0)-(new Date(b.nextRunAt??NaN).valueOf()||0):0)||a.name.localeCompare(b.name,'ko'));
 const available=agents.filter(agent=>!workflows.some(row=>row.agent.id===agent.id));
 const availableExternal=externalFlows.filter(flow=>flow.nodes.length>1&&!externalScheduled.some(scheduled=>scheduled.id===flow.id));
 const managedRow=workflows.find(row=>row.agent.id===manageId);
 const managedAgent=managedRow?.agent??agents.find(agent=>agent.id===manageId);
 function resetManager(){dirtyChanged(false);setAdding(false);setManageId('');}
 function closeManager(){if(canLeave())resetManager();}
 function toggleAdding(){if(!canLeave())return;dirtyChanged(false);setAdding(!adding);setManageId('');setNotice('');}
 function openManager(id:string){if(id===manageId&&!adding)return;if(!canLeave())return;dirtyChanged(false);setAdding(false);setManageId(id);setNotice('');}
 function selectWorkflow(id:string){if(id===manageId||!canLeave())return;if(id.startsWith('external:')){navigate(()=>onOpenExternal(id.slice('external:'.length)));return;}dirtyChanged(false);setManageId(id);}
 function navigate(action:()=>void){if(onCanLeaveChange||canLeave())action();}
 function changed(schedule:ManagedSchedule|null) {
  if(!managedAgent)return;
  setWorkflows(prior=>schedule?prior.some(row=>row.agent.id===managedAgent.id)?prior.map(row=>row.agent.id===managedAgent.id?{...row,schedule}:row):[...prior,{agent:managedAgent,schedule}]:prior.filter(row=>row.agent.id!==managedAgent.id));
  setNotice(schedule?managedAgent.config.name+'의 예약 설정을 저장했습니다.':managedAgent.config.name+'의 예약을 해제했습니다. 워크플로우는 유지됩니다.');
  if(schedule)setAdding(false);else resetManager();
 }
 return <section className="scheduled-workflows" aria-labelledby="schedules-heading">
  <div className="entry-page-heading"><div><h1 id="schedules-heading">예약 관리</h1></div><div className="schedule-page-actions"><button type="button" className="as-button" disabled={loading||busy} onClick={toggleAdding}><Plus size={15}/>기존 워크플로우에 예약 추가</button><button type="button" className="as-button" disabled={busy} onClick={()=>navigate(onCreate)}>새 예약 워크플로우</button><button type="button" className="as-button" aria-label="예약 목록 새로고침" disabled={loading||busy} onClick={()=>void load()}><RefreshCw size={15}/></button></div></div>
  {notice&&<p className="schedule-page-notice" role="status">{notice}</p>}
  {loading?<p className="entry-loading" role="status">예약 목록을 불러오고 있습니다.</p>:error?<div className="as-error" role="alert"><AlertCircle size={16}/><span>{error}</span><button type="button" onClick={()=>void load()}>다시 시도</button></div>:<>
   {(adding||managedAgent)&&<div className="schedule-manager"><div className="schedule-manager-heading"><h2>{adding?'기존 워크플로우에 예약 추가':managedAgent?.config.name+' · 일정 관리'}</h2><button type="button" className="as-button" aria-label="예약 설정 닫기" disabled={busy} onClick={closeManager}><X size={16}/></button></div>
    {adding&&<><label className="schedule-workflow-picker">예약할 워크플로우<select aria-label="예약할 워크플로우" value={manageId} disabled={busy} onChange={event=>selectWorkflow(event.target.value)}><option value="">워크플로우를 선택하세요</option>{available.length>0&&<optgroup label="내 워크플로우">{available.map(agent=><option key={agent.id} value={agent.id}>{agent.config.name}</option>)}</optgroup>}{availableExternal.length>0&&<optgroup label="외부 서비스 워크플로우">{availableExternal.map(flow=><option key={flow.id} value={'external:'+flow.id}>{flow.name}</option>)}</optgroup>}</select></label>{available.length+availableExternal.length===0&&<p className="schedule-manager-help">예약을 추가할 워크플로우가 없습니다. 새 워크플로우를 만들거나 아래에서 기존 예약을 수정하세요.</p>}</>}
    {managedAgent&&<><ScheduleSettings key={managedAgent.id} workflowId={managedAgent.id} workflowVersion={managedAgent.version} schedule={managedRow?.schedule??null} onChange={changed} onBusyChange={busyChanged} onDirtyChange={dirtyChanged}/><button type="button" className="as-button schedule-detail-link" disabled={busy} onClick={()=>navigate(()=>onOpen(managedAgent.id))}>예약 자료·실행 확인<ArrowRight size={14}/></button></>}
   </div>}
   <div className="schedule-overview" aria-label="예약 현황"><span>전체 <strong>{rows.length}</strong></span><span>활성 <strong>{counts.active}</strong></span><span>중지 <strong>{counts.paused}</strong></span>{counts.unconfigured>0&&<span>설정 확인 필요 <strong>{counts.unconfigured}</strong></span>}</div>
   {rows.length>0?<>
    <div className="entry-list-tools schedule-list-tools"><label><Search size={17} aria-hidden="true"/><input aria-label="예약 워크플로우 검색" placeholder="이름이나 업무 내용 검색" value={query} onChange={event=>setQuery(event.target.value)}/></label><select aria-label="예약 상태 필터" value={filter} onChange={event=>setFilter(event.target.value as 'all'|Status)}><option value="all">전체 상태</option><option value="active">활성</option><option value="paused">중지</option><option value="unconfigured">설정 확인 필요</option></select></div>
    {matches.length>0?<div className="schedule-table-container"><table className="schedule-table"><caption className="sr-only">예약 워크플로우 목록</caption><thead><tr><th scope="col">워크플로우</th><th scope="col">반복 일정</th><th scope="col">상태</th><th scope="col">다음 실행</th><th scope="col"><span className="sr-only">관리</span></th></tr></thead><tbody>{matches.map(row=>{
     const spec=row.kind==='agent'?workflows.find(item=>item.agent.id===row.id)?.schedule?.spec??workflows.find(item=>item.agent.id===row.id)?.agent.config.schedule:undefined;
     return <tr key={row.kind+'-'+row.id} data-schedule-id={row.id}><td data-label="워크플로우"><h2>{row.name}</h2><p>{row.purpose}</p></td><td data-label="반복 일정"><span>{row.description}</span><small>{row.timezone==='Asia/Seoul'?'서울 표준시':row.timezone}</small>{spec&&scheduleDateNote(spec)&&<small>{scheduleDateNote(spec)}</small>}</td><td data-label="상태"><span className={'schedule-status '+row.status}>{statusLabels[row.status]}</span></td><td data-label="다음 실행"><span>{row.status==='paused'?'예약 중지됨':row.status==='unconfigured'?'예약 설정 확인 필요':formatNextAt(row.nextRunAt)}</span></td><td><div className="schedule-row-actions">{row.kind==='agent'?<><button type="button" className="as-button" aria-label={row.name+' 일정 관리'} disabled={busy} onClick={()=>openManager(row.id)}>일정 관리</button><button type="button" className="as-button" aria-label={row.name+' 예약 자료·실행 확인'} disabled={busy} onClick={()=>navigate(()=>onOpen(row.id))}>상세 확인<ArrowRight size={14}/></button></>:<button type="button" className="as-button" aria-label={row.name+' 일정 관리'} disabled={busy} onClick={()=>navigate(()=>onOpenExternal(row.id))}>일정 관리<ArrowRight size={14}/></button>}</div></td></tr>;
    })}</tbody></table></div>:<div className="entry-empty"><Search size={24}/><h2>검색 결과가 없습니다.</h2><p>이름이나 상태 필터를 바꿔 확인하세요.</p><button type="button" className="as-button" onClick={()=>{setQuery('');setFilter('all');}}>검색 조건 초기화</button></div>}
   </>:<div className="entry-empty"><CalendarClock size={28}/><h2>등록한 예약이 없습니다.</h2><p>기존 워크플로우에 일정을 추가하거나, 예약 실행할 새 워크플로우를 만드세요.</p>{!adding&&<button type="button" className="as-button" onClick={toggleAdding}>기존 워크플로우에 예약 추가</button>}</div>}
  </>}
 </section>;

}
