import type {Json} from './workflow';

export type ItemPair={item:number;input?:number};
export type ItemSourceRef={run:string;output:number;item:number;side?:'input'};
export type TraceRecord={key:string;name:string;output:Json[][];inputRefs?:ItemSourceRef[][];outputPairedItems?:ItemPair[][][];outputSourceRefs?:ItemSourceRef[][][];inputPortCounts?:number[]};
export const MAX_PAIR_EDGES=100000;
export const MAX_TRACE_REFS=50000;
export const MAX_TRACE_DEPTH=20000;
export const MAX_TRACE_EDGES=200000;
const metadata=Symbol('easyflow-item-pairs');
const validIndex=(value:unknown)=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=0&&value<=1000000;
const invalid=()=>new Error('항목 출처 정보가 올바르지 않아요. 입력 포트와 항목 번호를 확인해주세요.');
const missing=()=>new Error('이 항목의 이전 단계 출처를 확인할 수 없어요. .first(), .last(), .all()[번호]로 항목을 명시해주세요.');
export function attachItemPairs<T extends Json[]>(items:T,pairs:ItemPair[][]):T {
 if(!Array.isArray(items)||!Array.isArray(pairs)||items.length!==pairs.length)throw invalid();let edges=0;
 const copy=pairs.map(group=>{if(!Array.isArray(group)||((edges+=group.length)>MAX_PAIR_EDGES))throw invalid();return group.map(pair=>{if(!pair||!validIndex(pair.item)||pair.input!==undefined&&!validIndex(pair.input))throw invalid();return {item:pair.item,...(pair.input===undefined?{}:{input:pair.input})};});});
 Object.defineProperty(items,metadata,{value:copy,enumerable:false,configurable:true});return items;
}
export function getItemPairs(items:Json[]):ItemPair[][]|undefined {return (items as any)[metadata];}
export function refKey(ref:ItemSourceRef):string {return JSON.stringify([ref.run,ref.side||'output',ref.output,ref.item]);}
function validateRef(ref:ItemSourceRef):void {if(!ref||typeof ref.run!=='string'||!ref.run||ref.run.length>4000||!validIndex(ref.output)||!validIndex(ref.item)||ref.side!==undefined&&ref.side!=='input')throw invalid();}
export function pairInputIndex(pair:ItemPair,counts:number[]|undefined,total:number):number {
 if(!pair||!validIndex(pair.item)||pair.input!==undefined&&!validIndex(pair.input))throw invalid();const port=pair.input??0;
 if(!counts){if(port!==0||pair.item>=total)throw invalid();return pair.item;}
 if(!Array.isArray(counts)||counts.some(n=>!validIndex(n))||counts.reduce((sum,n)=>sum+n,0)!==total||port>=counts.length||pair.item>=counts[port])throw invalid();return counts.slice(0,port).reduce((sum,n)=>sum+n,0)+pair.item;
}
/** Read the parents of an exact historical output. Loop origins may refer to an older activation's input side. */
export function sourceParents(ref:ItemSourceRef,record:TraceRecord):ItemSourceRef[] {
 validateRef(ref);if(ref.run!==record.key)throw invalid();
 if(ref.side==='input'){const sources=record.inputRefs?.[ref.item];if(!Array.isArray(sources))throw missing();return sources;}
 if(!Array.isArray(record.output[ref.output])||ref.item>=record.output[ref.output].length)throw invalid();
 const explicit=record.outputSourceRefs?.[ref.output]?.[ref.item];if(explicit!==undefined){if(!Array.isArray(explicit))throw invalid();return explicit;}
 const pairs=record.outputPairedItems?.[ref.output]?.[ref.item];if(pairs===undefined)throw missing();if(!Array.isArray(pairs))throw invalid();
 const refs=record.inputRefs;if(!Array.isArray(refs))throw missing();return pairs.flatMap(pair=>{const index=pairInputIndex(pair,record.inputPortCounts,refs.length),sources=refs[index];if(!Array.isArray(sources))throw invalid();return sources;});
}
/** No index inference: follow only recorded edges and deduplicate the same original source. */
export function lookupItemSources(name:string,index:number,inputRefs:ItemSourceRef[][]|undefined,lookup:(run:string)=>TraceRecord|undefined):{ref:ItemSourceRef;item:Json}[] {
 if(typeof name!=='string'||!name||!validIndex(index)||!Array.isArray(inputRefs)||!Array.isArray(inputRefs[index]))throw missing();
 const queue=inputRefs[index].map(ref=>({ref,depth:0})),visited=new Set<string>(),matches=new Map<string,{ref:ItemSourceRef;item:Json}>();
 for(let cursor=0;cursor<queue.length;cursor++){
  const {ref,depth}=queue[cursor];validateRef(ref);const key=refKey(ref);if(visited.has(key))continue;visited.add(key);
  if(visited.size>MAX_TRACE_REFS||depth>MAX_TRACE_DEPTH)throw new Error('항목 출처 추적 한도를 넘었어요. .first() 또는 .all()[번호]로 필요한 결과를 직접 선택해주세요.');
  const record=lookup(ref.run);if(!record)throw missing();
  if(ref.side!=='input'&&record.name===name){if(!record.output[ref.output]?.[ref.item])throw invalid();matches.set(key,{ref,item:record.output[ref.output][ref.item]});continue;}
  for(const parent of sourceParents(ref,record)){if(queue.length>=MAX_TRACE_EDGES)throw new Error('항목 출처 추적 한도를 넘었어요. 필요한 결과를 직접 선택해주세요.');queue.push({ref:parent,depth:depth+1});}
 }
 if(!matches.size)throw missing();return [...matches.values()];
}

export function lookupItemSource(name:string,index:number,inputRefs:ItemSourceRef[][]|undefined,lookup:(run:string)=>TraceRecord|undefined):{ref:ItemSourceRef;item:Json} {
 const sources=lookupItemSources(name,index,inputRefs,lookup);if(sources.length!==1)throw new Error(`${name}: 여러 원본 항목이 합쳐져 하나를 고를 수 없어요. .first(), .last(), .all()[번호]로 항목을 명시해주세요.`);return sources[0];
}
