import {clone,type Execution,type Flow,type Json} from '../lib/workflow';

export type ErrorDispatchResult={dispatched:boolean;executionId?:string;notificationError?:string};
export type ErrorEnqueue=(flow:Flow,input:Json[],mode:'live',extra:Json)=>Execution|null|Promise<Execution|null>;
const triggers=new Set(['n8n-nodes-base.manualTrigger','n8n-nodes-base.scheduleTrigger','n8n-nodes-base.webhook','n8n-nodes-base.executeWorkflowTrigger','n8n-nodes-base.errorTrigger']);

/** Error notification is a separate execution and never changes the failed result. */
export async function dispatchErrorWorkflow(execution:Execution,parentFlow:Flow,getFlow:(id:string)=>Flow|undefined,enqueue:ErrorEnqueue):Promise<ErrorDispatchResult> {
 if(execution.status!=='error'||execution.mode!=='live'||!['schedule','webhook'].includes(execution.trigger)||execution.errorWorkflowOf||execution.trigger==='error')return {dispatched:false};
 const configured=parentFlow.settings?.errorWorkflow,id=typeof configured==='string'?configured:configured?.value;
 if(!id)return {dispatched:false};
 if(typeof id!=='string'||id===parentFlow.id)return {dispatched:false,notificationError:'오류 처리 자동화는 원래 자동화와 다른 저장된 자동화를 선택해주세요.'};
 let target:Flow|undefined;try{target=getFlow(id);}catch{return {dispatched:false,notificationError:'오류 처리 자동화를 찾을 수 없어요. 선택한 자동화 ID를 확인해주세요.'};}
 if(!target||target.id!==id)return {dispatched:false,notificationError:'오류 처리 자동화를 찾을 수 없어요. 선택한 자동화 ID를 확인해주세요.'};
 const starts=target.nodes.filter(node=>triggers.has(node.type)&&!node.disabled);
 if(starts.length!==1||starts[0].type!=='n8n-nodes-base.errorTrigger')return {dispatched:false,notificationError:'오류 처리 자동화에는 켜져 있는 오류 시작 단계 하나가 필요해요.'};
 const error=execution.error as any,message=typeof error==='string'?error:typeof error?.message==='string'?error.message:'자동화 실행에 실패했어요.';
 const details:Json={id:execution.id,mode:execution.mode,error:{message},lastNodeExecuted:execution.lastNodeExecuted||execution.runs.at(-1)?.name};
 if(typeof execution.url==='string')details.url=execution.url;
 if(typeof execution.retryOf==='string')details.retryOf=execution.retryOf;
 if(typeof error?.stack==='string')details.error.stack=error.stack;
 const input=[{execution:details,workflow:{id:parentFlow.id,name:parentFlow.name}}];
 try{const queued=await enqueue(clone(target),input,'live',{trigger:'error',parentExecutionId:execution.id,errorWorkflowOf:parentFlow.id});if(!queued)return {dispatched:false,notificationError:'오류 처리 자동화를 시작하지 못했어요. 자동화 설정을 확인해주세요.'};return {dispatched:true,executionId:queued.id};}
 catch{return {dispatched:false,notificationError:'오류 처리 자동화를 시작하지 못했어요. 연결 정보와 자동화 설정을 확인해주세요.'};}
}
