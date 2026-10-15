import test from 'node:test';
import assert from 'node:assert/strict';
import {clone,type Flow,type FlowNode,type Json,type Execution} from '../lib/workflow';
import {compileLoopPlan,loopNodeType,nextBatch} from './loops';
import {executeFlow} from './engine';
import type {RunOptions} from './contracts';

const node=(id:string,type='noOp',parameters:Json={}):FlowNode=>({id,name:id,type:`n8n-nodes-base.${type}`,typeVersion:type==='splitInBatches'?3:1,position:[0,0],parameters});
const loop=(id='loop',batchSize=1,options:Json={})=>node(id,'splitInBatches',{batchSize,options});
const start=()=>node('start','manualTrigger');
function graph(nodes:FlowNode[],edges:[string,string,number?,number?][]):Flow {
 const connections:Flow['connections']={};for(const [source,target,output=0,input=0] of edges){const channels=connections[source]??={main:[]};while(channels.main.length<=output)channels.main.push([]);channels.main[output].push({node:target,type:'main',index:input});}
 return {id:'loop-test',name:'반복 검증',nodes,connections,settings:{}};
}
const opts=(extra:Partial<RunOptions>={}):RunOptions=>({mode:'learning',credential:()=>{throw new Error('no credential');},...extra});
const fetchMock=(fn:(url:string,init:RequestInit)=>Response|Promise<Response>)=>((url:any,init:any)=>fn(String(url),init)) as typeof fetch;
const response=(body:any,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
const basic=(body=node('body'))=>graph([start(),loop(),body,node('end')],[['start','loop'],['loop','body',1],['body','loop'],['loop','end',0]]);

test('batch state keeps original remaining items and collects transformed feedback only into done output 0',()=>{
 const input=[{id:1},{id:2},{id:3}],before=clone(input);
 const first=nextBatch(undefined,input,2);assert.deepEqual(first.output,[[],[{id:1},{id:2}]]);assert.equal(first.context.currentRunIndex,0);assert.equal(first.context.maxRunIndex,2);
 const second=nextBatch(first.context,[{id:10},{id:20}],2);assert.deepEqual(second.output,[[],[{id:3}]]);assert.equal(second.context.noItemsLeft,true);assert.equal(second.context.done,false);
 const done=nextBatch(second.context,[{id:30}],2);assert.deepEqual(done.output,[[{id:10},{id:20},{id:30}],[]]);assert.equal(done.context.done,true);assert.equal(done.context.currentRunIndex,2);
 assert.deepEqual(input,before);assert.deepEqual(first.context.items,[{id:3}]);
});
test('reset starts from newly supplied input while no reset retains a finished node context',()=>{
 const first=nextBatch(undefined,[{id:1}],1),done=nextBatch(first.context,[{id:10}],1);
 assert.deepEqual(nextBatch(done.context,[{id:2}],1).output,[[{id:10},{id:2}],[]]);
 const reset=nextBatch(done.context,[{id:2},{id:3}],1,true);assert.deepEqual(reset.output,[[],[{id:2}]]);assert.equal(reset.context.currentRunIndex,0);assert.deepEqual(reset.context.processedItems,[]);
 for(const invalid of [0,-1,1.5,5001,NaN])assert.throws(()=>nextBatch(undefined,[{}],invalid),/묶음 크기/);
 assert.throws(()=>nextBatch({...done.context,processedItems:Array.from({length:5000},()=>({}))},[{}],1),/5,000/);
});
test('compile plans contain body nodes and distinguish loop feedback from root done routes',()=>{
 const flow=basic(),plan=compileLoopPlan(flow);
 assert.deepEqual(plan.root.ordered.map(n=>n.name),['start','loop','end']);
 assert.deepEqual(plan.loops.get('loop')?.body.ordered.map(n=>n.name),['body']);
 assert.equal(plan.loops.get('loop')?.feedback.source,'body');
 assert.equal(plan.feedbackKeys.size,1);
});
test('unsupported body cycles, cross-entry, missing feedback and shared bodies are rejected before requests',async()=>{
 let calls=0;const http=node('http','httpRequest',{url:'https://example.test/write',method:'POST'}),fetch=fetchMock(()=>{calls++;return response({});});
 const bad=[
  graph([start(),http,loop(),node('a'),node('b')],[['start','http'],['http','loop'],['loop','a',1],['a','b'],['b','a'],['b','loop']]),
  graph([start(),http,loop(),node('body')],[['start','http'],['http','loop'],['start','body'],['loop','body',1],['body','loop']]),
  graph([start(),http,loop(),node('body')],[['start','http'],['http','loop'],['loop','body',1]]),
  graph([start(),http,loop('one'),loop('two'),node('body')],[['start','http'],['http','one'],['http','two'],['one','body',1],['two','body',1],['body','one'],['body','two']]),
 ];
 for(const flow of bad){const result=await executeFlow(flow,[{}],opts({mode:'live',fetch}));assert.equal(result.status,'error');assert.match(result.error||'',/반복|순환/);assert.equal(calls,0);}
});
test('actual loop connections run each batch and emit all transformed feedback after final activation',async()=>{
 const body=node('body','code',{jsCode:'return $input.all().map(item=>({json:{value:item.json.value*2}}));'}),flow=basic(body);flow.nodes.find(n=>n.type===loopNodeType)!.parameters.batchSize=2;
 const result=await executeFlow(flow,[{value:1},{value:2},{value:3}],opts());
 assert.equal(result.status,'success');assert.deepEqual(result.output,[{value:2},{value:4},{value:6}]);
 assert.deepEqual(result.runs.filter(r=>r.nodeId==='body').map(r=>r.input.length),[2,1]);
 assert.deepEqual(result.runs.filter(r=>r.nodeId==='loop').map(r=>r.output.map(items=>items.length)),[[0,2],[0,1],[3,0]]);
 assert.equal(new Set(result.runs.map(r=>r.executionKey)).size,result.runs.length);
 assert.deepEqual(result.runs.filter(r=>r.nodeId==='body').map(r=>r.runIndex),[0,1]);
 assert.equal(result.loopContexts.loop.done,true);
});
test('a body that returns no feedback stops traversal instead of inventing next batches or done results',async()=>{
 const body=node('body','code',{jsCode:'return [];'}),result=await executeFlow(basic(body),[{value:1},{value:2}],opts());
 assert.equal(result.status,'success');assert.equal(result.runs.filter(r=>r.nodeId==='loop').length,1);assert.equal(result.runs.filter(r=>r.nodeId==='body').length,1);assert.equal(result.runs.at(-1)?.status,'skipped');assert.equal(result.loopContexts.loop.done,false);
});
test('loop Wait persists batch identity, and resumed external effects execute only once per actual item',async()=>{
 const posted:number[]=[],checkpoints:Execution[]=[];
 const flow=graph([start(),loop(),node('post','httpRequest',{url:'https://example.test/write',method:'POST',sendBody:true,specifyBody:'json',jsonBody:'={{ $json }}'}),node('wait','wait',{amount:1,unit:'seconds'}),node('end')],[['start','loop'],['loop','post',1],['post','wait'],['wait','loop'],['loop','end']]);
 const input=[{id:1},{id:2}],options=opts({mode:'live',fetch:fetchMock((_url,init)=>{const item=JSON.parse(String(init.body));posted.push(item.id);return response(item);}),onProgress:e=>{checkpoints.push(clone(e));}});
 const first=await executeFlow(flow,input,options);assert.equal(first.status,'waiting');assert.equal(first.pendingExecutionKey,'root/loop/batch0/wait');assert.deepEqual(posted,[1]);
 const early=await executeFlow(flow,input,{...options,resume:JSON.parse(JSON.stringify(first))});assert.equal(early.status,'waiting');assert.equal(early.resumeAt,first.resumeAt);assert.deepEqual(posted,[1]);
 const second=await executeFlow(flow,input,{...options,resume:{...early,resumeAt:new Date(0).toISOString()}});assert.equal(second.status,'waiting');assert.equal(second.pendingExecutionKey,'root/loop/batch1/wait');assert.deepEqual(posted,[1,2]);
 const done=await executeFlow(flow,input,{...options,resume:{...second,resumeAt:new Date(0).toISOString()}});assert.equal(done.status,'success');assert.deepEqual(done.output,input);assert.deepEqual(posted,[1,2]);assert.equal(done.runs.filter(r=>r.nodeId==='post').length,2);
 assert.ok(checkpoints.some(e=>e.status==='waiting'&&e.pendingExecutionKey==='root/loop/batch1/wait'));
});
test('two sequential loops operate on the preceding done output with separate state and all body records',async()=>{
 const flow=graph([start(),loop('first',2),node('double','code',{jsCode:'return $input.all().map(item=>({json:{value:item.json.value*2}}));'}),loop('second'),node('add','code',{jsCode:'return $input.all().map(item=>({json:{value:item.json.value+1}}));'}),node('end')],[['start','first'],['first','double',1],['double','first'],['first','second'],['second','add',1],['add','second'],['second','end']]);
 const result=await executeFlow(flow,[{value:1},{value:2},{value:3}],opts());assert.equal(result.status,'success');assert.deepEqual(result.output,[{value:3},{value:5},{value:7}]);assert.equal(result.runs.filter(r=>r.nodeId==='double').length,2);assert.equal(result.runs.filter(r=>r.nodeId==='add').length,3);
});
test('nested loops honor per-input reset expressions and route inner done feedback to the outer loop',async()=>{
 const flow=graph([start(),loop('outer'),node('expand','code',{jsCode:'return $input.all().flatMap(item=>item.json.values.map(value=>({json:{value,reset:true}})));'}),loop('inner',1,{reset:'={{ $json.reset }}'}),node('multiply','code',{jsCode:'return $input.all().map(item=>({json:{value:item.json.value*10,reset:false}}));'}),node('end')],[['start','outer'],['outer','expand',1],['expand','inner'],['inner','multiply',1],['multiply','inner'],['inner','outer'],['outer','end']]);
 const plan=compileLoopPlan(flow);assert.deepEqual(plan.loops.get('outer')?.body.ordered.map(n=>n.id),['expand','inner']);assert.deepEqual(plan.loops.get('inner')?.body.ordered.map(n=>n.id),['multiply']);
 const result=await executeFlow(flow,[{values:[1,2]},{values:[3,4]}],opts());assert.equal(result.status,'success',result.error);assert.deepEqual(result.output,[{value:10,reset:false},{value:20,reset:false},{value:30,reset:false},{value:40,reset:false}]);assert.equal(result.runs.filter(r=>r.nodeId==='multiply').length,4);assert.equal(result.runs.filter(r=>r.nodeId==='inner').length,6);
});
test('loop body retry and continued failures retain one record per item, and learning still blocks write APIs',async()=>{
 let calls=0;const body={...node('body','httpRequest',{url:'https://example.test/write',method:'POST'}),retryOnFail:true,maxTries:2,waitBetweenTries:0,onError:'continueRegularOutput'};
 const live=await executeFlow(basic(body),[{id:1},{id:2}],opts({mode:'live',fetch:fetchMock(()=>{calls++;return response({},503);})}));assert.equal(live.status,'success');assert.equal(calls,4);assert.equal(live.output.length,2);assert.ok(live.output.every((item:any)=>item.error));assert.ok(live.runs.filter(r=>r.nodeId==='body').every(r=>r.attempts===2&&r.continued));
 const learning=await executeFlow(basic({...body,onError:undefined}),[{id:1}],opts({fetch:fetchMock(()=>{calls++;return response({});})}));assert.equal(learning.status,'error');assert.match(learning.error||'',/연습 모드/);assert.equal(calls,4);
});
test('loop cancellation and step limits stop further effects and cannot be swallowed by continueOnFail',async()=>{
 const controller=new AbortController(),seen:number[]=[],body={...node('body','httpRequest',{url:'https://example.test/write',method:'POST',sendBody:true,specifyBody:'json',jsonBody:'={{ $json }}'}),continueOnFail:true};
 const canceled=await executeFlow(basic(body),[{id:1},{id:2},{id:3}],opts({mode:'live',signal:controller.signal,fetch:fetchMock((_url,init)=>{const input=JSON.parse(String(init.body));seen.push(input.id);if(seen.length===2)controller.abort();return response(input);})}));assert.equal(canceled.status,'canceled');assert.deepEqual(seen,[1,2]);
 const limited=await executeFlow(basic(node('body')),Array.from({length:10},(_,id)=>({id})),opts({maxSteps:5}));assert.equal(limited.status,'error');assert.match(limited.error||'',/제한/);assert.equal(limited.runs.length,5);
});
test('loop subflows have per-batch snapshots and resume nested child waits without repeating writes',async()=>{
 let writes=0;const child=graph([node('child-start','executeWorkflowTrigger'),node('write','httpRequest',{url:'https://example.test/write',method:'POST',sendBody:true,specifyBody:'json',jsonBody:'={{ $json }}'}),node('child-wait','wait',{amount:1,unit:'seconds'}),node('child-end')],[['child-start','write'],['write','child-wait'],['child-wait','child-end']]);child.id='child';
 const parent=basic(node('body','executeWorkflow',{source:'database',workflowId:'child'})),input=[{id:1},{id:2}],options=opts({mode:'live',getFlow:()=>child,fetch:fetchMock((_url,init)=>{writes++;return response(JSON.parse(String(init.body)));})});
 let result=await executeFlow(parent,input,options);assert.equal(result.status,'waiting');assert.equal(writes,1);
 for(let i=0;i<2;i++){
  const resumed=clone(result);resumed.resumeAt=new Date(0).toISOString();for(const state of Object.values(resumed.subflows) as any[])for(const job of state.jobs)if(job.execution?.status==='waiting')job.execution.resumeAt=resumed.resumeAt;
  result=await executeFlow(parent,input,{...options,resume:resumed});
 }
 assert.equal(result.status,'success',result.error);assert.equal(writes,2);assert.deepEqual(result.output,input);assert.equal(Object.keys(result.subflows).length,2);assert.equal(result.runs.filter(r=>r.nodeId==='body').length,2);
});
test('self-feedback on the loop output uses real batches without a body and supports empty input safely',async()=>{
 const flow=graph([start(),loop(),node('end')],[['start','loop'],['loop','loop',1],['loop','end']]);
 const result=await executeFlow(flow,[{id:1},{id:2}],opts());assert.equal(result.status,'success');assert.deepEqual(result.output,[{id:1},{id:2}]);assert.equal(result.runs.filter(r=>r.nodeId==='loop').length,3);
 const empty=await executeFlow(flow,[],opts());assert.equal(empty.status,'success');assert.equal(empty.runs.filter(r=>r.nodeId==='loop').length,1);assert.deepEqual(empty.output,undefined);
});
test('resume from a saved loop checkpoint reconstructs state and never replays a completed write',async()=>{
 const controller=new AbortController(),posted:number[]=[],flow=basic(node('body','httpRequest',{url:'https://example.test/write',method:'POST',sendBody:true,specifyBody:'json',jsonBody:'={{ $json }}'})),input=[{id:1},{id:2},{id:3}];
 const options=opts({mode:'live',signal:controller.signal,fetch:fetchMock((_url,init)=>{const item=JSON.parse(String(init.body));posted.push(item.id);return response(item);}),onProgress:execution=>{if(execution.runs.filter(run=>run.nodeId==='body').length===1)controller.abort();}});
 const interrupted=await executeFlow(flow,input,options);assert.equal(interrupted.status,'canceled');assert.deepEqual(posted,[1]);
 const restored=await executeFlow(flow,input,{...options,signal:undefined,onProgress:undefined,resume:JSON.parse(JSON.stringify(interrupted))});assert.equal(restored.status,'success',restored.error);assert.deepEqual(posted,[1,2,3]);assert.deepEqual(restored.output,input);assert.equal(restored.runs.filter(run=>run.nodeId==='body').length,3);
});
test('reset with an IF termination branch uses incoming page data instead of repeating original input',async()=>{
 const branch=node('branch','if',{conditions:{options:{typeValidation:'strict'},conditions:[{leftValue:'={{ $json.page }}',rightValue:3,operator:{type:'number',operation:'lt'}}],combinator:'and'}});
 const flow=graph([start(),loop('pages',1,{reset:true}),node('next','code',{jsCode:'return [{json:{page:$json.page+1}}];'}),branch,node('exit'),node('done')],[['start','pages'],['pages','next',1],['next','branch'],['branch','pages',0],['branch','exit',1],['pages','done']]);
 const result=await executeFlow(flow,[{page:0}],opts({maxSteps:30}));assert.equal(result.status,'success',result.error);assert.deepEqual(result.runs.filter(run=>run.nodeId==='next').map(run=>run.input[0].page),[0,1,2]);assert.equal(result.runs.filter(run=>run.nodeId==='pages').length,3);assert.equal(result.loopContexts.pages.done,false);assert.equal(result.runs.at(-1)?.status,'skipped');
});
