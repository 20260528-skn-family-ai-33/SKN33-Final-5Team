import {createRequire} from 'node:module';
import {Worker} from 'node:worker_threads';
import type {Json} from '../lib/workflow';
import type {CodeContext} from './contracts';
import {BINARY_KEY,assertBinaryItems,splitBinaryItem} from './binary';
import {attachItemPairs} from './provenance';

const quickjsModule=createRequire(import.meta.url).resolve('quickjs-emscripten');
// Self contained host worker also works after esbuild bundles the server. User
// code is only evaluated inside QuickJS; no native callback is installed there.
const workerSource=`
 const {parentPort,workerData}=require('node:worker_threads');
 (async()=>{
  const {getQuickJS}=require(workerData.modulePath);
  const quickjs=await getQuickJS(),runtime=quickjs.newRuntime();
  runtime.setMemoryLimit(32*1024*1024);runtime.setMaxStackSize(512*1024);
  const deadline=Date.now()+2000;runtime.setInterruptHandler(()=>Date.now()>deadline);
  const context=runtime.newContext();
  let output;
  try{
   const result=context.evalCode(workerData.script,'workflow-code.js');
   if(result.error){const error=context.dump(result.error);result.error.dispose();if(Date.now()>deadline)throw new Error('맞춤 계산이 2초를 넘어서 중단했어요. 반복문을 확인해주세요.');throw new Error(typeof error?.message==='string'?error.message:'코드를 실행할 수 없어요. 메모리 또는 문법을 확인해주세요.');}
   output=context.dump(result.value);result.value.dispose();
  }finally{context.dispose();runtime.dispose();}
  parentPort.postMessage({value:output});parentPort.close();
 })().catch(error=>{parentPort.postMessage({error:error.message||'코드를 실행할 수 없어요.'});parentPort.close();});
`;

/** QuickJS receives JSON only. No host filesystem, process, network or native functions. */
export async function runCode(source:string,input:Json[],mode='runOnceForAllItems',context:CodeContext={}):Promise<Json[]> {
 if(typeof source!=='string'||source.length>200_000)throw new Error('코드는 200,000자 이내로 작성해주세요.');
 if(!['runOnceForAllItems','runOnceForEachItem'].includes(mode))throw new Error('맞춤 계산의 실행 방식을 확인해주세요.');
 assertBinaryItems(input);
 const envelope=(item:Json)=>{const {json,binary}=splitBinaryItem(item);return {json,...(Object.keys(binary).length?{binary}:{})};};
 const serialized=JSON.stringify({items:input.map(envelope),execution:context.execution??{id:'',mode:'test'},runIndex:context.runIndex??0,params:context.params??{},inputRefs:context.inputRefs??[],nodeRuns:(context.nodeRuns??[]).map(run=>({...run,output:run.output.map(port=>port.map(envelope))}))});
 if(Buffer.byteLength(serialized,'utf8')>2_000_000)throw new Error('맞춤 계산에 전달할 데이터가 너무 커요. 먼저 필요한 항목만 남겨주세요.');
 const script=`(() => {
   const state=JSON.parse(${JSON.stringify(serialized).replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029')});
   const items=state.items;
   const inputIdentity=new WeakMap(),plainJson=new WeakSet();
   items.forEach((item,index)=>{inputIdentity.set(item,index);inputIdentity.set(item.json,index);plainJson.add(item.json);item.pairedItem={item:index};});
   state.nodeRuns.forEach(run=>run.output.forEach(port=>port.forEach(item=>plainJson.add(item.json))));
   const $execution=state.execution,$runIndex=state.runIndex,$parameter=state.params;
   let currentIndex=0;
   const previous=(name,outputIndex,runIndex=-1)=>{
    if(typeof name!=='string'||outputIndex!==undefined&&(!Number.isSafeInteger(outputIndex)||outputIndex<0)||!Number.isSafeInteger(runIndex)||runIndex<-1)throw new Error('이전 단계의 이름과 출력·실행 번호를 확인해주세요.');
    const runs=state.nodeRuns.filter(run=>run.name===name&&!run.linkOnly);
    const run=runIndex===-1?runs[runs.length-1]:runs.find(run=>run.runIndex===runIndex);
    if(!run)throw new Error('이전 단계가 아직 실행되지 않았거나 이 회차에서 접근할 수 없어요: '+name);
    outputIndex??=run.defaultOutputIndex??0;
    if(outputIndex>=run.output.length)throw new Error('이전 단계의 출력 번호를 확인해주세요: '+name);
    return run.output[outputIndex];
   };
   const runsByKey=new Map(state.nodeRuns.filter(run=>typeof run.key==='string').map(run=>[run.key,run]));
   const linked=(name,index)=>{
    if(typeof name!=='string'||!Number.isSafeInteger(index)||index<0||index>=items.length)throw new Error('연결된 항목을 찾을 입력 번호를 확인해주세요.');
    const pending=(state.inputRefs[index]||[]).map(ref=>({ref,depth:0})),visited=new Set(),matches=new Map();
    let edges=pending.length;
    if(edges>200000)throw new Error('항목 연결이 너무 복잡해요. 중간 데이터를 먼저 정리해주세요.');
    while(pending.length){
     const {ref,depth}=pending.pop();
     if(!ref||typeof ref.run!=='string'||!ref.run||ref.run.length>4000||!Number.isSafeInteger(ref.item)||ref.item<0||ref.item>1000000||!Number.isSafeInteger(ref.output)||ref.output<0||ref.output>1000000||(ref.side!==undefined&&ref.side!=='input'))throw new Error('연결된 항목의 원본 정보가 올바르지 않아요. 다시 시험 실행해주세요.');
     const refKey=JSON.stringify([ref.run,ref.side||'output',ref.output,ref.item]);
     if(visited.has(refKey))continue;visited.add(refKey);
     if(visited.size>50000||depth>20000)throw new Error('항목 연결이 너무 복잡해요. 중간 데이터를 먼저 정리해주세요.');
     const add=refs=>{if(!Array.isArray(refs))throw new Error('연결된 항목의 원본 정보가 없어요.');if((edges+=refs.length)>200000)throw new Error('항목 연결이 너무 복잡해요. 중간 데이터를 먼저 정리해주세요.');for(const parent of refs)pending.push({ref:parent,depth:depth+1});};
     const run=runsByKey.get(ref.run);
     if(!run)throw new Error('연결된 항목의 실행 기록이 없어요. 다시 시험 실행해주세요.');
     if(ref.side==='input'){
      if(!Array.isArray(run.inputRefs)||ref.item>=run.inputRefs.length)throw new Error('연결된 항목의 입력 정보가 없어요. 다시 시험 실행해주세요.');
      add(run.inputRefs[ref.item]);continue;
     }
     const output=run.output[ref.output];
     if(!Array.isArray(output)||ref.item>=output.length)throw new Error('연결된 항목의 출력 정보가 없어요. 다시 시험 실행해주세요.');
     if(run.name===name){matches.set(refKey,output[ref.item]);continue;}
     const direct=run.outputSourceRefs?.[ref.output]?.[ref.item];
     if(direct!==undefined){add(direct);continue;}
     const pairs=run.outputPairedItems?.[ref.output]?.[ref.item];
     if(!Array.isArray(pairs))throw new Error('연결된 항목의 원본 정보가 없어요. 맞춤 계산에서는 pairedItem을 지정해주세요.');
     for(const pair of pairs){
      const port=pair.input??0,counts=run.inputPortCounts??[run.inputRefs?.length??0];
      if(!Array.isArray(counts)||counts.some(count=>!Number.isSafeInteger(count)||count<0)||counts.reduce((sum,count)=>sum+count,0)!==run.inputRefs?.length||!Number.isSafeInteger(port)||port<0||port>=counts.length||!Number.isSafeInteger(pair.item)||pair.item<0||pair.item>=counts[port])throw new Error('연결된 항목의 원본 번호가 올바르지 않아요.');
      const flat=counts.slice(0,port).reduce((sum,count)=>sum+count,0)+pair.item;
      if(!Array.isArray(run.inputRefs?.[flat]))throw new Error('연결된 항목의 입력 기록이 없어요.');
      add(run.inputRefs[flat]);
     }
    }
    if(matches.size>1)throw new Error('여러 원본 항목이 연결되어 하나를 고를 수 없어요. first() 또는 all()로 선택해주세요.');
    if(!matches.size)throw new Error('연결된 항목을 정확히 찾을 수 없어요. 맞춤 계산의 pairedItem이나 first() 또는 all()을 확인해주세요.');
    return matches.values().next().value;
   };
   const $=name=>({
    all:(outputIndex,runIndex)=>previous(name,outputIndex,runIndex),
    first:(outputIndex,runIndex)=>previous(name,outputIndex,runIndex)[0],
    last:(outputIndex,runIndex)=>previous(name,outputIndex,runIndex).at(-1),
    get isExecuted(){return state.nodeRuns.some(run=>run.name===name&&!run.linkOnly);},
    get item(){return linked(name,currentIndex);},
    itemMatching:index=>linked(name,index)
   });
   const $node=new Proxy(Object.create(null),{get:(_,name)=>{
    if(typeof name!=='string'||['__proto__','prototype','constructor'].includes(name))throw new Error('이전 단계 이름을 확인해주세요.');
    return {get json(){const item=previous(name)[currentIndex];if(!item)throw new Error('이전 단계에 같은 위치의 항목이 없어요. first() 또는 all()로 가져와주세요.');return item.json;},get binary(){const item=previous(name)[currentIndex];if(!item)throw new Error('이전 단계에 같은 위치의 항목이 없어요.');return item.binary||{};}};
   }});
   const $input={all:()=>items,first:()=>items[0],last:()=>items[items.length-1],item:items[0]};
   const $items=(name,outputIndex,runIndex)=>name===undefined?items:previous(name,outputIndex,runIndex);
   ${mode==='runOnceForEachItem'?`const result=items.map((item,$itemIndex)=>{currentIndex=$itemIndex;const $json=item.json,$binary=item.binary||{};const $input={all:()=>items,first:()=>items[0],last:()=>items.at(-1),item};return (function(){${source}\n})();});`:`const $json=items[0]?.json||{},$binary=items[0]?.binary||{};const $itemIndex=0;const value=(function(){${source}\n})();const result=value===null?[]:value;`}
   if(result&&typeof result.then==='function')throw new Error('비동기 코드는 지원하지 않아요. 네트워크 요청은 API 단계로 연결해주세요.');
   if(!Array.isArray(result))throw new Error('코드는 데이터 배열을 반환해야 해요.');
   if(result.length>5000)throw new Error('한 번에 5,000개 항목까지 반환할 수 있어요.');
   if(result.some(item=>item&&typeof item.then==='function'))throw new Error('비동기 코드는 지원하지 않아요. 네트워크 요청은 API 단계로 연결해주세요.');
   const pairings=[];let pairCount=0;
   const normalizePairs=value=>{
    const list=Array.isArray(value)?value:[value];
    if(list.length>5000||pairCount+list.length>20000)throw new Error('맞춤 계산의 항목 연결이 너무 많아요. 데이터를 나눠서 처리해주세요.');
    const pairs=list.map(pair=>{
     if(typeof pair==='number')pair={item:pair};
     if(!pair||typeof pair!=='object'||Array.isArray(pair)||!Number.isSafeInteger(pair.item)||pair.item<0||pair.item>=items.length||(pair.input!==undefined&&pair.input!==0)||Object.keys(pair).some(key=>!['item','input'].includes(key)))throw new Error('pairedItem은 실제 입력 항목 번호로 지정해주세요. 맞춤 계산에는 입력 0만 있어요.');
     return {item:pair.item,...(pair.input===undefined?{}:{input:0})};
    });pairCount+=pairs.length;return pairs;
   };
   const output=[];
   result.forEach((item,resultIndex)=>{
    ${mode==='runOnceForEachItem'?`if(item===null)return;`:''}
    const isEnvelope=item&&item.json!==undefined&&!plainJson.has(item);
    const json=isEnvelope?item.json:item;
    if(!json||typeof json!=='object'||Array.isArray(json))throw new Error('각 항목은 이름과 값이 있는 객체여야 해요.');
    if(Object.hasOwn(json,${JSON.stringify(BINARY_KEY)}))throw new Error('파일은 json 안이 아니라 binary 항목에 넣어주세요.');
    const known=inputIdentity.get(item)??inputIdentity.get(json);
    const pairing=${mode==='runOnceForEachItem'?'normalizePairs(resultIndex)':`isEnvelope&&Object.hasOwn(item,'pairedItem')?normalizePairs(item.pairedItem):known!==undefined?normalizePairs(known):items.length===1||result.length===1&&items.length>1?normalizePairs(0):result.length===items.length?normalizePairs(resultIndex):[]`};
    pairings.push(pairing);
    if(isEnvelope&&item.binary!==undefined){if(!item.binary||typeof item.binary!=='object'||Array.isArray(item.binary))throw new Error('파일 정보는 binary 객체로 반환해주세요.');output.push({...json,[${JSON.stringify(BINARY_KEY)}]:item.binary});}
    else output.push(json);
   });
   const text=JSON.stringify({output,pairings});
   if(text.length>2000000)throw new Error('코드 실행 결과는 2MB 이내여야 해요.');
   return text;
  })()`;
 return new Promise((resolve,reject)=>{
  const worker=new Worker(workerSource,{eval:true,workerData:{modulePath:quickjsModule,script},env:{},execArgv:[],resourceLimits:{maxOldGenerationSizeMb:64}});
  let settled=false;
  const finish=(error?:Error,value?:string)=>{if(settled)return;settled=true;clearTimeout(timer);void worker.terminate().catch(()=>{});if(error)reject(error);else {try{if(typeof value!=='string'||Buffer.byteLength(value,'utf8')>2_000_000)throw new Error('코드 실행 결과는 2MB 이내여야 해요.');const {output,pairings}=JSON.parse(value);assertBinaryItems(output);resolve(attachItemPairs(output,pairings));}catch(error){reject(error instanceof Error?error:new Error('코드 결과를 읽을 수 없어요. JSON 객체를 반환해주세요.'));}}};
  const timer=setTimeout(()=>finish(new Error('맞춤 계산이 2초를 넘어서 중단했어요. 반복문을 확인해주세요.')),2000);
  worker.on('message',message=>finish(message.error?new Error(message.error):undefined,message.value));
  worker.on('error',()=>finish(new Error('코드를 실행할 수 없어요. 메모리 또는 문법을 확인해주세요.')));
  worker.on('exit',()=>{if(!settled)finish(new Error('코드 실행이 중단됐어요. 메모리 또는 문법을 확인해주세요.'));});
 });
}
