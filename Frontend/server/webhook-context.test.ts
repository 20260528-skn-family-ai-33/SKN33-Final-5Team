import test from 'node:test';
import assert from 'node:assert/strict';
import type {Execution,Flow,FlowNode,Json,NodeRun} from '../lib/workflow';
import {webhookResponseItems,webhookResponseResolver} from './webhook-context';
import {executeFlow} from './engine';
import {buildWebhookResponse} from './webhook-response';

const node=(id:string,type:string,parameters:Json={}):FlowNode=>({id,name:id,type,typeVersion:1,position:[0,0],parameters});
const hook=()=>node('hook','n8n-nodes-base.webhook');
const record=(id:string,output:Json[][],extra:Partial<NodeRun>={}):NodeRun=>({nodeId:id,name:id,input:[],output,status:'success',duration:0,...extra});
const execution=(runs:NodeRun[]):Execution=>({id:'execution-fixture',workflowId:'flow',workflowName:'flow',status:'success',startedAt:'',duration:0,mode:'live',runs,output:[{fromFlattenedOutput:true}]});
function graph(nodes:FlowNode[],edges:[string,string,number?][]):Flow {const connections:Flow['connections']={};for(const [source,target,output=0] of edges){const channels=connections[source]??={main:[]};while(channels.main.length<=output)channels.main.push([]);channels.main[output].push({node:target,type:'main',index:0});}return {id:'webhook-context',name:'응답 문맥 검증',nodes,connections,settings:{timezone:'Asia/Seoul'}};}
const options={mode:'live' as const,credential:()=>{throw new Error('no external accounts');}};

test('immediate response expressions expose actual execution ID, production mode, input and parameters',()=>{
 const trigger=hook();trigger.parameters={path:'customer',options:{responseData:'={{ $execution.id }}',responseCode:{values:{responseCode:201}},responseHeaders:{entries:[{name:'X-Mode',value:'={{ $execution.mode }}'}]}}};const input=[{body:{name:'가'}},{body:{name:'나'}}],resolve=webhookResponseResolver(trigger,'reserved-id',input);
 assert.deepEqual(resolve('={{ {id:$execution.id,mode:$execution.mode,name:$json.body.name,count:$input.all().length,path:$parameter.path,index:$itemIndex} }}',input[1]),{id:'reserved-id',mode:'production',name:'나',count:2,path:'customer',index:1});
 assert.deepEqual(buildWebhookResponse(trigger,input,'onReceived',resolve),{statusCode:201,headers:{'x-mode':'production'},body:'reserved-id'});
 assert.equal(resolve('={{ $("future").isExecuted }}',input[0]),false);assert.throws(()=>resolve('={{ $("future").first().json }}',input[0]),/아직 실행/);
 assert.throws(()=>resolve('={{ process.env.SECRET }}',input[0]),/표현식/);assert.throws(()=>resolve('={{ $execution.constructor }}',input[0]),/표현식/);
});
test('last response selects only output zero and never reuses flattened, skipped, or other-port data',()=>{
 const final=record('branch',[[],[{id:'other-port'}]]),skipped=record('skipped',[[{id:'unreachable'}]],{status:'skipped'}),result=execution([record('before',[[{id:'before'}]]),final,skipped]);assert.deepEqual(webhookResponseItems(result),[]);
 final.output=[[{id:'zero'}],[{id:'one'}]];assert.deepEqual(webhookResponseItems(result),[{id:'zero'}]);assert.deepEqual(webhookResponseItems(execution([])),[]);
 final.status='error';final.continued=true;assert.deepEqual(webhookResponseItems(result),[{id:'zero'}]);
});
test('final response .item follows real sort and transformation provenance rather than positional input',async()=>{
 const start=hook(),sort=node('sort','n8n-nodes-base.sort',{sortFieldsUi:{sortField:[{fieldName:'score',order:'descending'}]}}),transform=node('transform','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {score:$json.score} }}'}),input=[{name:'A',score:40},{name:'B',score:90},{name:'C',score:70}],result=await executeFlow(graph([start,sort,transform],[['hook','sort'],['sort','transform']]),input,options);assert.equal(result.status,'success',result.error);const items=webhookResponseItems(result),resolve=webhookResponseResolver(start,result.id,input,result);
 assert.equal(resolve('={{ $("hook").item.json.name }}',items[0]),'B');assert.equal(resolve('={{ $("hook").item.json.name }}',items[1]),'C');assert.equal(resolve('={{ $("hook").itemMatching(2).json.name }}',items[0]),'A');assert.equal(resolve('={{ $input.all().length }}',items[0]),3);assert.equal(resolve('={{ $execution.id }}',items[0]),result.id);assert.equal(resolve('={{ $("transform").item.json.score }}',items[1]),70);
});
test('explicit previous outputs and run indexes use nearest visible scope without exposing siblings',()=>{
 const runs=[record('source',[[{value:'root0'}],[{value:'port-one'}]],{runIndex:0,executionScope:'root',executionKey:'root/source'}),record('source',[[{value:'ancestor1'}]],{runIndex:1,executionScope:'root/outer/batch0',executionKey:'root/outer/batch0/source'}),record('source',[[{value:'sibling'}]],{runIndex:2,executionScope:'root/outer/batch1',executionKey:'root/outer/batch1/source'}),record('siblingOnly',[[{secret:'sibling'}]],{executionScope:'root/outer/batch1',executionKey:'root/outer/batch1/siblingOnly'}),record('skipped',[[{value:'skip'}]],{status:'skipped',executionScope:'root/outer/batch0/inner/batch0'}),record('final',[[{value:'current'}]],{executionScope:'root/outer/batch0/inner/batch0',executionKey:'root/outer/batch0/inner/batch0/final',runIndex:4})],result=execution(runs),items=webhookResponseItems(result),resolve=webhookResponseResolver(hook(),result.id,[],result);
 assert.equal(resolve('={{ $("source").first().json.value }}',items[0]),'ancestor1');assert.equal(resolve('={{ $("source").first(1,0).json.value }}',items[0]),'port-one');assert.equal(resolve('={{ $("source").first(0,-1).json.value }}',items[0]),'ancestor1');assert.equal(resolve('={{ $runIndex }}',items[0]),4);
 for(const name of ['siblingOnly','skipped','future']){assert.equal(resolve(`={{ $("${name}").isExecuted }}`,items[0]),false);assert.throws(()=>resolve(`={{ $("${name}").first().json }}`,items[0]),/아직 실행/);}
 assert.throws(()=>resolve('={{ $("source").first(0,2).json }}',items[0]),/아직 실행/);assert.throws(()=>resolve('={{ $("source").all(5) }}',items[0]),/경로/);
});
test('final loop response traces exact historical body records while first/all retain scoped availability',async()=>{
 const start=hook(),loop=node('loop','n8n-nodes-base.splitInBatches',{batchSize:1});loop.typeVersion=3;const body=node('body','n8n-nodes-base.set',{mode:'raw',jsonOutput:'={{ {score:$json.score+1} }}'}),done=node('done','n8n-nodes-base.noOp'),input=[{name:'A',score:1},{name:'B',score:2}],result=await executeFlow(graph([start,loop,body,done],[['hook','loop'],['loop','body',1],['body','loop'],['loop','done',0]]),input,options);assert.equal(result.status,'success',result.error);const items=webhookResponseItems(result),resolve=webhookResponseResolver(start,result.id,input,result);
 assert.equal(resolve('={{ $("hook").item.json.name }}',items[1]),'B');assert.equal(resolve('={{ $("body").item.json.score }}',items[1]),3);assert.equal(resolve('={{ $("body").isExecuted }}',items[0]),false);assert.throws(()=>resolve('={{ $("body").first().json.score }}',items[0]),/아직 실행/);
});
test('ambiguous or missing causal originals fail clearly and never fall back to same output index',()=>{
 const source=record('source',[[{id:'A'},{id:'B'}]],{executionKey:'root/source'}),aggregate=record('aggregate',[[{count:2}]],{executionKey:'root/aggregate',input:[{id:'A'},{id:'B'}],inputRefs:[[{run:'root/source',output:0,item:0}],[{run:'root/source',output:0,item:1}]],outputPairedItems:[[[{item:0},{item:1}]]]}),result=execution([source,aggregate]),resolve=webhookResponseResolver(hook(),result.id,[],result),items=webhookResponseItems(result);assert.throws(()=>resolve('={{ $("source").item.json.id }}',items[0]),/여러 원본/);assert.equal(resolve('={{ $("source").first().json.id }}',items[0]),'A');
 aggregate.outputPairedItems=[[[]]];assert.throws(()=>webhookResponseResolver(hook(),result.id,[],result)('={{ $("source").item.json.id }}',items[0]),/출처/);
});
