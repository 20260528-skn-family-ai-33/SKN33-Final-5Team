import test from 'node:test';
import assert from 'node:assert/strict';
import {attachItemPairs,getItemPairs,lookupItemSource,lookupItemSources,pairInputIndex,type TraceRecord,type ItemSourceRef} from './provenance';
const ref=(run:string,item=0,output=0):ItemSourceRef=>({run,item,output});
const get=(records:TraceRecord[])=>{const map=new Map(records.map(record=>[record.key,record]));return (id:string)=>map.get(id);};
test('pair metadata stays on the array and never changes user JSON serialization',()=>{
 const rows=[{id:1},{id:2}],paired=attachItemPairs(rows,[[{item:3}],[{item:1,input:1}]]);
 assert.equal(paired,rows);assert.deepEqual(getItemPairs(rows),[[{item:3}],[{item:1,input:1}]]);assert.equal(JSON.stringify(rows),'[{"id":1},{"id":2}]');assert.deepEqual(Object.keys(rows),['0','1']);
 assert.deepEqual(getItemPairs(attachItemPairs([],[])),[]);assert.throws(()=>attachItemPairs(rows,[]));assert.throws(()=>attachItemPairs(rows,[[{item:-1}],[]]));
});
test('filter, reorder and split links select exact original output rather than the new position',()=>{
 const start:TraceRecord={key:'start',name:'original',output:[[{id:'A'},{id:'B'},{id:'C'}]],inputRefs:[[],[],[]],outputPairedItems:[[[{item:0}],[{item:1}],[{item:2}]]]};
 const reorder:TraceRecord={key:'sort',name:'sort',output:[[{id:'C'},{id:'A'}]],inputRefs:[[ref('start',0)],[ref('start',1)],[ref('start',2)]],outputPairedItems:[[[{item:2}],[{item:0}]]]};
 const split:TraceRecord={key:'split',name:'split',output:[[{child:1},{child:2},{child:3}]],inputRefs:[[ref('sort',0)],[ref('sort',1)]],outputPairedItems:[[[{item:0}],[{item:0}],[{item:1}]]]};
 const refs=[[ref('split',0)],[ref('split',1)],[ref('split',2)]],lookup=get([start,reorder,split]);
 assert.deepEqual(refs.map((_,index)=>lookupItemSource('original',index,refs,lookup).item.id),['C','C','A']);
});
test('port indexes map to flat inputs including empty ports and duplicate ancestry is deduplicated',()=>{
 assert.equal(pairInputIndex({item:1,input:2},[2,0,3],5),3);assert.throws(()=>pairInputIndex({item:0,input:1},[2,0,3],5));
 const original:TraceRecord={key:'start',name:'original',output:[[{id:'A'}]],inputRefs:[[]]};
 const left:TraceRecord={key:'left',name:'left',output:[[{left:true}]],inputRefs:[[ref('start')]],outputPairedItems:[[[{item:0}]]]};
 const right:TraceRecord={key:'right',name:'right',output:[[{right:true}]],inputRefs:[[ref('start')]],outputPairedItems:[[[{item:0}]]]};
 const merge:TraceRecord={key:'merge',name:'merge',output:[[{merged:true}]],inputRefs:[[ref('left')],[ref('right')]],inputPortCounts:[1,1],outputPairedItems:[[[{item:0,input:0},{item:0,input:1}]]]};
 assert.equal(lookupItemSource('original',0,[[ref('merge')]],get([original,left,right,merge])).item.id,'A');
});
test('multiple real originals are ambiguous and unknown origins never receive a positional fallback',()=>{
 const original:TraceRecord={key:'start',name:'original',output:[[{id:'A'},{id:'B'}]],inputRefs:[[],[]]};
 const aggregate:TraceRecord={key:'aggregate',name:'aggregate',output:[[{ids:['A','B']}]],inputRefs:[[ref('start',0)],[ref('start',1)]],outputPairedItems:[[[{item:0},{item:1}]]]};
 const lookup=get([original,aggregate]);assert.throws(()=>lookupItemSource('original',0,[[ref('aggregate')]],lookup),/여러 원본/);assert.equal(lookupItemSources('original',0,[[ref('aggregate')]],lookup).length,2);
 const unlinked={...aggregate,outputPairedItems:[[[]]]};assert.throws(()=>lookupItemSource('original',0,[[ref('aggregate')]],get([original,unlinked])),/출처/);
 assert.throws(()=>lookupItemSource('original',0,[[ref('missing')]],lookup),/출처/);
});
test('loop references preserve original first-activation inputs and actual historical feedback',()=>{
 const start:TraceRecord={key:'start',name:'original',output:[[{id:'A'},{id:'B'}]],inputRefs:[[],[]]};
 const first:TraceRecord={key:'loop/call0',name:'loop',output:[[],[{id:'A'}]],inputRefs:[[ref('start',0)],[ref('start',1)]],outputSourceRefs:[[],[[{run:'loop/call0',side:'input',item:0,output:0}]]]};
 const second:TraceRecord={key:'loop/call1',name:'loop',output:[[],[{id:'B'}]],inputRefs:[[ref('loop/call0',0,1)]],outputSourceRefs:[[],[[{run:'loop/call0',side:'input',item:1,output:0}]]]};
 const body:TraceRecord={key:'loop/batch1/body',name:'body',output:[[{changed:true}]],inputRefs:[[ref('loop/call1',0,1)]],outputPairedItems:[[[{item:0}]]]};
 const done:TraceRecord={key:'loop/call2',name:'loop',output:[[{changed:true}],[]],inputRefs:[[ref('loop/batch1/body')]],outputSourceRefs:[[[ref('loop/batch1/body')]],[]]};
 const lookup=get([start,first,second,body,done]);assert.equal(lookupItemSource('original',0,[[ref('loop/call2')]],lookup).item.id,'B');assert.equal(lookupItemSource('body',0,[[ref('loop/call2')]],lookup).item.changed,true);
});
test('malformed refs, unsupported old trace and cycles stop with explicit errors',()=>{
 const cycle:TraceRecord={key:'cycle',name:'cycle',output:[[{v:1}]],inputRefs:[[ref('cycle')]],outputPairedItems:[[[{item:0}]]]};
 assert.throws(()=>lookupItemSource('original',0,[[ref('cycle')]],get([cycle])),/출처/);
 assert.throws(()=>lookupItemSource('original',0,[[{run:'cycle',output:-1,item:0}]],get([cycle])),/올바르지/);
 const old:TraceRecord={key:'old',name:'old',output:[[{v:1}]]};assert.throws(()=>lookupItemSource('original',0,[[ref('old')]],get([old])),/출처/);
});
test('repeated edges hit the bounded traversal budget even when unique refs are few',()=>{
 const original:TraceRecord={key:'original',name:'original',output:[[{id:'A'}]],inputRefs:[[]]},dense:TraceRecord={key:'dense',name:'dense',output:[[{id:'dense'}]],inputRefs:[],outputSourceRefs:[[Array.from({length:200001},()=>ref('original'))]]};
 assert.throws(()=>lookupItemSource('original',0,[[ref('dense')]],get([original,dense])),/추적 한도/);
});
