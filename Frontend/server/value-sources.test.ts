import test from 'node:test';
import assert from 'node:assert/strict';
import {editorValueSources} from '../lib/value-sources';
import {buildValuePaths} from '../lib/value-paths';
import {lookupItemSource,type ItemSourceRef,type TraceRecord} from '../lib/item-provenance';
import {resolveExpression} from './expressions';
import type {Execution,FlowNode,NodeRun} from '../lib/workflow';
const node={id:'current',name:'현재',type:'n8n-nodes-base.set',typeVersion:3.4,position:[0,0],parameters:{}} as FlowNode;
const run=(id:string,scope:string,value:string,status:NodeRun['status']='success'):NodeRun=>({nodeId:id,name:id,input:[],output:[[{value}]],status,duration:0,executionScope:scope,runIndex:0});
test('Value picker excludes future activations, sibling batches, failed and skipped records',()=>{
 const current=run('current','root/loop/batch1','now');current.input=[{selected:1}];
 const execution={runs:[run('setup','root','global'),run('each','root/loop/batch0','old'),run('each','root/loop/batch1','new'),run('failed','root/loop/batch1','error','error'),run('skipped','root/loop/batch1','none','skipped'),current,run('future','root/loop/batch1','future')]} as Execution;
 const sources=editorValueSources(node,execution,current);
 assert.deepEqual(sources.map(source=>source.nodeName??'input'),['input','setup','each']);
 assert.equal(sources[2].items[0].value,'new');
 assert.deepEqual(sources[0].items,[{selected:1}]);
});
test('Value picker selects the nearest scope and does not expose completed loop batches outside the loop',()=>{
 const current=run('current','root/outer/batch0/inner/batch0','now');
 const execution={runs:[run('same','root/outer/batch0','outer'),run('same','root/outer/batch0/inner/batch0','inner'),run('same','root/outer/batch0','later outer'),current]} as Execution;
 assert.equal(editorValueSources(node,execution,current)[1].items[0].value,'inner');
 assert.deepEqual(editorValueSources(node,execution).map(source=>source.nodeName??'input'),['input']);
});
test('Legacy loop records infer their scope from activation keys rather than leaking prior batches',()=>{
 const old=run('each','root/loop/batch0','old'),now=run('each','root/loop/batch1','now'),current=run('current','root/loop/batch1','selected');
 for(const record of [old,now,current]){record.executionKey=record.executionScope+'/'+record.nodeId;delete record.executionScope;}
 const sources=editorValueSources(node,{runs:[old,now,current]} as Execution,current);
 assert.deepEqual(sources.filter(source=>source.kind==='node').map(source=>source.items[0].value),['now']);
});

const ref=(run:string,item=0,output=0):ItemSourceRef=>({run,item,output});
const trace=(record:NodeRun):TraceRecord=>({key:record.executionKey||record.nodeId,name:record.name,output:record.output,inputRefs:record.inputRefs,outputPairedItems:record.outputPairedItems,outputSourceRefs:record.outputSourceRefs,inputPortCounts:record.inputPortCounts});
test('sort and filter selections follow every exact original and preview the first linked input',()=>{
 const customers:NodeRun={...run('고객','root','unused'),output:[[{name:'고객 A'},{name:'고객 B'},{name:'고객 C'}]]};
 const sorted:NodeRun={...run('정렬','root','unused'),input:customers.output[0],output:[[{rank:1},{rank:2}]],inputRefs:[[ref('고객',0)],[ref('고객',1)],[ref('고객',2)]],outputPairedItems:[[[{item:2}],[{item:0}]]]};
 const current:NodeRun={...run('current','root','now'),input:sorted.output[0],inputRefs:[[ref('정렬',0)],[ref('정렬',1)]]};
 const sources=editorValueSources(node,{runs:[customers,sorted,current]} as Execution,current);
 const customer=sources.find(source=>source.nodeName==='고객')!;
 assert.equal(customer.linkedItem?.status,'ready');
 assert.equal(customer.linkedItem?.status==='ready'&&customer.linkedItem.sample.name,'고객 C');
 const path=buildValuePaths([customer]).paths.find(value=>value.label==='name')!;
 assert.equal(path.expression,'={{ $("고객").item.json["name"] }}');assert.equal(path.preview,'"고객 C"');
 const records=new Map([customers,sorted].map(record=>[record.nodeId,trace(record)]));
 const selected=current.input.map((item,index)=>resolveExpression(path.expression,item,{input:current.input,itemIndex:index,linkedItem:(name,itemIndex)=>lookupItemSource(name,itemIndex!,current.inputRefs,key=>records.get(key)).item}));
 assert.deepEqual(selected,['고객 C','고객 A']);
 const first=buildValuePaths([customer],{nodeSelection:'first'}).paths.find(value=>value.label==='name')!;
 assert.equal(first.preview,'"고객 A"');assert.match(first.expression,/\.first\(0, 0\)\.json/);
 assert.deepEqual(current.input.map(item=>resolveExpression(first.expression,item,{node:()=>({items:customers.output[0],isExecuted:true})})),['고객 A','고객 A']);
});

test('merged duplicates remain unique but two real originals and missing rows cannot use item',()=>{
 const customers:NodeRun={...run('고객','root','unused'),output:[[{name:'A'},{name:'B'}]]};
 const merge:NodeRun={...run('합치기','root','unused'),input:customers.output[0],inputRefs:[[ref('고객',0)],[ref('고객',1)]],outputPairedItems:[[[{item:0},{item:0}]]]};
 const current:NodeRun={...run('current','root','now'),input:[{merged:true}],inputRefs:[[ref('합치기')]]};
 const sources=()=>editorValueSources(node,{runs:[customers,merge,current]} as Execution,current);
 assert.equal(sources().find(source=>source.nodeName==='고객')!.linkedItem?.status,'ready');
 merge.outputPairedItems=[[[{item:0},{item:1}]]];
 const ambiguous=sources().find(source=>source.nodeName==='고객')!;
 assert.equal(ambiguous.linkedItem?.status,'unavailable');assert.match(ambiguous.linkedItem?.status==='unavailable'?ambiguous.linkedItem.reason:'',/여러 원본/);
 const unavailable=buildValuePaths([ambiguous]);assert.equal(unavailable.groups[0].selection,'linked');assert.equal(unavailable.paths.length,0);assert.match(unavailable.groups[0].selectionUnavailable!,/여러 원본/);assert.ok(unavailable.groups[0].allExpression);
 const first=buildValuePaths([ambiguous],{nodeSelection:'first'});assert.match(first.paths[1].expression,/\.first\(/);
 current.input=[{merged:true},{missing:true}];current.inputRefs=[[ref('고객',0)],[]];
 assert.equal(sources().find(source=>source.nodeName==='고객')!.linkedItem?.status,'unavailable');
});

test('a linked expression is offered only for its proven output port and activation',()=>{
 const old:NodeRun={...run('조건','root','unused'),executionKey:'root/조건/call0',runIndex:0,output:[[{name:'not linked'}],[{name:'B'}]]};
 const latest:NodeRun={...run('조건','root','unused'),executionKey:'root/조건/call1',runIndex:1,output:[[{name:'latest'}],[]]};
 const current:NodeRun={...run('current','root','now'),input:[{x:1}],inputRefs:[[ref(old.executionKey,0,1)]]};
 const sources=editorValueSources(node,{runs:[old,latest,current]} as Execution,current).filter(source=>source.nodeName==='조건');
 assert.equal(sources.length,3);
 const linked=sources.find(source=>source.linkedItem?.status==='ready')!;
 assert.equal(linked.activationKey,old.executionKey);assert.equal(linked.outputIndex,1);assert.equal(linked.runIndex,0);
 assert.equal(buildValuePaths([linked]).paths[1].preview,'"B"');
 assert.equal(buildValuePaths([linked],{nodeSelection:'first'}).paths[1].expression,'={{ $("조건").first(1, 0).json["name"] }}');
 assert.ok(sources.filter(source=>source.activationKey===latest.executionKey).every(source=>source.linkedItem?.status==='unavailable'));
});

test('loop input-side history resolves the original and causal bodies have linked-only selections',()=>{
 const customers:NodeRun={...run('고객','root','unused'),output:[[{name:'A'},{name:'B'}]],inputRefs:[[],[]]};
 const first:NodeRun={...run('반복','root','unused'),executionKey:'root/반복/call0',runIndex:0,input:customers.output[0],inputRefs:[[ref('고객',0)],[ref('고객',1)]],output:[[],[{name:'A'}]],outputSourceRefs:[[],[[{run:'root/반복/call0',side:'input',item:0,output:0}]]]};
 const second:NodeRun={...run('반복','root','unused'),executionKey:'root/반복/call1',runIndex:1,input:[{name:'A'}],inputRefs:[[ref(first.executionKey,0,1)]],output:[[],[{name:'B'}]],outputSourceRefs:[[],[[{run:first.executionKey,side:'input',item:1,output:0}]]]};
 const body:NodeRun={...run('본문','root/반복/batch1','unused'),executionKey:'root/반복/batch1/본문',input:second.output[1],inputRefs:[[ref(second.executionKey,0,1)]],output:[[{processed:'B'}]],outputPairedItems:[[[{item:0}]]]};
 const unrelated:NodeRun={...run('다른 본문','root/반복/batch0','private sibling'),executionKey:'root/반복/batch0/다른'};
 const done:NodeRun={...run('반복','root','unused'),executionKey:'root/반복/call2',runIndex:2,input:body.output[0],inputRefs:[[ref(body.executionKey)]],output:[[{processed:'B'}],[]],outputSourceRefs:[[[ref(body.executionKey)]],[]]};
 const current:NodeRun={...run('current','root','now'),input:done.output[0],inputRefs:[[ref(done.executionKey)]]};
 const sources=editorValueSources(node,{runs:[customers,first,unrelated,second,body,done,current]} as Execution,current);
 const original=sources.find(source=>source.nodeName==='고객')!;
 assert.equal(original.linkedItem?.status==='ready'&&original.linkedItem.sample.name,'B');
 const historical=sources.find(source=>source.nodeName==='본문')!;
 assert.equal(historical.linkedItem?.status,'ready');assert.equal(historical.explicitSelection,false);
 assert.match(buildValuePaths([historical]).paths[1].expression,/\.item\.json/);
 assert.equal(buildValuePaths([historical]).groups[0].allExpression,undefined);
 const firstMode=buildValuePaths([historical],{nodeSelection:'first'});assert.equal(firstMode.paths.length,0);assert.match(firstMode.groups[0].selectionUnavailable!,/이전 반복/);
 assert.ok(!sources.some(source=>source.nodeName==='다른 본문'));
});

test('future refs, failed origins and detached selected records never certify a linked value',()=>{
 const original=run('원본','root','past');
 const current:NodeRun={...run('current','root','now'),input:[{x:1}],inputRefs:[[ref('future')]]};
 const future=run('future','root','future');
 const sources=editorValueSources(node,{runs:[original,current,future]} as Execution,current);
 assert.equal(sources.find(source=>source.nodeName==='원본')!.linkedItem?.status,'unavailable');assert.ok(!sources.some(source=>source.nodeName==='future'));
 const failed:NodeRun={...run('실패','root','error','error'),inputRefs:[]};current.inputRefs=[[ref('실패')]];
 assert.ok(!editorValueSources(node,{runs:[original,failed,current]} as Execution,current).some(source=>source.nodeName==='실패'));
 current.inputRefs=[[ref('원본')]];
 assert.equal(editorValueSources(node,{runs:[original]} as Execution,current).find(source=>source.nodeName==='원본')!.linkedItem?.status,'unavailable');
});

test('unique links across different activations offer a linked-only group and retain visible first/all',()=>{
 const first:NodeRun={...run('원본','root','A'),executionKey:'root/원본/call0',runIndex:0};
 const second:NodeRun={...run('원본','root','B'),executionKey:'root/원본/call1',runIndex:1};
 const current:NodeRun={...run('current','root','now'),input:[{x:1},{x:2}],inputRefs:[[ref(first.executionKey)],[ref(second.executionKey)]]};
 const sources=editorValueSources(node,{runs:[first,second,current]} as Execution,current).filter(source=>source.nodeName==='원본');
 assert.equal(sources.length,2);assert.equal(sources[0].linkedItem?.status,'unavailable');
 assert.match(sources[0].linkedItem?.status==='unavailable'?sources[0].linkedItem.reason:'',/다른 처리 회차/);
 const mixed=sources[1];assert.equal(mixed.linkedItem?.status,'ready');assert.equal(mixed.explicitSelection,false);
 const paths=buildValuePaths([mixed]);assert.match(paths.groups[0].label,/여러 처리 회차/);assert.match(paths.paths[1].expression,/\.item\.json/);assert.equal(paths.groups[0].allExpression,undefined);
 assert.equal(buildValuePaths([mixed],{nodeSelection:'first'}).paths.length,0);
 assert.ok(buildValuePaths([sources[0]],{nodeSelection:'first'}).groups[0].allExpression);
 const records=new Map([first,second].map(record=>[record.executionKey,trace(record)]));
 assert.deepEqual(current.input.map((item,index)=>resolveExpression(paths.paths[1].expression,item,{input:current.input,itemIndex:index,linkedItem:(name,itemIndex)=>lookupItemSource(name,itemIndex!,current.inputRefs,key=>records.get(key)).item})),['A','B']);
});

test('unique links across output ports also offer a mixed linked-only source',()=>{
 const branch:NodeRun={...run('조건','root','unused'),output:[[{name:'A'}],[{name:'B'}]]};
 const current:NodeRun={...run('current','root','now'),input:[{x:1},{x:2}],inputRefs:[[ref('조건',0,1)],[ref('조건',0,0)]]};
 const sources=editorValueSources(node,{runs:[branch,current]} as Execution,current);
 const mixed=sources.find(source=>source.linkedItem?.status==='ready')!;
 assert.equal(mixed.explicitSelection,false);assert.equal(mixed.linkedItem?.status==='ready'&&mixed.linkedItem.multipleOrigins,true);
 assert.equal(buildValuePaths([mixed]).paths[1].preview,'"B"');
});

test('one screen shares its trace budget even for large duplicate ancestry edges',()=>{
 const original=run('원본','root','A');
 const dense:NodeRun={...run('합치기','root','merged'),outputSourceRefs:[[Array.from({length:100001},()=>ref('원본'))]]};
 const current:NodeRun={...run('current','root','now'),input:[{x:1}],inputRefs:[[ref('합치기')]]};
 const sources=editorValueSources(node,{runs:[original,dense,current]} as Execution,current);
 const source=sources.find(value=>value.nodeName==='원본')!;
 assert.equal(source.linkedItem?.status,'unavailable');assert.match(source.linkedItem?.status==='unavailable'?source.linkedItem.reason:'',/범위를 넘/);
 const data=buildValuePaths([source]);assert.equal(data.paths.length,0);assert.ok(data.groups[0].allExpression);
 assert.match(buildValuePaths([source],{nodeSelection:'first'}).paths[1].expression,/\.first\(/);
});
