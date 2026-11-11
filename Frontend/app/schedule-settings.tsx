import {useEffect,useId,useRef,useState,type FormEvent} from 'react';
import {CalendarClock} from 'lucide-react';
import {request} from '../components/flow/connection-panel';
import {scheduleDateNote,scheduleDescription,type ScheduleSpec} from '../lib/schedule-spec';
import './schedule-settings.css';

export type ManagedSchedule={active:boolean;spec:ScheduleSpec;next_run_at:string;agent_version?:number};
const defaultSpec:ScheduleSpec={frequency:'daily',timezone:'Asia/Seoul',hour:9,minute:0,weekday:null,monthday:null,month_end:false};
const weekdays=['월요일','화요일','수요일','목요일','금요일','토요일','일요일'];
const discardMessage='저장하지 않은 예약 일정 변경이 있습니다. 변경 내용을 버릴까요?';
function effectiveSpec(spec:ScheduleSpec):ScheduleSpec {
 return {frequency:spec.frequency,timezone:'Asia/Seoul',hour:spec.frequency==='hourly'?null:spec.hour??9,minute:spec.minute,weekday:spec.frequency==='weekly'?spec.weekday??0:null,monthday:spec.frequency==='monthly'&&!spec.month_end?spec.monthday??1:null,month_end:spec.frequency==='monthly'&&!!spec.month_end};
}

export function ScheduleSettings({workflowId,workflowVersion,schedule,onChange,disabled=false,activationDisabled=false,onBusyChange,onDirtyChange}:{
 workflowId:string;
 workflowVersion?:number;
 schedule:ManagedSchedule|null;
 onChange:(schedule:ManagedSchedule|null)=>void|Promise<void>;
 disabled?:boolean;
 activationDisabled?:boolean;
 onBusyChange?:(busy:boolean)=>void;
 onDirtyChange?:(dirty:boolean)=>void;
}) {
 const formId=useId(),inFlight=useRef(false);
 const [spec,setSpec]=useState<ScheduleSpec>(()=>({...defaultSpec,...schedule?.spec}));
 const [baseline,setBaseline]=useState<ScheduleSpec>(()=>({...defaultSpec,...schedule?.spec}));
 const [editing,setEditing]=useState(!schedule),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const dirtyCallback=useRef(onDirtyChange);dirtyCallback.current=onDirtyChange;
 const savedSpec=JSON.stringify(schedule?effectiveSpec({...defaultSpec,...schedule.spec}):null);
 useEffect(()=>{const saved={...defaultSpec,...schedule?.spec};setSpec(saved);setBaseline(saved);setEditing(!schedule);},[workflowId,savedSpec]);
 useEffect(()=>{setError('');setNotice('');},[workflowId]);
 const dirty=editing&&JSON.stringify(effectiveSpec(spec))!==JSON.stringify(effectiveSpec(baseline));
 useEffect(()=>{dirtyCallback.current?.(dirty);},[dirty]);
 useEffect(()=>()=>{dirtyCallback.current?.(false);},[]);
 const locked=disabled||busy;
 const stale=workflowVersion!==undefined&&schedule?.agent_version!==undefined&&workflowVersion!==schedule.agent_version;
 function change(patch:Partial<ScheduleSpec>){setSpec(prior=>({...prior,...patch}));setError('');setNotice('');}
 function closeEditor(){
  if(locked||dirty&&!window.confirm(discardMessage))return;
  setSpec(baseline);setEditing(false);setError('');
 }
 async function act(action:'configure'|'toggle'|'remove') {
  if(inFlight.current||disabled)return;
  if(action==='remove'&&dirty&&!window.confirm(discardMessage))return;
  inFlight.current=true;setBusy(true);onBusyChange?.(true);setError('');setNotice('');
  try {
   const configuredSpec=effectiveSpec(spec);
   const result=await request<{schedule:ManagedSchedule|null}>('/api/agent-builder/'+(action==='configure'?'schedule_configure':action==='remove'?'schedule_remove':'schedule'),
    action==='configure'?{id:workflowId,spec:configuredSpec}:{id:workflowId,...(action==='toggle'?{active:!schedule?.active}:{})});
   if(action!=='toggle'){
    const saved={...defaultSpec,...result.schedule?.spec};setSpec(saved);setBaseline(saved);setEditing(!result.schedule);dirtyCallback.current?.(false);
   }
   await onChange(result.schedule);
   setNotice(action==='remove'?'예약을 해제했습니다. 워크플로우는 계속 직접 실행할 수 있습니다.':action==='configure'?'일정을 저장했습니다. 자료와 연결 상태를 확인한 뒤 예약을 켜세요.':result.schedule?.active?'예약을 켰습니다.':'예약을 중지했습니다.');
  }catch(err){setError((err as Error).message);}
  finally{inFlight.current=false;setBusy(false);onBusyChange?.(false);}
 }
 function save(event:FormEvent){event.preventDefault();void act('configure');}
 let nextRun='';
 if(schedule?.active&&!stale)try{nextRun=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'long',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(schedule.next_run_at));}catch{nextRun='실행 시각 확인 필요';}
 return <section className="schedule-settings" aria-label="예약 설정" aria-busy={busy}>
  <div className="schedule-settings-heading"><strong><CalendarClock size={17}/>예약 실행</strong><span className={'schedule-status '+(stale?'unconfigured':schedule?.active?'active':'paused')}>{stale?'설정 확인 필요':schedule?schedule.active?'활성':'중지':'예약 없음'}</span></div>
  <p className="schedule-settings-help">예약을 추가하거나 해제해도 업무 흐름은 유지되며, 필요할 때 직접 실행할 수 있습니다.</p>
  {schedule&&<>
   <p>{scheduleDescription(schedule.spec)} · 서울 표준시</p>
   {scheduleDateNote(schedule.spec)&&<small>{scheduleDateNote(schedule.spec)}</small>}
   {nextRun&&<small>다음 실행: {nextRun}</small>}
   <div className="schedule-settings-actions"><button type="button" className="as-button as-primary" disabled={locked||!schedule.active&&(activationDisabled||stale)} onClick={()=>void act('toggle')}>{schedule.active?'예약 중지':'예약 켜기'}</button><button type="button" className="as-button" disabled={locked} onClick={()=>{if(editing)closeEditor();else{setSpec(baseline);setEditing(true);setError('');}}}>일정 {editing?'수정 닫기':'수정'}</button><button type="button" className="as-button schedule-remove" disabled={locked} onClick={()=>void act('remove')}>예약 해제</button></div>
   {stale&&<p className="schedule-settings-help">워크플로우가 수정되었습니다. 일정을 수정·저장하여 현재 버전 적용을 확인한 뒤 예약을 켜세요.</p>}
   {!schedule.active&&activationDisabled&&<p className="schedule-settings-help">자동 실행에 필요한 자료·요청 설정과 서비스 연결을 준비하면 예약을 켤 수 있습니다.</p>}
  </>}
  {editing&&<form className="schedule-settings-form" onSubmit={save}>
   <div className="schedule-settings-fields">
    <label htmlFor={formId+'-frequency'}>반복 주기<select id={formId+'-frequency'} value={spec.frequency} disabled={locked} onChange={event=>change({frequency:event.target.value as ScheduleSpec['frequency']})}><option value="hourly">매시간</option><option value="daily">매일</option><option value="weekly">매주</option><option value="monthly">매월</option></select></label>
    {spec.frequency==='weekly'&&<label htmlFor={formId+'-weekday'}>실행 요일<select id={formId+'-weekday'} value={spec.weekday??0} disabled={locked} onChange={event=>change({weekday:Number(event.target.value)})}>{weekdays.map((day,index)=><option key={day} value={index}>{day}</option>)}</select></label>}
    {spec.frequency==='monthly'&&<label htmlFor={formId+'-monthday'}>실행일<select id={formId+'-monthday'} value={spec.month_end?'last':spec.monthday??1} disabled={locked} onChange={event=>change({month_end:event.target.value==='last',monthday:event.target.value==='last'?null:Number(event.target.value)})}>{Array.from({length:31},(_,i)=><option key={i} value={i+1}>{i+1}일</option>)}<option value="last">말일</option></select></label>}
    {spec.frequency!=='hourly'&&<label htmlFor={formId+'-time'}>실행 시간<input id={formId+'-time'} type="time" required disabled={locked} value={String(spec.hour??9).padStart(2,'0')+':'+String(spec.minute??0).padStart(2,'0')} onChange={event=>{const [hour,minute]=event.target.value.split(':').map(Number);change({hour,minute});}}/></label>}
    {spec.frequency==='hourly'&&<label htmlFor={formId+'-minute'}>매시간 실행할 분<input id={formId+'-minute'} type="number" min={0} max={59} step={1} required disabled={locked} value={spec.minute??0} onChange={event=>change({minute:event.target.value===''?null:Number(event.target.value)})}/></label>}
    <p className="schedule-settings-help">시간대: 서울 표준시 (고정)</p>
   </div>
   <p className="schedule-settings-help">예약은 중지 상태로 저장됩니다. 자동 실행에 사용할 자료와 서비스 연결을 먼저 준비하세요.</p>
   {scheduleDateNote(spec)&&<small>{scheduleDateNote(spec)}</small>}
   <div className="schedule-settings-actions"><button type="submit" className="as-button as-primary" disabled={locked}>{busy?'저장 중…':stale?'일정 저장 · 현재 버전 적용':schedule?'일정 저장':'예약 추가'}</button>{schedule&&<button type="button" className="as-button" disabled={locked} onClick={closeEditor}>취소</button>}</div>
  </form>}
  {error&&<p className="schedule-settings-error" role="alert">{error}</p>}
  {notice&&<p className="schedule-settings-notice" role="status">{notice}</p>}
 </section>;
}
