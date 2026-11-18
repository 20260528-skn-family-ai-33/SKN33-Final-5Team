import {clone,type Flow} from '../../../shared/workflow';
import {validateSchedule} from './schedule';

/** Modify only the schedule trigger; preserve its ID/name, edges and business nodes. */
export function updateWorkflowSchedule(flow:Flow,action:unknown,rule?:unknown):Flow {
 const next=clone(flow);
 const scheduled=next.nodes.filter(node=>!node.disabled&&node.type.endsWith('.scheduleTrigger'));
 const triggers=scheduled.length||action!=='configure'?scheduled:next.nodes.filter(node=>!node.disabled&&node.type.endsWith('.manualTrigger'));
 if(triggers.length!==1)throw new Error('예약 시작 단계 하나를 선택한 워크플로우에서 일정을 관리하세요.');
 const node=triggers[0];
 if(action==='remove'){
  node.type='n8n-nodes-base.manualTrigger';node.typeVersion=1;node.parameters={};
 }else if(action==='configure'){
  if(!rule||typeof rule!=='object'||Array.isArray(rule))throw new Error('예약 일정을 확인해주세요.');
  node.type='n8n-nodes-base.scheduleTrigger';node.typeVersion=1.2;
  node.parameters={...node.parameters,rule:clone(rule)};
  validateSchedule(node,next.settings.timezone||'Asia/Seoul');
 }else throw new Error('예약 변경 요청을 확인해주세요.');
 next.active=false;delete next.schedule;
 return next;
}
