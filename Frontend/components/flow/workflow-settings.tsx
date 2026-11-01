"use client";

import {useEffect,useState,type FormEvent} from 'react';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription,DialogFooter} from '@/components/ui/dialog';
import {Select,SelectTrigger,SelectValue,SelectContent,SelectItem} from '@/components/ui/select';
import {clone,nodeInfo,type Flow,type Json} from '@/lib/workflow';

type WorkflowSettingsProps={
 open:boolean;
 onClose:()=>void;
 flow:Flow;
 workflows:Flow[];
 onApply:(settings:Json)=>void;
};

export function WorkflowSettings({open,onClose,flow,workflows,onApply}:WorkflowSettingsProps) {
 const [timezone,setTimezone]=useState('Asia/Seoul');
 const [errorWorkflow,setErrorWorkflow]=useState('none');
 const [timezoneError,setTimezoneError]=useState('');
 const [errorWorkflowError,setErrorWorkflowError]=useState('');

 useEffect(()=>{
  if(!open)return;
  setTimezone(typeof flow.settings?.timezone==='string'?flow.settings.timezone:'Asia/Seoul');
  setErrorWorkflow(typeof flow.settings?.errorWorkflow==='string'&&flow.settings.errorWorkflow?flow.settings.errorWorkflow:'none');
  setTimezoneError('');
  setErrorWorkflowError('');
 },[open,flow.id,flow.settings]);

 const handlers=workflows.filter(workflow=>{const starts=workflow.nodes.filter(node=>nodeInfo(node)?.category==='시작'&&!node.disabled);return workflow.id!==flow.id&&starts.length===1&&starts[0].type==='n8n-nodes-base.errorTrigger';});
 const handlerAvailable=errorWorkflow==='none'||handlers.some(workflow=>workflow.id===errorWorkflow);

 function apply(event:FormEvent<HTMLFormElement>) {
  event.preventDefault();
  const value=timezone.trim();
  let invalidTimezone='';
  try {
   if(!value)throw new Error();
   new Intl.DateTimeFormat('ko-KR',{timeZone:value}).format(new Date());
  } catch {
   invalidTimezone='올바른 시간대를 입력해주세요. 예: Asia/Seoul, UTC';
  }
  setTimezoneError(invalidTimezone);
  setErrorWorkflowError(handlerAvailable?'':'오류를 처리할 워크플로를 다시 선택하거나 사용하지 않음을 선택해주세요.');
  if(invalidTimezone||!handlerAvailable)return;
  const settings:Json={...clone(flow.settings||{}),timezone:value};
  if(errorWorkflow==='none')delete settings.errorWorkflow;
  else settings.errorWorkflow=errorWorkflow;
  onApply(settings);
  onClose();
 }

 return <Dialog open={open} onOpenChange={value=>{if(!value)onClose();}}>
  <DialogContent className="flow-dialog">
   <DialogHeader>
    <DialogTitle>워크플로 설정</DialogTitle>
    <DialogDescription>{flow.name}의 예약 시간대와 오류 처리 방법을 정해요.</DialogDescription>
   </DialogHeader>
   <form className="grid gap-4" onSubmit={apply} noValidate>
    <div className="field">
     <label htmlFor="workflow-timezone">예약에 사용할 시간대</label>
     <input id="workflow-timezone" value={timezone} onChange={event=>{setTimezone(event.target.value);setTimezoneError('');}} placeholder="Asia/Seoul" list="workflow-timezones" aria-invalid={!!timezoneError} aria-describedby={timezoneError?'workflow-timezone-error':'workflow-timezone-help'}/>
     <datalist id="workflow-timezones"><option value="Asia/Seoul"/><option value="UTC"/><option value="Asia/Tokyo"/><option value="America/New_York"/><option value="Europe/London"/></datalist>
     <small id="workflow-timezone-help">한국 시각으로 예약하려면 Asia/Seoul을 사용하세요.</small>
     {timezoneError&&<small id="workflow-timezone-error" className="error-text" role="alert">{timezoneError}</small>}
    </div>
    <div className="field">
     <label htmlFor="workflow-error-handler">자동 실행이 실패하면 이어서 실행할 워크플로</label>
     <Select value={errorWorkflow} onValueChange={value=>{setErrorWorkflow(value);setErrorWorkflowError('');}}>
      <SelectTrigger id="workflow-error-handler" aria-invalid={!!errorWorkflowError} aria-describedby={errorWorkflowError?'workflow-error-handler-error':'workflow-error-handler-help'}><SelectValue/></SelectTrigger>
      <SelectContent>
       <SelectItem value="none">사용하지 않음</SelectItem>
       {!handlerAvailable&&<SelectItem value={errorWorkflow} disabled>선택한 워크플로를 사용할 수 없어요</SelectItem>}
       {handlers.map(workflow=><SelectItem key={workflow.id} value={workflow.id}>{workflow.name}</SelectItem>)}
      </SelectContent>
     </Select>
     <small id="workflow-error-handler-help">‘자동화 오류로 시작’ 단계가 켜진 워크플로만 선택할 수 있어요.</small>
     {!handlers.length&&<small>오류를 처리할 워크플로를 먼저 만들고 저장해주세요.</small>}
     {errorWorkflowError&&<small id="workflow-error-handler-error" className="error-text" role="alert">{errorWorkflowError}</small>}
    </div>
    <div className="help-box">실제 예약 실행이나 Webhook 실행이 실패하면 선택한 워크플로를 자동으로 실행해요. 수동 실행과 시험 실행에서는 호출하지 않아요.</div>
    <DialogFooter>
     <button type="button" className="btn" onClick={onClose}>취소</button>
     <button type="submit" className="btn primary">설정 적용</button>
    </DialogFooter>
   </form>
  </DialogContent>
 </Dialog>;
}
