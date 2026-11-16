import {makeFlow,type Flow} from '../../shared/workflow';

const starterFlow=makeFlow();

/** Preserve every user run and every modified copy of the old starter flow. */
export function visibleExternalFlows(flows:Flow[],executedWorkflowIds?:string[]):Flow[] {
 if(!Array.isArray(executedWorkflowIds))return flows;
 const executed=new Set(executedWorkflowIds);
 const {id:_starterId,...starter}=starterFlow;
 return flows.filter(flow=>{
  if(executed.has(flow.id))return true;
  const {id:_id,updatedAt:_updatedAt,...value}=flow;
  return JSON.stringify(value)!==JSON.stringify(starter);
 });
}
