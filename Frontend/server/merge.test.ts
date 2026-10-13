import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {executeMerge,MERGE_NODE_TYPE,MAX_MERGE_JSON_BYTES} from './merge';
import {BINARY_KEY,createBinaryFile,withBinary,getBinary,decodeBinaryFile} from './binary';
import type {FlowNode,Json} from '../lib/workflow';
import {getItemPairs} from './provenance';

const node=(parameters:Json):FlowNode=>({id:'merge',name:'합치기',type:MERGE_NODE_TYPE,typeVersion:3.2,position:[0,0],parameters});
const run=(parameters:Json,groups:Json[][])=>executeMerge(node(parameters),groups);
const byFields=(parameters:Json={},groups:Json[][])=>run({mode:'combine',combineBy:'combineByFields',fieldsToMatchString:'id',...parameters},groups);
const options=(resolveClash='preferLast',mergeMode='deepMerge',overrideEmpty=false)=>({clashHandling:{values:{resolveClash,mergeMode,overrideEmpty}}});
const binary=(bytes:Buffer,name='file.txt',json:Json={})=>withBinary(json,'data',createBinaryFile(bytes,{mimeType:'text/plain',fileName:name}));

test('append preserves input port and item order including empty ports and clones ordinary data',async()=>{
 const input:Json[][]=[[{id:1,nested:{value:'a'}}],[],[{id:2},{id:3}]],result=await run({mode:'append',numberInputs:3},input);assert.deepEqual(result,[[{id:1,nested:{value:'a'}},{id:2},{id:3}]]);const first=result[0][0];assert.ok(first.nested);first.nested.value='changed';assert.equal(input[0][0].nested.value,'a');assert.deepEqual(await run({},[]),[[]]);
});
test('chooseBranch keeps the selected input unchanged or emits one empty item even with all empty inputs',async()=>{
 assert.deepEqual(await run({mode:'chooseBranch',numberInputs:3,useDataOfInput:3},[[{a:1}],[],[{c:3}]]),[[{c:3}]]);assert.deepEqual(await run({mode:'chooseBranch',useDataOfInput:2},[[{a:1}],[]]),[[]]);assert.deepEqual(await run({mode:'chooseBranch',output:'empty'},[[],[]]),[[{}]]);
 await assert.rejects(run({mode:'chooseBranch',useDataOfInput:0},[[],[]]),/입력 번호/);await assert.rejects(run({mode:'chooseBranch',useDataOfInput:3},[[],[]]),/입력 번호/);await assert.rejects(run({mode:'chooseBranch',chooseBranchMode:'immediately'},[[],[]]),/모든 입력/);
});
test('position combines only paired rows by default, with optional unpaired rows from all ports',async()=>{
 const groups=[[{id:1,a:'left'},{id:2,a:'second'}],[{id:1,b:'right'}]];assert.deepEqual(await run({mode:'combine',combineBy:'combineByPosition'},groups),[[{id:1,a:'left',b:'right'}]]);
 assert.deepEqual(await run({mode:'combine',combineBy:'combineByPosition',options:{includeUnpaired:true}},groups),[[{id:1,a:'left',b:'right'},{id:2,a:'second'}]]);assert.deepEqual(await run({mode:'combine',combineBy:'combineByPosition'},[[],[{b:2}]]),[[]]);
 assert.deepEqual(await run({mode:'combine',combineBy:'combineByPosition',numberInputs:3,options:{includeUnpaired:'true'}},[[],[{b:2}],[{c:3},{c:4}]]),[[{b:2,c:3},{c:4}]]);
});
test('field clashes prefer the last or first input and deep/shallow nested merges are distinct',async()=>{
 const groups=[[{value:'left',nested:{left:1,same:'left'},list:[1,2]}],[{value:'right',nested:{right:2,same:'right'},list:[3]}]];
 assert.deepEqual(await run({mode:'combine',combineBy:'combineByPosition'},groups),[[{value:'right',nested:{left:1,right:2,same:'right'},list:[3,2]}]]);
 assert.deepEqual(await run({mode:'combine',combineBy:'combineByPosition',options:options('preferInput1')},groups),[[{value:'left',nested:{left:1,right:2,same:'left'},list:[1,2]}]]);
 assert.deepEqual(await run({mode:'combine',combineBy:'combineByPosition',options:options('preferLast','shallowMerge')},groups),[[{value:'right',nested:{right:2,same:'right'},list:[3]}]]);
 assert.deepEqual(groups[0][0].nested,{left:1,same:'left'});assert.deepEqual(groups[0][0].list,[1,2]);
});
test('addSuffix keeps every top-level field with its original input number including unpaired rows',async()=>{
 const result=await run({mode:'combine',combineBy:'combineByPosition',numberInputs:3,options:{...options('addSuffix'),includeUnpaired:true}},[[{id:1,left:true}],[{id:2}, {id:4}],[{nested:{a:3}}]]);
 assert.deepEqual(result,[[{id_1:1,left_1:true,id_2:2,nested_3:{a:3}},{id_2:4}]]);
});
test('overrideEmpty retains existing nonempty values and does not mistake false/zero/arrays for empty',async()=>{
 const groups=[[{id:1,name:'민지',nested:{x:'keep',y:1},flag:true,score:3,array:[1]}],[{id:1,name:'',nested:{x:null,z:null},flag:false,score:0,array:[],newValue:null}]];
 for(const mergeMode of ['deepMerge','shallowMerge']){const result=await byFields({options:options('preferLast',mergeMode,true)},groups);assert.equal(result[0][0].name,'민지');assert.equal(result[0][0].flag,false);assert.equal(result[0][0].score,0);assert.equal(result[0][0].newValue,null);}
 const deep=await byFields({options:options('preferLast','deepMerge',true)},groups);assert.deepEqual(deep[0][0].nested,{x:'keep',y:1,z:null});assert.deepEqual(deep[0][0].array,[1]);
});
test('all combinations yield actual cross joins, and Input 1 preference follows documented source iteration',async()=>{
 const groups=[[{a:1,value:'left1'},{a:2,value:'left2'}],[{b:3,value:'right3'},{b:4,value:'right4'}]];
 assert.deepEqual(await run({mode:'combine',combineBy:'combineAll'},groups),[[{a:1,b:3,value:'right3'},{a:1,b:4,value:'right4'},{a:2,b:3,value:'right3'},{a:2,b:4,value:'right4'}]]);
 assert.deepEqual(await run({mode:'combine',combineBy:'combineAll',options:options('preferInput1')},groups),[[{a:1,b:3,value:'left1'},{a:2,b:3,value:'left2'},{a:1,b:4,value:'left1'},{a:2,b:4,value:'left2'}]]);assert.deepEqual(await run({mode:'combine',combineBy:'combineAll'},[[],[{a:1}]]),[[]]);
});
test('matching fields implement inner/outer/left/right/non-match with stable matched then unmatched order',async()=>{
 const groups=[[{id:3,l:'unmatched3'},{id:1,l:'one'},{id:2,l:'two'}],[{id:2,r:'second'},{id:1,r:'first'},{id:4,r:'unmatched4'}]];
 const matched=[{id:1,l:'one',r:'first'},{id:2,l:'two',r:'second'}];assert.deepEqual(await byFields({},groups),[matched]);
 assert.deepEqual(await byFields({joinMode:'keepEverything'},groups),[[...matched,{id:3,l:'unmatched3'},{id:4,r:'unmatched4'}]]);
 assert.deepEqual(await byFields({joinMode:'enrichInput1'},groups),[[...matched,{id:3,l:'unmatched3'}]]);assert.deepEqual(await byFields({joinMode:'enrichInput2'},groups),[[...matched,{id:4,r:'unmatched4'}]]);
 assert.deepEqual(await byFields({joinMode:'keepNonMatches'},groups),[[{id:3,l:'unmatched3',_source:'input1'},{id:4,r:'unmatched4',_source:'input2'}]]);
 assert.deepEqual(await byFields({joinMode:'keepNonMatches',outputDataFrom:'input2'},groups),[[{id:4,r:'unmatched4'}]]);
});
test('multiple matching records produce all or first pairs, while projected matched inputs keep their own order',async()=>{
 const groups=[[{id:1,left:'a'},{id:1,left:'b'}],[{id:1,right:'x'},{id:1,right:'y'}]];
 assert.deepEqual(await byFields({},groups),[[{id:1,left:'a',right:'x'},{id:1,left:'a',right:'y'},{id:1,left:'b',right:'x'},{id:1,left:'b',right:'y'}]]);
 assert.deepEqual(await byFields({options:{multipleMatches:'first'}},groups),[[{id:1,left:'a',right:'x'},{id:1,left:'b',right:'x'}]]);
 assert.deepEqual(await byFields({outputDataFrom:'input1'},groups),[groups[0]]);assert.deepEqual(await byFields({outputDataFrom:'input2'},groups),[groups[1]]);
 assert.deepEqual(await byFields({joinMode:'keepEverything',options:{multipleMatches:'first'}},groups),[[{id:1,left:'a',right:'x'},{id:1,left:'b',right:'x'},{id:1,right:'y'}]]);
});
test('nested/differently named multiple fields, array paths, strict objects and disabled dot notation match correctly',async()=>{
 const parameters={advanced:true,mergeByFields:{values:[{field1:'customer.id',field2:'buyerId'},{field1:'orders[0].region',field2:'area'}]}};
 assert.deepEqual(await byFields(parameters,[[{customer:{id:1},orders:[{region:'서울'}]}],[{buyerId:1,area:'서울'}]]),[[{customer:{id:1},orders:[{region:'서울'}],buyerId:1,area:'서울'}]]);
 assert.deepEqual(await byFields({fieldsToMatchString:'customer.id',options:{disableDotNotation:true}},[[{'customer.id':1}],[{'customer.id':1}]]),[[{'customer.id':1}]]);
 assert.equal((await byFields({},[[{id:{a:1,b:2}}],[{id:{b:2,a:1}}]]))[0].length,1);
 assert.equal((await byFields({fieldsToMatchString:'id, region'},[[{id:1,region:'서울'}],[{id:1,region:'부산'}]]))[0].length,0);
});
test('missing match values never become equal accidentally, while null remains an explicit value',async()=>{
 assert.deepEqual(await byFields({},[[{missing:'left'}],[{missing:'right'}]]),[[]]);assert.equal((await byFields({},[[{id:null}],[{id:null}]]))[0].length,1);
 assert.deepEqual(await byFields({joinMode:'keepEverything'},[[{missing:'left'}],[{missing:'right'}]]),[[{missing:'left'},{missing:'right'}]]);
 // n8n's source treats a missing left lookup as unmatched, including fuzzy mode.
 assert.deepEqual(await byFields({options:{fuzzyCompare:true}},[[{other:1}],[{id:null}]]),[[]]);assert.equal((await byFields({options:{fuzzyCompare:true}},[[{id:null}],[{other:1}]]))[0].length,1);
 for(const explicit of [null,'',[]]){assert.deepEqual(await byFields({options:{fuzzyCompare:true}},[[{other:1}],[{id:explicit}]]),[[]]);assert.equal((await byFields({options:{fuzzyCompare:true}},[[{id:explicit}],[{other:1}]]))[0].length,1);}
 assert.deepEqual(await byFields({options:{fuzzyCompare:true}},[[{id:0}],[{other:1}]]),[[]]);assert.deepEqual(await byFields({options:{fuzzyCompare:true}},[[{id:false}],[{other:1}]]),[[]]);
});
test('fuzzy comparison follows actual numeric, boolean, JSON and empty-value relations without broad coercion',async()=>{
 const cases:[any,any,boolean][]=[[3,'3',true],[3,'03',false],[true,'TRUE',true],[false,0,true],[true,'1',true],[1,'TRUE',false],[null,0,false],[null,'',true],[[],'',true],[{a:1},'{"a":1}',true],['true','TRUE',false]];
 for(const [a,b,expected] of cases){assert.equal((await byFields({options:{fuzzyCompare:true}},[[{id:a}],[{id:b}]]))[0].length,expected?1:0,JSON.stringify([a,b]));}
 assert.equal((await byFields({},[[{id:3}],[{id:'3'}]]))[0].length,0);
});
test('empty inputs retain all five join meanings with both projection directions and official fast-path shape',async()=>{
 const row={id:1};
 for(const joinMode of ['keepMatches','keepEverything','enrichInput1','enrichInput2','keepNonMatches'])for(const outputDataFrom of ['both','input1','input2'])for(const emptyPort of [0,1]){
  const groups=emptyPort===0?[[],[row]]:[[row],[]];const discarded=joinMode==='keepMatches'||joinMode==='enrichInput1'&&emptyPort===0||joinMode==='enrichInput2'&&emptyPort===1||joinMode==='keepNonMatches'&&outputDataFrom===`input${emptyPort+1}`;
  assert.deepEqual(await byFields({joinMode,outputDataFrom},groups),discarded?[[]]:[[row]],`${joinMode}/${outputDataFrom}/empty input ${emptyPort+1}`);
  assert.deepEqual(await byFields({joinMode,outputDataFrom},[[],[]]),[[]]);
 }
});
test('right enrichment default conflict priority follows the source, with explicit override available',async()=>{
 const groups=[[{id:1,value:'left'}],[{id:1,value:'right'}]];assert.deepEqual(await byFields({joinMode:'enrichInput2'},groups),[[{id:1,value:'left'}]]);assert.deepEqual(await byFields({joinMode:'enrichInput2',options:options('preferLast')},groups),[[{id:1,value:'right'}]]);
});
test('binary files remain atomic and unsuffixed, are not read as matching fields, and preserve bytes',async()=>{
 const left=binary(Buffer.from('left'),'left.txt',{id:1}),right=binary(Buffer.from(''),'empty.txt',{id:1});const result=await byFields({options:options('preferLast','deepMerge',true)},[[left],[right]]);assert.equal(getBinary(result[0][0]).fileName,'empty.txt');assert.equal(getBinary(result[0][0]).fileSize,0);assert.equal(decodeBinaryFile(getBinary(result[0][0])).length,0);
 const preferred=await byFields({options:options('preferInput1')},[[left],[right]]);assert.equal(decodeBinaryFile(getBinary(preferred[0][0])).toString(),'left');const suffix=await byFields({options:options('addSuffix')},[[left],[right]]);assert.deepEqual(Object.keys(suffix[0][0][BINARY_KEY]),['data']);assert.equal(suffix[0][0].id_1,1);assert.equal(suffix[0][0].id_2,1);assert.equal(getBinary(left).fileName,'left.txt');
 await assert.rejects(byFields({fieldsToMatchString:BINARY_KEY+'.data.fileName'},[[left],[right]]),/이름/);
});
test('Merge enforces per-port and output 5000 items without expanding huge cartesian results',async()=>{
 const rows=Array.from({length:5000},(_,id)=>({id}));assert.equal((await byFields({},[rows,rows]))[0].length,5000);
 await assert.rejects(run({mode:'append'},[rows,[{id:5001}]]),/5,000/);await assert.rejects(run({mode:'append'},[Array.from({length:5001},()=>({})),[]]),/5,000/);await assert.rejects(run({mode:'combine',combineBy:'combineAll'},[Array.from({length:100},()=>({})),Array.from({length:51},()=>({}))]),/5,000/);
});
test('Merge bounds ordinary UTF-8 JSON separately from binary data and catches repeated output expansion',async()=>{
 await assert.rejects(run({mode:'append'},[[{text:'한'.repeat(1_400_000)}],[]]),/4MB/);
 const row={id:1,text:'x'.repeat(1_500_000)};assert.ok(Buffer.byteLength(JSON.stringify([[row],[{id:1},{id:1},{id:1}]]))<MAX_MERGE_JSON_BYTES);await assert.rejects(byFields({},[[row],[{id:1},{id:1},{id:1}]]),/JSON 결과.*4MB/);
 const attachment=binary(Buffer.alloc(3*1024*1024), 'three.txt',{id:1});await assert.rejects(byFields({},[[attachment],[{id:1},{id:1},{id:1}]]),/결과 파일 총 크기/);await assert.rejects(run({mode:'chooseBranch'},[[binary(Buffer.alloc(5*1024*1024))],[binary(Buffer.alloc(4*1024*1024))]]),/입력 파일 총 크기/);
 assert.equal((await run({mode:'chooseBranch'},[[binary(Buffer.alloc(5*1024*1024))],[]]))[0].length,1);
});
test('malformed graphs/parameters/data and unsafe paths fail explicitly, including unsupported SQL',async()=>{
 for(const parameters of [{mode:'combineBySql'},{mode:'legacy'},{mode:'append',numberInputs:11},{mode:'combine',combineBy:'unknown'}])await assert.rejects(run(parameters,[[],[]]));
 await assert.rejects(byFields({fieldsToMatchString:''},[[],[]]),/항목 이름/);await assert.rejects(byFields({fieldsToMatchString:'__proto__.x'},[[],[]]),/이름/);await assert.rejects(byFields({advanced:true,mergeByFields:{values:[{field1:'id',field2:'x'},{field1:'other',field2:'x'}]}},[[],[]]),/중복/);
 await assert.rejects(run({mode:'append'},[[JSON.parse('{"__proto__":{"polluted":true}}')],[]]),/이름/);assert.equal(({} as Json).polluted,undefined);await assert.rejects(run({mode:'append'},[[{a:NaN}],[]]),/JSON/);await assert.rejects(run({mode:'append'},[[{a:BigInt(1)}],[]]),/JSON/);
 await assert.rejects(run({mode:'combine',combineBy:'combineByPosition'},[[{a:[1]}],[{a:{x:2}}]]),/배열과 객체/);await assert.rejects(byFields({options:{fuzzyCompare:'yes'}},[[],[]]),/참 또는 거짓/);
});
test('inactive numberInputs is harmless for two-port matching/cartesian modes, but extra data is rejected',async()=>{
 assert.deepEqual(await byFields({numberInputs:3},[[{id:1}],[{id:1}]]),[[{id:1}]]);await assert.rejects(byFields({numberInputs:3},[[{id:1}],[{id:1}],[{third:'must not disappear'}]]),/포트/);
});
test('merge catalog describes actual operation identifiers and excludes SQL support',()=>{
 const data=JSON.parse(readFileSync(new URL('./merge-catalog.json',import.meta.url),'utf8')),descriptor=data.catalog[0];assert.equal(descriptor.n8nType,MERGE_NODE_TYPE);assert.equal(descriptor.typeVersion,3.2);assert.deepEqual(descriptor.fields.find((field:any)=>field.key==='mode').options.map((option:any)=>option.value),['append','combine','chooseBranch']);assert.deepEqual(descriptor.fields.find((field:any)=>field.key==='combineBy').options.map((option:any)=>option.value),['combineByFields','combineByPosition','combineAll']);assert.equal(data.unsupported.includes('combineBySql'),true);
});
test('Merge append, input selection and empty output explicitly map input ports without adding JSON fields',async()=>{
 const groups=[[{id:'left0'},{id:'left1'}],[],[{id:'right0'}]],append=await run({mode:'append',numberInputs:3},groups);assert.deepEqual(getItemPairs(append[0]),[[{item:0,input:0}],[{item:1,input:0}],[{item:0,input:2}]]);assert.equal(JSON.stringify(append),'[[{"id":"left0"},{"id":"left1"},{"id":"right0"}]]');assert.deepEqual(Object.keys(append[0]),['0','1','2']);
 const chosen=await run({mode:'chooseBranch',numberInputs:3,useDataOfInput:3},groups);assert.deepEqual(getItemPairs(chosen[0]),[[{item:0,input:2}]]);
 const gate=await run({mode:'chooseBranch',numberInputs:3,output:'empty'},groups);assert.deepEqual(gate,[[{}]]);assert.deepEqual(getItemPairs(gate[0]),[[{item:0,input:0},{item:1,input:0},{item:0,input:2}]]);
 const empty=await run({mode:'chooseBranch',output:'empty'},[[],[]]);assert.deepEqual(getItemPairs(empty[0]),[[]]);assert.deepEqual(getItemPairs((await run({mode:'append'},[[],[]]))[0]),[]);
});
test('Position and cross-join pairing includes every actual participating input and omits missing rows',async()=>{
 const position=await run({mode:'combine',combineBy:'combineByPosition',numberInputs:3,options:{includeUnpaired:true}},[[{a:1},{a:2}],[{b:3}], [{c:4},{c:5}]]);assert.deepEqual(getItemPairs(position[0]),[[{item:0,input:0},{item:0,input:1},{item:0,input:2}],[{item:1,input:0},{item:1,input:2}]]);
 const cross=await run({mode:'combine',combineBy:'combineAll'},[[{a:1},{a:2}],[{b:3},{b:4}]]);assert.deepEqual(getItemPairs(cross[0]),[[{item:0,input:0},{item:0,input:1}],[{item:0,input:0},{item:1,input:1}],[{item:1,input:0},{item:0,input:1}],[{item:1,input:0},{item:1,input:1}]]);
});
test('Field matching links each matched pair, unmatched row and projected input at original indexes',async()=>{
 const groups=[[{id:9,left:'unmatched'},{id:1,left:'a'},{id:1,left:'b'}],[{id:1,right:'x'},{id:1,right:'y'},{id:8,right:'unmatched'}]];
 const all=await byFields({joinMode:'keepEverything'},groups);assert.deepEqual(getItemPairs(all[0]),[[{item:1,input:0},{item:0,input:1}],[{item:1,input:0},{item:1,input:1}],[{item:2,input:0},{item:0,input:1}],[{item:2,input:0},{item:1,input:1}],[{item:0,input:0}],[{item:2,input:1}]]);
 const first=await byFields({joinMode:'keepEverything',options:{multipleMatches:'first'}},groups);assert.deepEqual(getItemPairs(first[0]),[[{item:1,input:0},{item:0,input:1}],[{item:2,input:0},{item:0,input:1}],[{item:0,input:0}],[{item:1,input:1}],[{item:2,input:1}]]);
 const left=await byFields({outputDataFrom:'input1'},groups),right=await byFields({outputDataFrom:'input2'},groups);assert.deepEqual(getItemPairs(left[0]),[[{item:1,input:0}],[{item:2,input:0}]]);assert.deepEqual(getItemPairs(right[0]),[[{item:0,input:1}],[{item:1,input:1}]]);
 const nonmatch=await byFields({joinMode:'keepNonMatches'},groups);assert.deepEqual(getItemPairs(nonmatch[0]),[[{item:0,input:0}],[{item:2,input:1}]]);const onlyRight=await byFields({joinMode:'enrichInput2'},[[],groups[1]]);assert.deepEqual(getItemPairs(onlyRight[0]),[[{item:0,input:1}],[{item:1,input:1}],[{item:2,input:1}]]);
});
