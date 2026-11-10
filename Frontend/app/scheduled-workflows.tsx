import {useEffect,useRef,useState} from 'react';
import {AlertCircle,ArrowRight,CalendarClock,RefreshCw,Search} from 'lucide-react';
import {request} from '../components/flow/connection-panel';
import {scheduleDescription,scheduleDateNote,type ScheduleSpec} from '../lib/schedule-spec';
import './scheduled-workflows.css';

type ScheduledWorkflow={
  agent:{id:string;version:number;config:{name:string;purpose:string;schedule:ScheduleSpec}};
  schedule:{active:boolean;spec:ScheduleSpec;next_run_at:string;agent_version?:number}|null;
};
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
  if(Number.isNaN(value.valueOf())||!schedule.spec.timezone)return '실행 시각 확인 필요';
  try{return new Intl.DateTimeFormat('ko-KR',{timeZone:schedule.spec.timezone,month:'long',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(value);}
  catch{return '실행 시각 확인 필요';}
}

export function ScheduledWorkflows({onOpen,onCreate}:{onOpen:(id:string)=>void;onCreate:()=>void}) {
  const [workflows,setWorkflows]=useState<ScheduledWorkflow[]>([]),[loading,setLoading]=useState(true),[error,setError]=useState('');
  const [query,setQuery]=useState(''),[filter,setFilter]=useState<'all'|Status>('all');
  const generation=useRef(0);
  async function load() {
    const token=++generation.current;setLoading(true);setError('');
    try {
      const result=await request<{workflows:ScheduledWorkflow[]}>('/api/agent-builder/schedules');
      if(!Array.isArray(result?.workflows))throw new Error('예약 목록 응답을 확인하지 못했습니다. 다시 시도해주세요.');
      if(token===generation.current)setWorkflows(result.workflows);
    } catch(error) {if(token===generation.current)setError((error as Error).message);}
    finally {if(token===generation.current)setLoading(false);}
  }
  useEffect(()=>{void load();return()=>{generation.current++;};},[]);
  const counts={active:0,paused:0,unconfigured:0};
  for(const row of workflows)counts[statusOf(row)]++;
  const search=query.trim().toLocaleLowerCase();
  const matches=workflows.filter(row=>(filter==='all'||statusOf(row)===filter)&&(row.agent.config.name+' '+row.agent.config.purpose).toLocaleLowerCase().includes(search));
  const rank:Record<Status,number>={active:0,paused:1,unconfigured:2};
  matches.sort((a,b)=>rank[statusOf(a)]-rank[statusOf(b)]||
    (statusOf(a)==='active'?(Date.parse(a.schedule!.next_run_at)||0)-(Date.parse(b.schedule!.next_run_at)||0):0)||a.agent.config.name.localeCompare(b.agent.config.name,'ko'));

  return <section className="scheduled-workflows" aria-labelledby="schedules-heading">
    <div className="entry-page-heading"><div><h1 id="schedules-heading">예약 실행</h1><p>정해진 시간에 실행할 워크플로우와 다음 실행 일정을 확인하세요.</p></div><button type="button" className="as-button" aria-label="예약 목록 새로고침" disabled={loading} onClick={()=>void load()}><RefreshCw size={15}/></button></div>
    {loading?<p className="entry-loading" role="status">예약 워크플로우를 불러오고 있습니다.</p>:error?<div className="as-error" role="alert"><AlertCircle size={16}/><span>{error}</span><button type="button" onClick={()=>void load()}>다시 시도</button></div>:<>
      <div className="schedule-overview" aria-label="예약 현황"><span>전체 <strong>{workflows.length}</strong></span><span>활성 <strong>{counts.active}</strong></span><span>중지 <strong>{counts.paused}</strong></span>{counts.unconfigured>0&&<span>설정 확인 필요 <strong>{counts.unconfigured}</strong></span>}</div>
      {workflows.length>0?<>
        <div className="entry-list-tools schedule-list-tools"><label><Search size={17} aria-hidden="true"/><input aria-label="예약 워크플로우 검색" placeholder="이름이나 업무 내용 검색" value={query} onChange={event=>setQuery(event.target.value)}/></label><select aria-label="예약 상태 필터" value={filter} onChange={event=>setFilter(event.target.value as 'all'|Status)}><option value="all">전체 상태</option><option value="active">활성</option><option value="paused">중지</option><option value="unconfigured">설정 확인 필요</option></select></div>
        {matches.length>0?<div className="schedule-table-container"><table className="schedule-table"><caption className="sr-only">예약 워크플로우 목록</caption><thead><tr><th scope="col">워크플로우</th><th scope="col">반복 일정</th><th scope="col">상태</th><th scope="col">다음 실행</th><th scope="col"><span className="sr-only">관리</span></th></tr></thead><tbody>{matches.map(row=>{
          const {agent,schedule}=row,status=statusOf(row),spec=status==='unconfigured'?agent.config.schedule:schedule!.spec;
          return <tr key={agent.id} data-schedule-id={agent.id}><td data-label="워크플로우"><h2>{agent.config.name}</h2><p>{agent.config.purpose}</p></td><td data-label="반복 일정"><span>{scheduleDescription(spec)}</span><small>{spec.timezone||'시간대 확인 필요'}</small>{scheduleDateNote(spec)&&<small>{scheduleDateNote(spec)}</small>}</td><td data-label="상태"><span className={'schedule-status '+status}>{statusLabels[status]}</span></td><td data-label="다음 실행"><span>{nextExecution(row)}</span></td><td><button type="button" className="as-button" aria-label={agent.config.name+' 예약 관리'} onClick={()=>onOpen(agent.id)}>예약 관리<ArrowRight size={14} aria-hidden="true"/></button></td></tr>;
        })}</tbody></table></div>:<div className="entry-empty"><Search size={24}/><h2>검색 결과가 없습니다.</h2><p>이름이나 상태 필터를 바꿔 확인하세요.</p><button type="button" className="as-button" onClick={()=>{setQuery('');setFilter('all');}}>검색 조건 초기화</button></div>}
      </>:<div className="entry-empty"><CalendarClock size={28}/><h2>예약된 워크플로우가 없습니다.</h2><p>새 워크플로우에서 반복 주기와 실행 시간을 설명하고 저장하세요.</p><button type="button" className="as-button" onClick={onCreate}>예약 워크플로우 만들기</button></div>}
    </>}
  </section>;
}
