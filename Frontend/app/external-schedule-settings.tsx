import {scheduleCronExpression,scheduleRuleForField} from '../../shared/schedule-rule';
import {useEffect,useRef,useState} from 'react';
import {CalendarClock} from 'lucide-react';
import type {Flow} from '../lib/workflow';
import {request} from '../components/flow/connection-panel';
import {scheduleRemovalMessage} from './schedule-settings';
import './schedule-settings.css';

const defaultScheduleRule={field:'days',triggerAtHour:9,triggerAtMinute:0};
function isRuleObject(value:unknown):value is Record<string,unknown>{
 return !!value&&typeof value==='object'&&!Array.isArray(value);
}
function editableSchedule(value:unknown){
 const rule=isRuleObject(value)?value:{};
 const intervals=Array.isArray(rule.interval)?rule.interval:[];
 const usable=intervals.length>0&&intervals.length<=20;
 return {
  rule:{...rule,interval:usable?intervals.map(entry=>isRuleObject(entry)?entry:defaultScheduleRule):[defaultScheduleRule]},
  needsRepair:!usable||intervals.some(entry=>!isRuleObject(entry)),
 };
}

export function ExternalScheduleSettings({flow,onChange,onDirtyChange,onBusyChange,disabled=false}:{flow:Flow;onChange:(flow:Flow)=>void;onDirtyChange?:(dirty:boolean)=>void;onBusyChange?:(busy:boolean)=>void;disabled?:boolean}) {
 const triggers=flow.nodes.filter(node=>!node.disabled&&node.type.endsWith('.scheduleTrigger'));
 const targets=triggers.length?triggers:flow.nodes.filter(node=>!node.disabled&&node.type.endsWith('.manualTrigger'));
 const hasSchedule=triggers.length>0;
 const trigger=targets[0];
 const normalized=editableSchedule(trigger?.parameters.rule);
 const needsRepair=hasSchedule&&normalized.needsRepair;
 const saved=JSON.stringify(normalized.rule);
 const [rule,setRule]=useState<any>(()=>JSON.parse(saved)),[editing,setEditing]=useState(!hasSchedule),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const lock=useRef(false),dirty=editing&&JSON.stringify(rule)!==saved;
 const dirtyCallback=useRef(onDirtyChange);dirtyCallback.current=onDirtyChange;
 useEffect(()=>{setRule(JSON.parse(saved));setEditing(!hasSchedule);setError('');},[flow.id,saved,hasSchedule]);
 useEffect(()=>{dirtyCallback.current?.(dirty);return()=>dirtyCallback.current?.(false);},[dirty]);
 const intervals=Array.isArray(rule.interval)?rule.interval:[];
 function replaceRule(index:number,value:Record<string,unknown>){setRule({...rule,interval:intervals.map((entry:any,i:number)=>i===index?value:entry)});}
 function patch(index:number,value:Record<string,unknown>){replaceRule(index,{...intervals[index],...value});}
 async function act(action:'toggle'|'configure'|'remove'){
  if(disabled||lock.current)return;
  if(action==='remove'&&!window.confirm(scheduleRemovalMessage))return;
  if(action==='remove'&&dirty&&!window.confirm('저장하지 않은 일정 변경을 버리고 예약을 삭제할까요?'))return;
  lock.current=true;setBusy(true);onBusyChange?.(true);setError('');
  try{
   const result=await request<{workflow:Flow}>(action==='toggle'?'/api/activation':'/api/workflows/schedule',action==='toggle'?{workflowId:flow.id,active:!flow.active,expectedUpdatedAt:flow.updatedAt}:{workflowId:flow.id,updatedAt:flow.updatedAt,action,...(action==='configure'?{rule}:{})});
   onChange(result.workflow);if(action!=='toggle')setEditing(action==='remove');
  }catch(err){setError((err as Error).message);}finally{lock.current=false;setBusy(false);onBusyChange?.(false);}
 }
 if(targets.length!==1)return <section className="schedule-settings"><p>예약 시작 단계 하나가 있는 워크플로우에서 일정을 관리할 수 있습니다.</p></section>;
 return <section className="schedule-settings" aria-label="예약 설정" aria-busy={busy}>
  <div className="schedule-settings-heading"><strong><CalendarClock size={17}/>예약 실행</strong><span className={'schedule-status '+(flow.active?'active':'paused')}>{!triggers.length?'예약 없음':flow.active?'활성':'중지'}</span></div>
  <p className="schedule-settings-help">{!flow.settings.timezone||flow.settings.timezone==='Asia/Seoul'?'서울 표준시':flow.settings.timezone}</p>
  {triggers.length>0&&<div className="schedule-settings-actions"><button className="as-button as-primary" disabled={disabled||busy||!flow.active&&(dirty||needsRepair)} onClick={()=>void act('toggle')}>{flow.active?'예약 중지':'예약 켜기'}</button><button className="as-button as-secondary" disabled={disabled||busy} onClick={()=>{if(editing&&dirty&&!window.confirm('저장하지 않은 일정 변경을 버릴까요?'))return;setRule(JSON.parse(saved));setEditing(!editing);}}>일정 {editing?'수정 닫기':'수정'}</button><button className="as-button as-danger schedule-remove" disabled={disabled||busy} onClick={()=>void act('remove')}>예약 삭제</button></div>}
  {needsRepair&&<p className="schedule-settings-help" role="status">{editing?'읽을 수 없는 항목에 매일 오전 9시를 표시했습니다. 실행 일정을 확인한 뒤 저장하세요.':'예약 설정을 읽을 수 없습니다. 일정 수정에서 값을 확인하고 저장하세요.'}</p>}
  {editing?<form className="schedule-settings-form" onSubmit={event=>{event.preventDefault();void act('configure');}}>
   {intervals.map((entry:any,index:number)=><fieldset className="schedule-settings-fields" key={index} disabled={disabled||busy}><legend>반복 일정 {intervals.length>1?index+1:''}</legend>
    <label>반복 주기<select aria-label={'반복 주기 '+(index+1)} value={entry.field||'days'} onChange={event=>replaceRule(index,scheduleRuleForField(event.target.value,entry,trigger?.parameters))}>{[['seconds','초 간격'],['minutes','분 간격'],['hours','시간 간격'],['days','매일'],['weeks','매주'],['months','매월'],['cronExpression','직접 지정']].map(([value,name])=><option key={value} value={value}>{name}</option>)}</select></label>
    {['seconds','minutes','hours','days','weeks','months'].includes(entry.field)&&<label>반복 간격<input aria-label={'반복 간격 '+(index+1)} type="number" min="1" step="1" required value={entry[entry.field+'Interval']??(entry.field==='seconds'?30:entry.field==='minutes'?5:1)} onChange={event=>patch(index,{[entry.field+'Interval']:Number(event.target.value)})}/></label>}
    {['days','weeks','months'].includes(entry.field||'days')&&<label>실행 시간<input aria-label={'실행 시간 '+(index+1)} type="time" required value={String(entry.triggerAtHour??0).padStart(2,'0')+':'+String(entry.triggerAtMinute??0).padStart(2,'0')} onChange={event=>{const [triggerAtHour,triggerAtMinute]=event.target.value.split(':').map(Number);patch(index,{triggerAtHour,triggerAtMinute});}}/></label>}
    {entry.field==='weeks'&&<fieldset className="schedule-settings-weekdays"><legend>실행 요일</legend>{['일','월','화','수','목','금','토'].map((day,i)=><label key={day}><input type="checkbox" checked={(Array.isArray(entry.triggerAtDay)?entry.triggerAtDay:entry.triggerAtDay===undefined?[0]:[]).map(Number).includes(i)} onChange={event=>{const days:number[]=(Array.isArray(entry.triggerAtDay)?entry.triggerAtDay:entry.triggerAtDay===undefined?[0]:[]).map(Number);patch(index,{triggerAtDay:event.target.checked?[...days,i]:days.filter(day=>day!==i)});}}/>{day}요일</label>)}</fieldset>}
    {entry.field==='months'&&<label>실행일<input aria-label={'실행일 '+(index+1)} type="number" min="1" max="31" required value={entry.triggerAtDayOfMonth??1} onChange={event=>patch(index,{triggerAtDayOfMonth:Number(event.target.value)})}/></label>}
    {entry.field==='cronExpression'&&<label>실행 일정 (Cron)<input aria-label={'실행 일정 '+(index+1)} required value={String(scheduleCronExpression(entry,trigger?.parameters??{})??'')} onChange={event=>patch(index,{customExpression:event.target.value})}/></label>}
   </fieldset>)}{flow.active&&<p className="schedule-settings-help">일정을 저장하면 예약이 중지됩니다.</p>}<div className="schedule-settings-actions schedule-settings-submit"><button className="as-button as-primary" type="submit" disabled={disabled||busy}>일정 저장</button></div>
  </form>:null}
  {error&&<p role="alert" className="schedule-settings-error">{error}</p>}
 </section>;
}
