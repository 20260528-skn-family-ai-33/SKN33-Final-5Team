import test from 'node:test';
import assert from 'node:assert/strict';
import {executeDataNode,normalOutputCount,type DataContext} from './data-nodes';
import {matchConditions,resolveValue} from './engine';
import {runCode} from './sandbox';
import {attachItemPairs,getItemPairs} from './provenance';
import type {FlowNode,Json} from '../lib/workflow';
const stage=(type:string,parameters:Json):FlowNode=>({id:type,name:type,type:`n8n-nodes-base.${type}`,typeVersion:1,position:[0,0],parameters});
const ctx:DataContext={resolve:resolveValue,matches:matchConditions,code:runCode};
const condition=(value:any,operation='equals',type='string',field='route')=>({options:{typeValidation:'strict',caseSensitive:true},conditions:[{leftValue:`={{ $json.${field} }}`,rightValue:value,operator:{type,operation}}],combinator:'and'});

test('Switch uses rules.values with distinct output indexes, fallback and first/all matching policies',async()=>{
 const node=stage('switch',{mode:'rules',rules:{values:[{conditions:condition('vip')},{conditions:condition('v','startsWith')}]},options:{fallbackOutput:'extra'}}),input=[{route:'vip'},{route:'visitor'},{route:'other'}];
 assert.equal(normalOutputCount(node),3);assert.deepEqual(await executeDataNode(node,input,ctx),[[input[0]],[input[1]],[input[2]]]);node.parameters.options.allMatchingOutputs=true;assert.deepEqual(await executeDataNode(node,input,ctx),[[input[0]],[input[0],input[1]],[input[2]]]);
 node.parameters.options.fallbackOutput=0;assert.deepEqual(await executeDataNode(node,input,ctx),[[input[0],input[2]],[input[0],input[1]]]);
});
test('Switch expression routes typed item indexes and rejects out-of-range or string indexes',async()=>{
 const node=stage('switch',{mode:'expression',numberOutputs:3,output:'={{ $json.route }}'}),input=[{route:2},{route:0}];assert.deepEqual(await executeDataNode(node,input,ctx),[[input[1]],[],[input[0]]]);await assert.rejects(executeDataNode(node,[{route:3}],ctx),/경로 번호/);await assert.rejects(executeDataNode(node,[{route:'1'}],ctx),/경로 번호/);
});
test('Filter removes unmatched items and supports strict, loose, OR, date and array-length conditions',async()=>{
 const node=stage('filter',{conditions:condition(80,'gte','number','score'),options:{}});assert.deepEqual(await executeDataNode(node,[{score:90},{score:60}],ctx),[[{score:90}]]);await assert.rejects(executeDataNode(node,[{score:'90'}],ctx),/종류/);node.parameters.looseTypeValidation=true;assert.deepEqual(await executeDataNode(node,[{score:'90'}],ctx),[[{score:'90'}]]);
 assert.equal(matchConditions(condition('2026-01-01','after','dateTime','date'),{date:'2026-10-07'}),true);assert.equal(matchConditions(condition(2,'lengthGte','array','items'),{items:[1,2]}),true);
 const or={...condition('vip'),combinator:'or',conditions:[...condition('vip').conditions,...condition('gold').conditions]};assert.equal(matchConditions(or,{route:'gold'}),true);
});
test('Limit keeps first or last exact items; Sort uses official sortFieldsUi.sortField and stable ties',async()=>{
 const input=[{id:'A',team:'Zulu',score:4},{id:'B',team:'alpha',score:2},{id:'C',team:'ALPHA',score:8},{id:'D',team:'alpha',score:8}];
 assert.deepEqual((await executeDataNode(stage('limit',{maxItems:2,keep:'lastItems'}),input,ctx))[0].map(v=>v.id),['C','D']);assert.deepEqual((await executeDataNode(stage('limit',{}),input,ctx))[0].map(v=>v.id),['A']);
 const sort=stage('sort',{type:'simple',sortFieldsUi:{sortField:[{fieldName:'team',order:'ascending'},{fieldName:'score',order:'descending'}]},options:{}});assert.deepEqual((await executeDataNode(sort,input,ctx))[0].map(v=>v.id),['C','D','B','A']);await assert.rejects(executeDataNode(stage('sort',{sortFieldsUi:{sortField:[{fieldName:'missing'}]}}),input,ctx),/찾을 수 없어요/);
});
test('Sort code runs its comparator inside QuickJS and random sort retains every item',async()=>{
 const input=[{value:2},{value:1},{value:3}];assert.deepEqual(await executeDataNode(stage('sort',{type:'code',code:'return b.json.value-a.json.value;'}),input,ctx),[[{value:3},{value:2},{value:1}]]);const random=(await executeDataNode(stage('sort',{type:'random'}),input,ctx))[0];assert.deepEqual(random.map(v=>v.value).sort(),[1,2,3]);
});
test('SplitOut flattens object lists, preserves selected/all fields and aligns multiple lists by index',async()=>{
 assert.deepEqual(await executeDataNode(stage('splitOut',{fieldToSplitOut:'items'}),[{items:[{name:'A'},{name:'B'}]}],ctx),[[{name:'A'},{name:'B'}]]);
 assert.deepEqual(await executeDataNode(stage('splitOut',{fieldToSplitOut:'nested.items',include:'allOtherFields',options:{destinationFieldName:'result.customer'}}),[{order:7,nested:{items:['A','B'],keep:true}}],ctx),[[{order:7,nested:{keep:true},result:{customer:'A'}},{order:7,nested:{keep:true},result:{customer:'B'}}]]);
 assert.deepEqual(await executeDataNode(stage('splitOut',{fieldToSplitOut:'names,scores',include:'selectedOtherFields',fieldsToInclude:'order',options:{destinationFieldName:'name,score'}}),[{order:7,names:['A','B'],scores:[8]}],ctx),[[{order:7,name:'A',score:8},{order:7,name:'B'}]]);
 await assert.rejects(executeDataNode(stage('splitOut',{fieldToSplitOut:'items'}),[{items:Array.from({length:5001},()=>1)}],ctx),/5,000/);
 await assert.rejects(executeDataNode(stage('splitOut',{fieldToSplitOut:'items',options:{destinationFieldName:'__proto__.polluted'}}),[{items:[1]}],ctx),/항목 이름/);assert.equal(({} as any).polluted,undefined);
});
test('RemoveDuplicates compares structural equality and selected nested fields without changing first item',async()=>{
 const all=[{a:1,b:2},{b:2,a:1},{a:2,b:2}];assert.deepEqual(await executeDataNode(stage('removeDuplicates',{operation:'removeDuplicateInputItems',compare:'allFields'}),all,ctx),[[all[0],all[2]]]);
 const input=[{id:1,person:{email:'a@example.test'},time:1},{id:2,person:{email:'a@example.test'},time:2},{id:3,person:{email:'b@example.test'},time:1}];const node=stage('removeDuplicates',{operation:'removeDuplicateInputItems',compare:'selectedFields',fieldsToCompare:'person.email',options:{removeOtherFields:true}});assert.deepEqual(await executeDataNode(node,input,ctx),[[{person:{email:'a@example.test'}},{person:{email:'b@example.test'}}]]);await assert.rejects(executeDataNode(stage('removeDuplicates',{operation:'removeItemsSeenInPreviousExecutions'}),input,ctx),/이전 실행/);
});
test('Aggregate uses official individual-field shape, mergeLists, keepMissing and output-name validation',async()=>{
 const node=stage('aggregate',{aggregate:'aggregateIndividualFields',fieldsToAggregate:{fieldToAggregate:[{fieldToAggregate:'person.name',renameField:true,outputFieldName:'names'},{fieldToAggregate:'tags'}]},options:{mergeLists:true,keepMissing:true}});
 assert.deepEqual(await executeDataNode(node,[{person:{name:'A'},tags:['x']},{person:{name:'B'},tags:['y',null]},{person:{},tags:[]}],ctx),[[{names:['A','B',null],tags:['x','y',null]}]]);
 node.parameters.fieldsToAggregate.fieldToAggregate.push({fieldToAggregate:'other',renameField:true,outputFieldName:'names'});await assert.rejects(executeDataNode(node,[{}],ctx),/서로 달라야/);
 assert.deepEqual(await executeDataNode(stage('aggregate',{aggregate:'aggregateAllItemData',destinationFieldName:'records',include:'specifiedFields',fieldsToInclude:'name'}),[{name:'A',secret:'x'},{name:'B',secret:'y'}],ctx),[[{records:[{name:'A'},{name:'B'}]}]]);
});
test('StopAndError throws configured messages or parsed error-object descriptions',async()=>{
 await assert.rejects(executeDataNode(stage('stopAndError',{errorType:'errorMessage',errorMessage:'{{ $json.reason }}'}),[{reason:'검증 실패'}],ctx),/검증 실패/);await assert.rejects(executeDataNode(stage('stopAndError',{errorType:'errorObject',errorObject:'{"description":"요청 실패"}'}),[{}],ctx),/요청 실패/);
});
test('Filter, Limit, Switch and Sort preserve actual original indexes in hidden output metadata',async()=>{
 const input=[{score:60,route:'other'},{score:90,route:'vip'},{score:70,route:'visitor'},{score:92,route:'vip'}],serialized=JSON.stringify(input);
 const filtered=await executeDataNode(stage('filter',{conditions:condition(80,'gte','number','score')}),input,ctx);assert.deepEqual(getItemPairs(filtered[0]),[[{item:1}],[{item:3}]]);
 const limited=await executeDataNode(stage('limit',{maxItems:2,keep:'lastItems'}),input,ctx);assert.deepEqual(getItemPairs(limited[0]),[[{item:2}],[{item:3}]]);
 const sorted=await executeDataNode(stage('sort',{sortFieldsUi:{sortField:[{fieldName:'score',order:'descending'}]}}),input,ctx);assert.deepEqual(getItemPairs(sorted[0]),[[{item:3}],[{item:1}],[{item:2}],[{item:0}]]);
 const routed=await executeDataNode(stage('switch',{rules:{values:[{conditions:condition('vip')},{conditions:condition('v','startsWith')}]},options:{allMatchingOutputs:true,fallbackOutput:'extra'}}),input,ctx);assert.deepEqual(routed.map(getItemPairs),[[[{item:1}],[{item:3}]],[[{item:1}],[{item:2}],[{item:3}]],[[{item:0}]]]);
 assert.equal(JSON.stringify(filtered),'[[{"score":90,"route":"vip"},{"score":92,"route":"vip"}]]');assert.deepEqual(Object.keys(filtered[0]),['0','1']);assert.equal(JSON.stringify(input),serialized);
 const empty=await executeDataNode(stage('filter',{conditions:condition(100,'gte','number','score')}),input,ctx);assert.deepEqual(getItemPairs(empty[0]),[]);
});
test('Random Sort tracks duplicate references by occurrence and Sort code retains supplied pairing',async()=>{
 const same={value:1},input=[same,same,{value:2}],random=await executeDataNode(stage('sort',{type:'random'}),input,ctx),pairs=getItemPairs(random[0]);assert.ok(pairs);assert.deepEqual(pairs.map(pair=>pair[0].item).sort((a,b)=>a-b),[0,1,2]);for(let i=0;i<random[0].length;i++)assert.strictEqual(random[0][i],input[pairs[i][0].item]);
 const custom:DataContext={...ctx,code:async()=>attachItemPairs([{value:2},{value:1},{value:1}],[[{item:2}],[{item:1}],[{item:0}]])};const sorted=await executeDataNode(stage('sort',{type:'code',code:'return b.json.value-a.json.value;'}),input,custom);assert.deepEqual(getItemPairs(sorted[0]),[[{item:2}],[{item:1}],[{item:0}]]);
});
test('SplitOut and RemoveDuplicates link each output to the contributing input rather than output position',async()=>{
 const split=await executeDataNode(stage('splitOut',{fieldToSplitOut:'values'}),[{values:[]},{values:['a','b']},{values:['c']}],ctx);assert.deepEqual(getItemPairs(split[0]),[[{item:1}],[{item:1}],[{item:2}]]);assert.deepEqual(split,[[{values:'a'},{values:'b'},{values:'c'}]]);
 const dedup=await executeDataNode(stage('removeDuplicates',{compare:'selectedFields',fieldsToCompare:'id',options:{removeOtherFields:true}}),[{id:1,private:'a'},{id:1,private:'b'},{id:2,private:'c'}],ctx);assert.deepEqual(getItemPairs(dedup[0]),[[{item:0}],[{item:2}]]);assert.deepEqual(dedup,[[{id:1},{id:2}]]);
});
test('Aggregate links all participating items and excludes discarded empty field selections',async()=>{
 const input=[{name:'A',value:1},{other:'removed'},{name:'B',value:null}];
 const individual=await executeDataNode(stage('aggregate',{fieldsToAggregate:{fieldToAggregate:[{fieldToAggregate:'value'}]}}),input,ctx);assert.deepEqual(individual,[[{value:[1]}]]);assert.deepEqual(getItemPairs(individual[0]),[[{item:0},{item:1},{item:2}]]);
 const selected=await executeDataNode(stage('aggregate',{aggregate:'aggregateAllItemData',include:'specifiedFields',fieldsToInclude:'name'}),input,ctx);assert.deepEqual(selected,[[{data:[{name:'A'},{name:'B'}]}]]);assert.deepEqual(getItemPairs(selected[0]),[[{item:0},{item:2}]]);
 const excluded=await executeDataNode(stage('aggregate',{aggregate:'aggregateAllItemData',include:'allFieldsExcept',fieldsToExclude:'other'}),input,ctx);assert.deepEqual(getItemPairs(excluded[0]),[[{item:0},{item:2}]]);
 const empty=await executeDataNode(stage('aggregate',{aggregate:'aggregateAllItemData'}),[],ctx);assert.deepEqual(empty,[[{data:[]}]]);assert.deepEqual(getItemPairs(empty[0]),[[]]);assert.equal(JSON.stringify(selected),'[[{"data":[{"name":"A"},{"name":"B"}]}]]');
});
