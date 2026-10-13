import type {Execution,FlowNode,NodeRun} from './workflow';
import type {ValueSource} from './value-paths';
import {lookupItemSource,sourceParents,refKey,MAX_TRACE_REFS,MAX_TRACE_EDGES,type TraceRecord,type ItemSourceRef} from './item-provenance';

const runScope=(record:NodeRun)=>typeof record.executionScope==='string'?record.executionScope:typeof record.executionKey==='string'&&record.executionKey.endsWith('/'+record.nodeId)?record.executionKey.slice(0,-record.nodeId.length-1):'root';
const runKey=(record:NodeRun):string=>record.executionKey||record.nodeId;
const traceRecord=(record:NodeRun):TraceRecord=>({key:runKey(record),name:record.name,output:record.output,inputRefs:record.inputRefs,outputPairedItems:record.outputPairedItems,outputSourceRefs:record.outputSourceRefs,inputPortCounts:record.inputPortCounts});
const missingReason='원본 연결 기록이 없어 각 항목의 원본을 확인할 수 없어요. 첫 항목이나 전체 목록을 선택해주세요.';
const traceReason=(error:unknown)=>error instanceof Error&&error.message.includes('여러 원본')?'여러 원본 항목이 합쳐져 하나를 고를 수 없어요. 첫 항목이나 전체 목록을 선택해주세요.':error instanceof Error&&error.message.includes('한도')?'원본 연결을 확인할 수 있는 범위를 넘었어요. 첫 항목이나 전체 목록을 선택해주세요.':missingReason;
type LinkResult={status:'ready';sources:{ref:ItemSourceRef;item:ValueSource['items'][number]}[]}|{status:'unavailable';reason:string};
const MAX_EDITOR_TRACE_WORK=200000;

/** Read completed visible results and prove linked selections against exact historical edges. */
export function editorValueSources(node:FlowNode,execution?:Execution|null,currentRun?:NodeRun):ValueSource[] {
 if(!execution)return [];
 const currentIndex=currentRun?execution.runs.indexOf(currentRun):-1;
 const scope=currentRun?runScope(currentRun):'root';
 const records=execution.runs.slice(0,currentIndex<0?undefined:currentIndex).filter(record=>record.status==='success'||record.continued===true);
 const inScope=(record:NodeRun)=>{const origin=runScope(record);return origin===scope||scope.startsWith(origin+'/');};
 const visible=records.filter(record=>record.nodeId!==node.id&&inScope(record));
 const nearest=new Map<string,NodeRun>();
 for(const record of visible){const previous=nearest.get(record.nodeId);if(!previous||runScope(record).length>=runScope(previous).length)nearest.set(record.nodeId,record);}
 // A detached or old record without input refs cannot establish which results preceded it.
 const trace=new Map<string,NodeRun>(currentIndex>=0?records.map(record=>[runKey(record),record]):[]);
 let traceWork=0;
 const spend=(amount=1)=>{traceWork+=amount;if(traceWork>MAX_EDITOR_TRACE_WORK)throw new Error('원본 연결 확인 한도를 넘었어요.');};
 const traceCache=new Map<string,{record:TraceRecord;parentWidth:number}>();
 const lookup=(key:string)=>{
  spend();const source=trace.get(key);if(!source)return undefined;
  let cached=traceCache.get(key);
  if(!cached){
   const record=traceRecord(source);let inputWidth=0,parentWidth=0;
   // Count metadata scans once and charge a conservative parent fan-out on every
   // lookup. Repeated duplicate edges therefore share this screen's total budget.
   for(const group of Array.isArray(record.inputRefs)?record.inputRefs:[]){spend(1+(Array.isArray(group)?group.length:0));if(Array.isArray(group))inputWidth=Math.max(inputWidth,group.length);}
   parentWidth=inputWidth;
   for(const port of Array.isArray(record.outputSourceRefs)?record.outputSourceRefs:[]){spend();if(Array.isArray(port))for(const group of port){spend(1+(Array.isArray(group)?group.length:0));if(Array.isArray(group))parentWidth=Math.max(parentWidth,group.length);}}
   for(const port of Array.isArray(record.outputPairedItems)?record.outputPairedItems:[]){spend();if(Array.isArray(port))for(const group of port){spend(1+(Array.isArray(group)?group.length:0));if(Array.isArray(group))parentWidth=Math.max(parentWidth,group.length*inputWidth);}}
   cached={record,parentWidth};traceCache.set(key,cached);
  }
  spend(cached.parentWidth);return cached.record;
 };
 const links=new Map<string,LinkResult>();
 const linked=(name:string):LinkResult=>{
  const known=links.get(name);if(known)return known;
  let result:LinkResult;
  if(!currentRun?.input.length)result={status:'unavailable',reason:'현재 입력 항목이 없어 각 항목의 원본을 확인할 수 없어요.'};
  else if(currentIndex<0||!Array.isArray(currentRun.inputRefs)||currentRun.inputRefs.length!==currentRun.input.length)result={status:'unavailable',reason:missingReason};
  else if(currentRun.input.length>5000)result={status:'unavailable',reason:'입력 항목이 너무 많아 원본 연결을 확인할 수 없어요. 첫 항목이나 전체 목록을 선택해주세요.'};
  else {try {result={status:'ready',sources:currentRun.input.map((_,index)=>lookupItemSource(name,index,currentRun.inputRefs,lookup))};}catch(error){result={status:'unavailable',reason:traceReason(error)};}}
  links.set(name,result);return result;
 };
 const selections=new Map<string,{record:NodeRun;outputIndex:number;explicitSelection:boolean}>();
 const mixedSources=new Map<string,ValueSource>();
 const add=(record:NodeRun,outputIndex:number,explicitSelection:boolean)=>{
  const key=JSON.stringify([runKey(record),outputIndex]);
  if(!selections.has(key)&&selections.size+mixedSources.size<500)selections.set(key,{record,outputIndex,explicitSelection});
 };
 for(const record of nearest.values())for(let outputIndex=0;outputIndex<record.output.length;outputIndex++)add(record,outputIndex,true);
 // Only actual recorded ancestors may introduce older loop results. Siblings and future
 // activations never enter this queue merely because they have the same node name.
 if(currentIndex>=0&&Array.isArray(currentRun?.inputRefs)){
  const queue:ItemSourceRef[]=[],visited=new Set<string>(),causal=new Map<string,NodeRun>();
  try {
   for(const group of currentRun.inputRefs){spend(1+(Array.isArray(group)?group.length:0));if(Array.isArray(group)){if(queue.length+group.length>MAX_TRACE_EDGES)throw new Error('원본 연결 확인 한도를 넘었어요.');for(const ref of group)queue.push(ref);}}
   for(let cursor=0;cursor<queue.length&&cursor<MAX_TRACE_EDGES;cursor++){
   spend();
   const ref=queue[cursor];if(!ref||typeof ref.run!=='string')continue;
   const key=refKey(ref);if(visited.has(key))continue;visited.add(key);if(visited.size>MAX_TRACE_REFS)break;
   const record=trace.get(ref.run);if(!record)continue;
   if(record.nodeId!==node.id)causal.set(ref.run,record);
   const historical=lookup(ref.run);if(!historical)continue;
   let parents:ItemSourceRef[]=[];try{parents=sourceParents(ref,historical);}catch{/* Missing legacy ancestry remains an explicit selection, never an inferred link. */}
   spend(parents.length);
   for(const parent of parents){if(queue.length>=MAX_TRACE_EDGES)break;queue.push(parent);}
   }
  }catch{/* A shared budget failure disables unproven links while keeping first/all. */}
  for(const record of causal.values()){
   const result=linked(record.name);if(result.status!=='ready')continue;
   const source=result.sources[0]?.ref;
   const sameOrigin=source&&result.sources.every(value=>value.ref.run===source.run&&value.ref.output===source.output);
   if(source&&sameOrigin&&source.run===runKey(record)){
    // first/all have stricter scope rules than .item. Old records without runIndex
    // cannot explicitly select a different activation by name alone.
    add(record,source.output,inScope(record)&&(record.runIndex!==undefined||nearest.get(record.nodeId)===record));
   }else if(source&&!sameOrigin&&!mixedSources.has(record.name)&&selections.size+mixedSources.size<500){
    const firstRecord=trace.get(source.run);
    if(firstRecord)mixedSources.set(record.name,{kind:'node',nodeId:firstRecord.nodeId,nodeName:record.name,outputIndex:source.output,items:result.sources.map(value=>value.item),explicitSelection:false,linkedItem:{status:'ready',sample:result.sources[0].item,inputCount:result.sources.length,multipleOrigins:true}});
   }
  }
 }
 return [
  {kind:'input',outputIndex:0,items:currentRun?.input??[]},
  ...[...selections.values()].map(({record,outputIndex,explicitSelection}):ValueSource=>{
   const result=linked(record.name);
   const matches=result.status==='ready'&&result.sources.every(value=>value.ref.run===runKey(record)&&value.ref.output===outputIndex);
   return {kind:'node',nodeId:record.nodeId,nodeName:record.name,activationKey:runKey(record),outputIndex,runIndex:record.runIndex,items:record.output[outputIndex],explicitSelection,
    linkedItem:matches&&result.status==='ready'?{status:'ready',sample:result.sources[0].item,inputCount:result.sources.length}:{status:'unavailable',reason:result.status==='unavailable'?result.reason:'각 항목의 원본이 다른 처리 회차 또는 출력에 있어요. 이 결과는 첫 항목이나 전체 목록으로 선택해주세요.'}};
  }),
  ...mixedSources.values(),
 ];
}
