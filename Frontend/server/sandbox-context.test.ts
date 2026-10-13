import test from 'node:test';
import assert from 'node:assert/strict';
import {runCode} from './sandbox';
import {BINARY_KEY,createBinaryFile} from './binary';
import {getItemPairs} from './provenance';
import type {CodeContext} from './contracts';

test('Code preserves canonical files separately from JSON and exposes the current file',async()=>{
 const file=createBinaryFile(Buffer.from('hello'),{fileName:'hello.txt',mimeType:'text/plain'});
 const input=[{name:'A',[BINARY_KEY]:{data:file}}];
 assert.deepEqual(await runCode('return $input.all();',input),input);
 const [output]=await runCode('return {json:{name:$json.name,file:$binary.data.fileName,hidden:$json.__easyflowBinary===undefined},binary:$input.item.binary};',input,'runOnceForEachItem');
 assert.deepEqual(output,{name:'A',file:'hello.txt',hidden:true,[BINARY_KEY]:{data:file}});
 await assert.rejects(runCode('return [{json:{},binary:{data:{data:"!!!!",fileSize:3,mimeType:"text/plain",fileName:"bad.txt"}}}];',[{}]),/base64/);
});

test('Code previous-node selectors use explicit completed ports and run indexes',async()=>{
 const context={execution:{id:'code-test',mode:'production'},runIndex:2,params:{limit:3},nodeRuns:[
  {name:'고객 목록',runIndex:0,output:[[{name:'A'},{name:'B'}],[{name:'C'}]]},
  {name:'고객 목록',runIndex:1,output:[[{name:'Z'}]]},
 ]};
 assert.deepEqual(await runCode(`return [{json:{first:$('고객 목록').first().json.name,last:$('고객 목록').last(0,0).json.name,branch:$('고객 목록').first(1,0).json.name,count:$items('고객 목록',0,0).length,executed:$('고객 목록').isExecuted,missing:$('다음').isExecuted,id:$execution.id,mode:$execution.mode,run:$runIndex,limit:$parameter.limit}}];`,[{}],'runOnceForAllItems',context),[{first:'Z',last:'B',branch:'C',count:2,executed:true,missing:false,id:'code-test',mode:'production',run:2,limit:3}]);
 assert.deepEqual(await runCode(`return {json:{name:$node['고객 목록'].json.name,index:$itemIndex,last:$input.last().json.v}};`,[{v:1},{v:2}],'runOnceForEachItem',{nodeRuns:context.nodeRuns.slice(0,1)}),[{name:'A',index:0,last:2},{name:'B',index:1,last:2}]);
 await assert.rejects(runCode(`return [{json:$('고객 목록').item.json}];`,[{}],'runOnceForAllItems',context),/연결된 항목/);
 await assert.rejects(runCode(`return [{json:$('고객 목록').first(0,10).json}];`,[{}],'runOnceForAllItems',context),/접근/);
 await assert.rejects(runCode(`return [{json:$('다음').first().json}];`,[{}],'runOnceForAllItems',context),/아직 실행/);
});

test('Code rejects unknown modes and prevents prior-history data from reaching native host capabilities',async()=>{
 await assert.rejects(runCode('return [];',[{}],'unknown'),/실행 방식/);
 const [output]=await runCode(`return [{json:{process:typeof process,require:typeof require,fetch:typeof fetch,buffer:typeof Buffer,constructor:$('이전').first().json.constructor===Object}}];`,[{}],'runOnceForAllItems',{nodeRuns:[{name:'이전',runIndex:0,output:[[{value:1}]]}]});
 assert.deepEqual(output,{process:'undefined',require:'undefined',fetch:'undefined',buffer:'undefined',constructor:true});
});
test('Code input and output bounds count UTF-8 bytes for Korean text',async()=>{
 await assert.rejects(runCode('return $input.all();',[{text:'가'.repeat(700000)}]),/전달할 데이터/);
 await assert.rejects(runCode('return [{json:{text:"가".repeat(700000)}}];',[{}]),/2MB/);
});

test('Code retains exact input identity after filtering and reversing identical records',async()=>{
 const result=await runCode('return $input.all().filter((item,index)=>index!==1).reverse();',[{value:1},{value:1},{value:1}]);
 assert.deepEqual(result,[{value:1},{value:1}]);
 assert.deepEqual(getItemPairs(result),[[{item:2}],[{item:0}]]);
 assert.equal(JSON.stringify(result),'[{"value":1},{"value":1}]');
 const originalJson=await runCode('return [$input.last().json,$input.first().json];',[{id:'A'},{id:'B'}]);
 assert.deepEqual(getItemPairs(originalJson),[[{item:1}],[{item:0}]]);
 const ordinary={json:{value:'real'},id:'A',pairedItem:'ordinary',binary:'ordinary column'};
 const preserved=await runCode('return [$input.first().json];',[ordinary]);
 assert.deepEqual(preserved,[ordinary]);assert.deepEqual(getItemPairs(preserved),[[{item:0}]]);
 const unusual=JSON.parse('{"__proto__":{"x":1},"id":"A"}');
 const [roundTrip]=await runCode('return $input.all();',[unusual]);
 assert.deepEqual(roundTrip,unusual);assert.equal(Object.hasOwn(roundTrip,'__proto__'),true);
});

test('Code supports numeric, object and multiple explicit parents without leaking pairing into JSON',async()=>{
 const result=await runCode('return [{json:{name:"B"},pairedItem:1},{json:{name:"A"},pairedItem:{item:0,input:0}},{json:{pairedItem:"ordinary user value"},pairedItem:[{item:0},{item:1}]}];',[{name:'A'},{name:'B'}]);
 assert.deepEqual(result,[{name:'B'},{name:'A'},{pairedItem:'ordinary user value'}]);
 assert.deepEqual(getItemPairs(result),[[{item:1}],[{item:0,input:0}],[{item:0},{item:1}]]);
 for(const source of ['-1','2','0.5','null','true','{item:0,input:1}','{item:0,sourceOverwrite:{}}'])await assert.rejects(runCode('return [{json:{},pairedItem:'+source+'}];',[{name:'A'},{name:'B'}]),/pairedItem/);
});

test('Code automatically pairs fresh matching counts and a single result like n8n',async()=>{
 const matching=await runCode('return [{json:{id:"B"}},{json:{id:"A"}}];',[{id:'A'},{id:'B'}]);
 assert.deepEqual(getItemPairs(matching),[[{item:0}],[{item:1}]]);
 const aggregated=await runCode('return [{json:{count:$input.all().length}}];',[{id:'A'},{id:'B'}]);
 assert.deepEqual(getItemPairs(aggregated),[[{item:0}]]);
 const expanded=await runCode('return [{json:{a:1}},{json:{a:2}},{json:{a:3}}];',[{id:'A'}]);
 assert.deepEqual(getItemPairs(expanded),[[{item:0}],[{item:0}],[{item:0}]]);
 const unknown=await runCode('return [{json:{a:1}},{json:{a:2}},{json:{a:3}}];',[{id:'A'},{id:'B'}]);
 assert.deepEqual(getItemPairs(unknown),[[],[],[]]);
 const deliberate=await runCode('return [{json:{count:2},pairedItem:[{item:0},{item:1}]}];',[{id:'A'},{id:'B'}]);
 assert.deepEqual(getItemPairs(deliberate),[[{item:0},{item:1}]]);
});

test('Code foreach always records its current input and null keeps exact indices',async()=>{
 const perItem=await runCode('if($itemIndex===1)return null;return {json:{id:$json.id}};',[{id:'A'},{id:'B'},{id:'C'}],'runOnceForEachItem');
 assert.deepEqual(perItem,[{id:'A'},{id:'C'}]);
 assert.deepEqual(getItemPairs(perItem),[[{item:0}],[{item:2}]]);
 const explicit=await runCode('return {json:{id:$json.id},pairedItem:0};',[{id:'A'},{id:'B'}],'runOnceForEachItem');
 assert.deepEqual(getItemPairs(explicit),[[{item:0}],[{item:1}]]);
 assert.deepEqual(await runCode('return null;',[{id:'A'}]),[]);
});

const linkedContext=():CodeContext=>({inputRefs:[[{run:'sorted',output:0,item:0}],[{run:'sorted',output:0,item:1}]],nodeRuns:[
 {key:'original',name:'원본 고객',runIndex:0,linkOnly:true,output:[[{id:'A'},{id:'B'},{id:'C'}]],inputRefs:[],outputPairedItems:[[[],[],[]]]},
 {key:'sorted',name:'정렬',runIndex:0,output:[[{score:95},{score:90}]],inputRefs:[[{run:'original',output:0,item:0}],[{run:'original',output:0,item:1}],[{run:'original',output:0,item:2}]],outputPairedItems:[[[{item:2}],[{item:0}]]]},
 {key:'visible',name:'원본 고객',runIndex:1,output:[[{id:'Z'}]]},
]});
test('Code modern item selectors trace causal records independently of first/all and each index',async()=>{
 const context=linkedContext();
 assert.deepEqual(await runCode('return $input.all().map((item,index)=>({json:{original:$("원본 고객").itemMatching(index).json.id,current:$("원본 고객").item.json.id,first:$("원본 고객").first().json.id},pairedItem:index}));',[{score:95},{score:90}],'runOnceForAllItems',context),[{original:'C',current:'C',first:'Z'},{original:'A',current:'C',first:'Z'}]);
 assert.deepEqual(await runCode('return {json:{id:$("원본 고객").item.json.id}};',[{score:95},{score:90}],'runOnceForEachItem',context),[{id:'C'},{id:'A'}]);
 await assert.rejects(runCode('return [{json:$("원본 고객").itemMatching(2).json}];',[{score:95},{score:90}],'runOnceForAllItems',context),/입력 번호/);
});

test('Code linkage rejects missing or ambiguous sources and deduplicates one original',async()=>{
 const context=linkedContext();context.inputRefs![0]=[{run:'original',output:0,item:0},{run:'original',output:0,item:2}];
 await assert.rejects(runCode('return [{json:$("원본 고객").item.json}];',[{}],'runOnceForAllItems',context),/여러 원본/);
 context.inputRefs![0]=[{run:'original',output:0,item:0},{run:'original',output:0,item:0}];
 assert.deepEqual(await runCode('return [{json:$("원본 고객").item.json}];',[{}],'runOnceForAllItems',context),[{id:'A'}]);
 context.inputRefs![0]=[{run:'missing',output:0,item:0}];
 await assert.rejects(runCode('return [{json:$("원본 고객").item.json}];',[{}],'runOnceForAllItems',context),/실행 기록/);
});

test('Code linkage follows exact Merge input ports and rejects port-local range errors',async()=>{
 const context:CodeContext={inputRefs:[[{run:'merge',output:0,item:0}]],nodeRuns:[
  {key:'left',name:'왼쪽',runIndex:0,output:[[{id:'A'},{id:'B'}]]},
  {key:'right',name:'오른쪽',runIndex:0,output:[[{id:'R'}]]},
  {key:'merge',name:'합치기',runIndex:0,output:[[{}]],inputPortCounts:[2,1],inputRefs:[[{run:'left',output:0,item:0}],[{run:'left',output:0,item:1}],[{run:'right',output:0,item:0}]],outputPairedItems:[[[{item:0,input:1}]]]},
 ]};
 assert.deepEqual(await runCode('return [{json:$("오른쪽").item.json}];',[{}],'runOnceForAllItems',context),[{id:'R'}]);
 context.nodeRuns![2].outputPairedItems=[[[{item:1,input:1}]]];
 await assert.rejects(runCode('return [{json:$("오른쪽").item.json}];',[{}],'runOnceForAllItems',context),/원본 번호/);
});

test('Code loop origins follow historical input sides and explicit source overrides without positional fallback',async()=>{
 const context:CodeContext={inputRefs:[[{run:'loop-final',output:0,item:0}]],nodeRuns:[
  {key:'original',name:'원본',runIndex:0,output:[[{id:'A'},{id:'B'}]]},
  {key:'loop-first',name:'반복',runIndex:0,output:[[],[{}]],inputRefs:[[{run:'original',output:0,item:0}],[{run:'original',output:0,item:1}]]},
  {key:'loop-final',name:'반복',runIndex:1,output:[[{}],[]],inputRefs:[[{run:'original',output:0,item:0}]],outputPairedItems:[[[{item:0}]],[]],outputSourceRefs:[[[{run:'loop-first',side:'input',output:0,item:1}]],[]]},
 ]};
 assert.deepEqual(await runCode('return [{json:$("원본").item.json}];',[{}],'runOnceForAllItems',context),[{id:'B'}]);
 context.nodeRuns![2].outputSourceRefs=[[[]],[]];
 await assert.rejects(runCode('return [{json:$("원본").item.json}];',[{}],'runOnceForAllItems',context),/정확히/);
});
