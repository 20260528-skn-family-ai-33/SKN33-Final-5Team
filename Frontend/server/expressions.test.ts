import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveExpression,type ExpressionContext} from './expressions';
const read=(source:string,item:any={},context:ExpressionContext={})=>resolveExpression(`={{ ${source} }}`,item,context);
test('nested, computed and optional reads preserve types with arithmetic and lazy branches',()=>{
 const data={고객:{이름:'민지'},records:[{id:7},{id:9}],score:92};
 assert.equal(read('$json.고객.이름',data),'민지');assert.equal(read('$json.records[$json.records.length - 1].id',data),9);
 assert.equal(read('$json.missing?.details?.[0] ?? "없음"',data),'없음');assert.equal(read('$json.score >= 80 ? "합격" : "보류"',data),'합격');
 assert.equal(read('1 + 2 * 3 - 4 / 2'),5);assert.equal(read('false && Number("oops")'),false);assert.equal(read('true || Number("oops")'),true);
 assert.deepEqual(read('{name: $json.고객.이름, values: [1, 2, $json.score]}',data),{name:'민지',values:[1,2,92]});
});
test('exact expressions return data and interpolation returns escaped-safe text',()=>{
 assert.equal(resolveExpression('{{ $json.amount + 2 }}',{amount:3}),5);
 assert.equal(resolveExpression('고객 {{ $json.name }} / {{ $json.missing }}',{name:'"민지"'}),'고객 "민지" / ');
 assert.equal(resolveExpression('값 {{ $json.payload }}',{payload:{a:1}}),'값 {"a":1}');
 assert.equal(resolveExpression('ordinary = text',{}),'ordinary = text');
 assert.equal(resolveExpression('시작 {{ "a }} b" }} 끝',{}),'시작 a }} b 끝');
 assert.equal(resolveExpression("{{ 'a }} b' }}",{}),'a }} b');
 assert.equal(resolveExpression('결과 {{ {a: "}}", b: 1} }} 끝',{}),'결과 {"a":"}}","b":1} 끝');
 assert.equal(resolveExpression('=https://example.test/{{ $json.id }}',{id:4}),'https://example.test/4');
 assert.throws(()=>resolveExpression('실수 {{ $json.id',{}));
});
test('optional chaining short-circuits only its chain and parentheses preserve JS boundaries',()=>{
 assert.throws(()=>read('$json.missing.child'),/없는 값/);
 assert.equal(read('$json.missing?.child.name ?? "none"'),'none');assert.equal(read('$json.missing?.trim().toUpperCase()'),undefined);
 assert.throws(()=>read('($json.missing?.child).name'),/없는 값/);
 assert.throws(()=>read('$json.present?.child.name',{present:{}}),/없는 값/);
 assert.equal(read('$json.present?.child?.name',{present:{}}),undefined);
});
test('current input uses canonical items, metadata and zero-based item position',()=>{
 const items=[{n:2},{n:5}],context:ExpressionContext={input:items,itemIndex:1,runIndex:3,execution:{id:'execution-id',mode:'production'},params:{operation:'run'}};
 assert.equal(read('$input.item.json.n',items[1],context),5);assert.equal(read('$input.first().json.n',items[1],context),2);assert.equal(read('$input.last().json.n',items[1],context),5);
 assert.deepEqual(read('$input.all()',items[1],context),[{json:{n:2}},{json:{n:5}}]);
 assert.equal(read('$itemIndex',items[1],context),1);assert.equal(read('$runIndex',items[1],context),3);assert.equal(read('$execution.id',items[1],context),'execution-id');assert.equal(read('$execution.mode',items[1],context),'production');
 assert.equal(read('$parameter.operation',items[1],context),'run');assert.equal(read('$input.params.operation',items[1],context),'run');
 assert.throws(()=>read('$input.first(0)',{},context),/인수/);assert.throws(()=>read('$input.last(0)',{},context),/인수/);
});
test('canonical binary references expose file metadata while JSON remains separate and raw bytes are explicit errors',()=>{
 const item={name:'record',__easyflowBinary:{data:{data:'x'.repeat(4_000_001),fileName:'report.csv',mimeType:'text/csv',fileSize:12,fileExtension:'csv'}}},context:ExpressionContext={input:[item],node:()=>({items:[item],isExecuted:true})};
 assert.deepEqual(read('$json',item,context),{name:'record'});assert.equal(read('$binary.data.fileName',item,context),'report.csv');assert.equal(read('$input.item.binary.data.fileSize',item,context),12);
 assert.equal(read("$('file').first().binary.data.mimeType",item,context),'text/csv');assert.equal(read('$node["file"].binary.data.fileExtension',item,context),'csv');
 assert.deepEqual(read('$input.all()',item,context),[{json:{name:'record'},binary:{data:{fileName:'report.csv',mimeType:'text/csv',fileExtension:'csv',fileSize:12}}}]);
 assert.throws(()=>read('$binary.data.data',item,context),/파일 원본/);assert.equal(read('$json.__easyflowBinary',item,context),undefined);
});
test('explicit previous node branches and runs are selected by trusted execution context',()=>{
 const queries:any[]=[];const context:ExpressionContext={node:(name,output,run)=>{queries.push([name,output,run]);return {items:[{id:1},{id:2}],isExecuted:name==='이전 단계'};}};
 assert.equal(read("$('이전 단계').first().json.id",{},context),1);assert.equal(read("$('이전 단계').last(1,3).json.id",{},context),2);
 assert.deepEqual(read("$('이전 단계').all(0,-1)",{},context),[{json:{id:1}},{json:{id:2}}]);
 assert.deepEqual(queries,[['이전 단계',undefined,undefined],['이전 단계',1,3],['이전 단계',0,-1]]);
 assert.equal(read("$('미실행').isExecuted",{},context),false);assert.throws(()=>read("$('미실행').first()",{},context),/아직 실행되지/);
 assert.equal(read("$('미실행').isExecuted ? $('미실행').first().json.id : 0",{},context),0);
 assert.throws(()=>read("$('이전 단계').all(-2)",{},context));
});
test('legacy node uses actual positional semantics; modern item never guesses linking',()=>{
 const context:ExpressionContext={itemIndex:1,node:()=>({items:[{original:'first'},{original:'second'}],isExecuted:true})};
 assert.equal(read('$node["이전"].json.original',{},context),'second');
 assert.throws(()=>read("$('이전').item.json.original",{},context),/first\(\).*all\(\)/);
 assert.throws(()=>read('$node["이전"].json.original',{}, {...context,itemIndex:2}),/항목 번호/);
});
test('modern item and itemMatching forward the actual input index to causal lookup',()=>{
 const queried:any[]=[];const context:ExpressionContext={input:[{n:1},{n:2}],itemIndex:1,linkedItem:(name,index)=>{queried.push([name,index]);return {id:index,__easyflowBinary:{data:{fileName:'linked.csv'}}};}};
 assert.equal(read("$('original').item.json.id",{},context),1);assert.equal(read("$('original').itemMatching(0).json.id",{},context),0);assert.equal(read("$('original').item.binary.data.fileName",{},context),'linked.csv');assert.deepEqual(queried,[['original',1],['original',0],['original',1]]);
 assert.throws(()=>read("$('original').itemMatching(-1)",{},context));
});
test('safe string, list, numeric and JSON methods work without exposing callables',()=>{
 assert.equal(read('$json.name.trim().toUpperCase().replace("HELLO", "HI")',{name:' hello world '}),'HI WORLD');
 assert.equal(read('"A-B-C".split("-").slice(1).join(",")'),'B,C');assert.equal(read('[1,2,3].at(-1)'),3);
 assert.equal(read('"welcome".includes("come") && "abc".startsWith("a")'),true);assert.equal(read('"a".padStart(3, "0")'),'00a');
 assert.equal(read('Math.round(Number("12.6"))'),13);assert.equal(read('Math.max(2,4,3)'),4);
 assert.deepEqual(read('JSON.parse("{\\"value\\":2}")'),{value:2});assert.equal(read('JSON.stringify({a:1})'),'{"a":1}');
 assert.equal(read('"text".toString'),undefined);assert.throws(()=>read('Math.random()'),/표현식/);
});
test('host code, dangerous properties, data functions and getters never execute',()=>{
 for(const source of ['process.env','globalThis','eval("1")','Function("return 1")()','new Date()','(()=>1)()','$json.a = 3','$json.constructor','"a"["con" + "structor"]','$json.__proto__','JSON.parse("{\\"__proto__\\":{}}")','({constructor: 1})'])assert.throws(()=>read(source,{a:1}));
 let calls=0;assert.throws(()=>read('$json.function()', {function:()=>calls++}));assert.equal(calls,0);
 assert.throws(()=>read('$json == 1',{valueOf(){calls++;return 1;}}));assert.equal(calls,0);
 const getter=Object.defineProperty({},'secret',{enumerable:true,get(){calls++;return 'secret';}});assert.throws(()=>read('$json.secret',getter));assert.equal(calls,0);
 const array=Object.defineProperty([],'0',{enumerable:true,get(){calls++;return 'secret';}});assert.throws(()=>read('$json.items.join(",")',{items:array}));assert.equal(calls,0);
});
test('limits reject expensive depth, function count, list size, output amplification and nonfinite math',()=>{
 assert.throws(()=>read('('.repeat(70)+'1'+')'.repeat(70)));
 assert.throws(()=>read('"a"'+'.trim()'.repeat(105)));
 assert.throws(()=>read('"x".repeat(4000001)'));assert.throws(()=>read('"x".padEnd(4000001)'));
 assert.throws(()=>read('$json.large.split(",")',{large:'a,'.repeat(6000)}));assert.equal(read('$json.large.split(",",1)[0]',{large:'a,'.repeat(6000)}),'a');
 assert.throws(()=>read('"aaaa".replaceAll("a", $json.large)',{large:'x'.repeat(1_100_000)}));
 assert.throws(()=>read('1 / 0'),/유한한/);assert.throws(()=>read('Number("abc")'),/유한한/);
 assert.throws(()=>read('$json.items',{items:Array.from({length:5001},()=>({x:1}))}),/5,000/);
 assert.throws(()=>read('"가".repeat(1400000)'),/4MB/);assert.throws(()=>resolveExpression('가'.repeat(1_400_000),{}),/4MB/);
});
test('empty or unavailable input and node results are clear errors',()=>{
 assert.throws(()=>read('$input.item',{}, {input:[]}),/입력 항목/);assert.throws(()=>read('$execution.id'),/실행 정보/);
 assert.throws(()=>read("$('x').first()"),/실행 안/);
 const context:ExpressionContext={node:()=>({items:[],isExecuted:true})};assert.deepEqual(read("$('x').all()",{},context),[]);assert.throws(()=>read("$('x').last()",{},context),/출력 항목/);
});
