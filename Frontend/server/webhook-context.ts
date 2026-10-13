import type {Execution,FlowNode,Json,NodeRun} from '../lib/workflow';
import {resolveValue} from './engine';
import {lookupItemSource,type ItemSourceRef,type TraceRecord} from './provenance';
import type {ExpressionContext} from './expressions';

const successful=(record:NodeRun)=>record.status==='success'||record.continued===true;
const key=(record:NodeRun):string=>record.executionKey||record.nodeId;
const scope=(record:NodeRun):string=>typeof record.executionScope==='string'?record.executionScope:typeof record.executionKey==='string'&&record.executionKey.endsWith('/'+record.nodeId)?record.executionKey.slice(0,-record.nodeId.length-1):'root';
const lastRecord=(execution?:Execution)=>execution?.runs.filter(record=>record.status!=='skipped').at(-1);
const trace=(record:NodeRun):TraceRecord=>({key:key(record),name:record.name,output:record.output,inputRefs:record.inputRefs,outputPairedItems:record.outputPairedItems,outputSourceRefs:record.outputSourceRefs,inputPortCounts:record.inputPortCounts});

/** Native Webhook replies read only output zero of the last executed step. */
export function webhookResponseItems(execution:Execution):Json[] {return lastRecord(execution)?.output[0]??[];}

/** Only recorded data and public execution metadata reach response expressions. */
export function webhookResponseResolver(node:FlowNode,executionId:string,input:Json[],execution?:Execution):(value:any,item:Json)=>any {
 const current=lastRecord(execution),items=execution?webhookResponseItems(execution):input,currentScope=current?scope(current):'root',currentIndex=current&&execution?execution.runs.indexOf(current):-1;
 const completed=(execution?.runs.slice(0,currentIndex+1)??[]).filter(successful),records=new Map(completed.map(record=>[key(record),trace(record)])),visible=completed.filter(record=>{const origin=scope(record);return origin===currentScope||currentScope.startsWith(origin+'/');});
 const refs:ItemSourceRef[][]=current?items.map((_,item)=>[{run:key(current),output:0,item}]):items.map(()=>[]);
 const context:ExpressionContext={input:items,inputByPort:[items],params:node.parameters,runIndex:current?.runIndex??0,execution:{id:executionId,mode:'production'},
  linkedItem:(name,index=0)=>lookupItemSource(name,index,refs,record=>records.get(record)).item,
  node:(name,outputIndex=0,requestedRun)=>{
   if(!Number.isSafeInteger(outputIndex)||outputIndex<0)throw new Error('참조할 이전 단계의 출력 경로 번호를 확인해주세요.');
   if(requestedRun!==undefined&&(!Number.isSafeInteger(requestedRun)||requestedRun< -1))throw new Error('참조할 이전 단계의 처리 회차 번호를 확인해주세요.');
   const available=visible.filter(record=>record.name===name),ordered=available.map((record,index)=>({record,index})).sort((a,b)=>scope(b.record).length-scope(a.record).length||b.index-a.index);
   const selected=requestedRun===undefined||requestedRun===-1?ordered[0]?.record:ordered.find(value=>(value.record.runIndex??0)===requestedRun)?.record;
   if(!selected)return {items:[],isExecuted:false};if(outputIndex>=selected.output.length)throw new Error('참조할 이전 단계의 출력 경로 번호를 확인해주세요.');return {items:selected.output[outputIndex],isExecuted:true};
  }
 };
 return (value,item)=>resolveValue(value,item,{...context,itemIndex:Math.max(0,items.indexOf(item))});
}
