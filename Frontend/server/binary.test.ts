import {examplePath} from './paths';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {BINARY_KEY,MAX_BINARY_BYTES,binaryNodeTypes,createBinaryFile,createBinaryFileFromBase64,decodeBinaryFile,getBinary,withBinary,splitBinaryItem,jsonWithoutBinary,binaryMetadata,assertBinaryItems,readBinaryResponse,parseCsv,serializeCsv,executeBinaryNode} from './binary';
import type {FlowNode,Json} from '../lib/workflow';
import {getItemPairs} from './provenance';

const node=(convert:boolean,parameters:Json):FlowNode=>({id:'file',name:'file',type:binaryNodeTypes[convert?0:1],typeVersion:convert?1.1:1.2,position:[0,0],parameters});
const file=(content:string,name='file.txt',property='data',json:Json={})=>withBinary(json,property,createBinaryFile(Buffer.from(content,'utf8'),{fileName:name,mimeType:name.endsWith('.csv')?'text/csv':name.endsWith('.json')?'application/json':'text/plain'}));
const content=(item:Json,property='data')=>decodeBinaryFile(getBinary(item,property)).toString('utf8');

test('file helpers preserve bytes and separate binary metadata from ordinary JSON',()=>{
 const bytes=Buffer.from([0,1,2,255]),binary=createBinaryFile(bytes,{mimeType:'application/octet-stream',fileName:'../경로/파일.bin'}),input=withBinary({customer:'민지'},'attachment',binary);
 assert.equal(binary.fileName,'파일.bin');assert.equal(binary.fileExtension,'bin');assert.equal(binary.fileSize,4);assert.deepEqual(decodeBinaryFile(getBinary(input,'attachment')),bytes);
 assert.deepEqual(jsonWithoutBinary(input),{customer:'민지'});assert.deepEqual(splitBinaryItem(input).json,{customer:'민지'});assert.equal(Object.hasOwn(binaryMetadata(input).attachment,'data'),false);assert.equal(binaryMetadata(input).attachment.fileSize,4);assert.equal(input.customer,'민지');
 const second=withBinary(input,'other',createBinaryFile(Buffer.from('text'),{mimeType:'text/plain'}));assert.equal(Object.keys(splitBinaryItem(second).binary).length,2);assert.equal(Object.keys(splitBinaryItem(input).binary).length,1);
});
test('strict file validation rejects malformed encodings, forged sizes and unsafe metadata',()=>{
 const valid=createBinaryFile(Buffer.from('f'),{mimeType:'text/plain',fileName:'file.txt'});
 for(const change of [{data:'!!!!'},{data:'Zh=='},{data:'Zg'},{fileSize:2},{fileSize:-1},{mimeType:'text/plain\r\nInjected: yes'},{fileName:'..'}])assert.throws(()=>decodeBinaryFile({...valid,...change}));
 assert.throws(()=>splitBinaryItem({[BINARY_KEY]:'not a map'}),/파일 항목/);assert.throws(()=>withBinary({},'__proto__',valid),/항목 이름/);assert.throws(()=>getBinary({},'data'),/파일이 없/);
});
test('base64 input supports canonical, unpadded, whitespace and data URI values without losing bytes',()=>{
 for(const source of ['Zg==','Zg','Z g\n==','data:text/plain;base64,Zg==']){const binary=createBinaryFileFromBase64(source,{fileName:'letter.txt'});assert.equal(decodeBinaryFile(binary).toString(),'f');assert.equal(binary.data,'Zg==');}
 assert.equal(createBinaryFileFromBase64('data:image/png;base64,AQID').mimeType,'image/png');
 for(const source of ['Zh==','A','====','%not-base64%','Zm8==='])assert.throws(()=>createBinaryFileFromBase64(source),/base64/);
});
test('exact 8MiB files are valid while per-file, aggregate and item limits are enforced',()=>{
 const bytes=Buffer.alloc(MAX_BINARY_BYTES,7),binary=createBinaryFile(bytes,{fileName:'max.bin'});assert.equal(binary.fileSize,MAX_BINARY_BYTES);assertBinaryItems([withBinary({},'data',binary)]);assert.equal(decodeBinaryFile(binary).at(-1),7);
 assert.throws(()=>createBinaryFile(Buffer.alloc(MAX_BINARY_BYTES+1)),/8MB/);
 assert.throws(()=>assertBinaryItems([withBinary({},'data',binary),file('x')]),/총 크기/);assert.throws(()=>assertBinaryItems(Array.from({length:5001},()=>({}))),/5,000/);
});
test('CSV parser handles BOM, delimiters, quoted commas, escaped quotes and embedded CRLF',()=>{
 const rows=parseCsv('\uFEFF이름,메모,빈칸\r\n민지,"쉼표, 그리고 ""인용""\r\n다음 줄",\r\n지훈,일반 글,끝\r\n');
 assert.deepEqual(rows,[{이름:'민지',메모:'쉼표, 그리고 "인용"\r\n다음 줄',빈칸:''},{이름:'지훈',메모:'일반 글',빈칸:'끝'}]);
 assert.deepEqual(parseCsv('a;b\n"값;하나";둘',{delimiter:';'}),[{a:'값;하나',b:'둘'}]);assert.deepEqual(parseCsv('a\tb\r1\t2',{delimiter:'\t'}),[{a:'1',b:'2'}]);
});
test('CSV parser supports no header and quoted empty values without inventing a trailing row',()=>{
 assert.deepEqual(parseCsv('민지,001\n지훈,002\n',{headerRow:false}),[{column1:'민지',column2:'001'},{column1:'지훈',column2:'002'}]);
 assert.deepEqual(parseCsv('a\n""\n\n'),[{a:''}]);assert.deepEqual(parseCsv(''),[]);assert.deepEqual(parseCsv('a,b\n'),[]);assert.deepEqual(parseCsv('a,b\n1,2\n'),[{a:'1',b:'2'}]);
});
test('CSV malformed quotes, duplicate headers, dangerous columns and ragged rows are rejected',()=>{
 for(const source of ['a,b\n"open,2','a,b\n"closed"junk,2','a,b\npartial"quote,2','a,a\n1,2',',b\n1,2','a,b\n1,2,3','__proto__,value\na,b','__easyflowBinary,value\na,b'])assert.throws(()=>parseCsv(source));
 assert.throws(()=>parseCsv('a,b',{delimiter:'\n'}),/구분자/);assert.throws(()=>parseCsv(Array.from({length:1001},(_,i)=>'column'+i).join(',')),/1,000/);
});
test('CSV row limit is 5000 data rows, independent of the header',()=>{
 const source='name\n'+Array.from({length:5000},(_,i)=>'row'+i).join('\n');assert.equal(parseCsv(source).length,5000);assert.throws(()=>parseCsv(source+'\nextra'),/5,000/);
});
test('CSV serialization roundtrips content and omits file metadata',()=>{
 const rows=[{name:'민지',memo:'인용 "하나",\n두 줄',id:'001',other:true},{name:'지훈',memo:'끝',id:'002',other:false}],input=rows.map(row=>file('attachment','attachment.txt','data',row));
 const csv=serializeCsv(input,{addBOM:true});assert.ok(csv.startsWith('\uFEFF'));assert.ok(!csv.includes(BINARY_KEY));assert.deepEqual(parseCsv(csv),rows.map(row=>({...row,other:String(row.other)})));
 assert.equal(serializeCsv([{a:1,b:2}],{headerRow:false}),'1,2');assert.equal(serializeCsv([{data:{a:1}}]),'data\r\n"{""a"":1}"');
});
test('Convert to CSV creates a real downloadable file with beginner flat fields',async()=>{
 const out=await executeBinaryNode(node(true,{operation:'csv',binaryPropertyName:'report',fileName:'업무.csv',addBOM:'true',headerRow:'true'}),[{name:'민지',score:92},{name:'지훈',score:68}]);
 const binary=getBinary(out[0][0],'report');assert.equal(binary.fileName,'업무.csv');assert.equal(binary.mimeType,'text/csv');assert.deepEqual(parseCsv(content(out[0][0],'report')),[{name:'민지',score:'92'},{name:'지훈',score:'68'}]);assert.deepEqual(jsonWithoutBinary(out[0][0]),{});
});
test('Convert JSON once/each uses ordinary JSON only and preserves actual JSON types',async()=>{
 const inputs=[file('private attachment','file.txt','data',{name:'민지',score:92}),{name:'지훈',score:68}];
 const once=await executeBinaryNode(node(true,{operation:'toJson',mode:'once',options:{fileName:'records.json',format:true,addBOM:true}}),inputs);
 assert.equal(once[0].length,1);assert.equal(getBinary(once[0][0]).fileName,'records.json');assert.deepEqual(JSON.parse(content(once[0][0]).slice(1)),[{name:'민지',score:92},{name:'지훈',score:68}]);assert.ok(!content(once[0][0]).includes(BINARY_KEY));
 const each=await executeBinaryNode(node(true,{operation:'toJson',mode:'each'}),inputs);assert.equal(each[0].length,2);assert.deepEqual(JSON.parse(content(each[0][1])),{name:'지훈',score:68});
 await assert.rejects(executeBinaryNode(node(true,{operation:'toJson',mode:'invalid'}),[{}]),/만들기/);
});
test('Convert text selects nested input fields and resolves filenames per item',async()=>{
 const source={message:{text:'한국어와 \n새 줄'},fileName:'결과.txt'};const out=await executeBinaryNode(node(true,{operation:'toText',sourceProperty:'message.text',fileName:'={{ $json.fileName }}'}),[source],{resolveValue:(value,item)=>value==='={{ $json.fileName }}'?item.fileName:value});
 assert.equal(content(out[0][0]),source.message.text);assert.equal(getBinary(out[0][0]).fileName,'결과.txt');
 await assert.rejects(executeBinaryNode(node(true,{operation:'toText',sourceProperty:'score'}),[{score:92}]),/글자 값/);await assert.rejects(executeBinaryNode(node(true,{operation:'toText',sourceProperty:'/etc/passwd'}),[{}]),/입력 항목이 없/);
});
test('binary conversion roundtrip supports arbitrary bytes and both base64 operation spellings',async()=>{
 const original=Buffer.from([0,255,1,2]),converted=await executeBinaryNode(node(true,{operation:'toBinary',sourceProperty:'payload',options:{mimeType:'image/png',fileName:'image.png'}}),[{payload:original.toString('base64')}]);
 assert.deepEqual(decodeBinaryFile(getBinary(converted[0][0])),original);assert.equal(getBinary(converted[0][0]).mimeType,'image/png');
 for(const operation of ['binaryToPropery','binaryToProperty']){const extracted=await executeBinaryNode(node(false,{operation,destinationKey:'result.payload',keepSource:'none'}),converted[0]);assert.deepEqual(extracted,[[{result:{payload:original.toString('base64')}}]]);}
});
test('Extract text honors UTF-8/BOM and source retention without mutating input',async()=>{
 const input=file('\uFEFF본문','note.txt','attachment',{customer:'민지',result:{prior:true}});
 const out=await executeBinaryNode(node(false,{operation:'text',binaryPropertyName:'attachment',destinationKey:'result.content',options:{keepSource:'both'}}),[input]);
 assert.equal(out[0][0].customer,'민지');assert.deepEqual(out[0][0].result,{prior:true,content:'본문'});assert.ok(out[0][0][BINARY_KEY]);assert.deepEqual(input.result,{prior:true});
 const preserved=await executeBinaryNode(node(false,{operation:'text',binaryPropertyName:'attachment',stripBOM:'false',keepSource:'none'}),[input]);assert.deepEqual(preserved,[[{data:'\uFEFF본문'}]]);
 const invalid=withBinary({},'data',createBinaryFile(Buffer.from([0xff,0xfe])));await assert.rejects(executeBinaryNode(node(false,{operation:'text'}),[invalid]),/UTF-8/);
});
test('Extract JSON preserves arrays/objects and rejects malformed files',async()=>{
 const original=[{name:'민지',score:92},{name:'지훈',score:68}],out=await executeBinaryNode(node(false,{operation:'fromJson',destinationKey:'records',keepSource:'none'}),[file('\uFEFF'+JSON.stringify(original),'data.json')]);assert.deepEqual(out,[[{records:original}]]);
 await assert.rejects(executeBinaryNode(node(false,{operation:'fromJson'}),[file('{broken','data.json')]),/올바른 JSON/);assert.deepEqual(await executeBinaryNode(node(false,{operation:'fromJson',keepSource:'none'}),[file('','data.json')]),[[{data:{}}]]);
});
test('Extract CSV outputs each row and controls retained input data',async()=>{
 const input=file('\uFEFFname,score\r\n민지,92\r\n지훈,68','data.csv','data',{batch:'A'}),out=await executeBinaryNode(node(false,{operation:'csv',headerRow:'true',keepSource:'json'}),[input]);assert.deepEqual(out,[[{batch:'A',name:'민지',score:'92'},{batch:'A',name:'지훈',score:'68'}]]);
 const noHeader=await executeBinaryNode(node(false,{operation:'csv',headerRow:'false',keepSource:'none'}),[file('민지,92','data.csv')]);assert.deepEqual(noHeader,[[{column1:'민지',column2:'92'}]]);
});
test('file operations reject unsupported formats, unsafe destinations and incompatible encodings',async()=>{
 await assert.rejects(executeBinaryNode(node(true,{operation:'docx'}),[{}]),/지원하는 파일/);await assert.rejects(executeBinaryNode(node(false,{operation:'ods'}),[file('text')]),/지원하는 파일/);
 await assert.rejects(executeBinaryNode(node(false,{operation:'text',encoding:'euc-kr'}),[file('text')]),/UTF-8/);await assert.rejects(executeBinaryNode(node(false,{operation:'text',destinationKey:'__proto__.polluted'}),[file('text')]),/항목 이름/);assert.equal(({} as Json).polluted,undefined);
 await assert.rejects(executeBinaryNode(node(true,{operation:'toText',sourceProperty:'__easyflowBinary.data'}),[file('text')]),/항목 이름/);assert.deepEqual(await executeBinaryNode(node(true,{operation:'csv'}),[]),[[]]);
 const controller=new AbortController();controller.abort();await assert.rejects(executeBinaryNode(node(true,{operation:'csv'}),[{}],{signal:controller.signal}),/중지/);
});
test('multiple files cannot collectively produce more than 5000 CSV output items',async()=>{
 const half='name\n'+Array.from({length:2501},(_,i)=>'row'+i).join('\n');await assert.rejects(executeBinaryNode(node(false,{operation:'csv'}),[file(half,'a.csv'),file(half,'b.csv')]),/5,000/);
});
test('HTTP file response helper streams bytes, checks HTTP errors and cancels oversized bodies',async()=>{
 const result=await readBinaryResponse(new Response(Buffer.from([0,255,7]),{headers:{'content-type':'image/png'}}),{fileName:'image.png'});assert.deepEqual(decodeBinaryFile(result),Buffer.from([0,255,7]));assert.equal(result.mimeType,'image/png');
 await assert.rejects(readBinaryResponse(new Response('failure',{status:403})),/HTTP 403/);
 let canceled=false,chunks=0;const stream=new ReadableStream<Uint8Array>({pull(controller){chunks++;controller.enqueue(new Uint8Array(1024*1024));if(chunks===20)controller.close();},cancel(){canceled=true;}});await assert.rejects(readBinaryResponse(new Response(stream)),/8MB/);assert.equal(canceled,true);assert.ok(chunks<20);
});
test('HTTP file read can be canceled while awaiting bytes and masks stream faults',async()=>{
 const controller=new AbortController();let canceled=false;const stream=new ReadableStream<Uint8Array>({cancel(){canceled=true;}});
 const pending=readBinaryResponse(new Response(stream),{},controller.signal);controller.abort();await assert.rejects(pending,/중지/);assert.equal(canceled,true);
 const fault=new ReadableStream({start(c){c.error(new Error('private upstream file content'));}});await assert.rejects(readBinaryResponse(new Response(fault)),error=>/끝까지 읽지 못/.test(String(error))&&!String(error).includes('private upstream'));
});
test('binary catalog declares only the thirteen implemented operations',()=>{
 const catalog=JSON.parse(readFileSync(new URL('./binary-catalog.json',import.meta.url),'utf8')).catalog;assert.deepEqual(catalog.map((c:any)=>c.n8nType),binaryNodeTypes);assert.equal(catalog[0].typeVersion,1.1);assert.equal(catalog[1].typeVersion,1.2);
 assert.deepEqual(catalog[0].fields.find((f:any)=>f.key==='operation').options.map((o:any)=>o.value),['csv','toJson','toText','toBinary','xlsx','html','rtf']);assert.deepEqual(catalog[1].fields.find((f:any)=>f.key==='operation').options.map((o:any)=>o.value),['csv','fromJson','text','binaryToPropery','xlsx','pdf']);
});
test('Convert/Extract XLSX nodes produce actual Excel files and rows with flat beginner options',async()=>{
 const rows=[{name:'민지',score:92,passed:true,id:'001'},{name:'지훈',score:68,passed:false,id:'002'}],out=await executeBinaryNode(node(true,{operation:'xlsx',binaryPropertyName:'report',fileName:'업무결과.xlsx',sheetName:'업무',headerRow:'true',compression:'true'}),rows);
 const binary=getBinary(out[0][0],'report');assert.equal(binary.fileName,'업무결과.xlsx');assert.equal(binary.mimeType,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');assert.deepEqual(jsonWithoutBinary(out[0][0]),{});
 assert.deepEqual(getItemPairs(out[0]),[[{item:0},{item:1}]]);
 const extracted=await executeBinaryNode(node(false,{operation:'xlsx',binaryPropertyName:'report',sheetName:'업무',resultTypes:'typed',keepSource:'none'}),out[0]);assert.deepEqual(extracted,[rows]);assert.deepEqual(getItemPairs(extracted[0]),[[{item:0}],[{item:0}]]);
 const strings=await executeBinaryNode(node(false,{operation:'xlsx',binaryPropertyName:'report',range:'B1:C3',resultTypes:'strings',keepSource:'none'}),[out[0][0],out[0][0]]);assert.deepEqual(strings,[[{score:'92',passed:'true'},{score:'68',passed:'false'},{score:'92',passed:'true'},{score:'68',passed:'false'}]]);assert.deepEqual(getItemPairs(strings[0]),[[{item:0}],[{item:0}],[{item:1}],[{item:1}]]);
});
test('Single-file CSV/HTML/RTF/JSON conversion records every source item without changing file contents',async()=>{
 const input=[{name:'A',score:1},{name:'B',score:2},{name:'C',score:3}];
 for(const operation of ['csv','html','rtf','toJson']){const output=await executeBinaryNode(node(true,{operation}),input);assert.deepEqual(getItemPairs(output[0]),[[{item:0},{item:1},{item:2}]],operation);assert.deepEqual(Object.keys(output[0]),['0']);assert.deepEqual(Object.keys(output[0][0]),[BINARY_KEY]);assert.ok(!content(output[0][0]).includes('pairedItem'));}
 const empty=await executeBinaryNode(node(true,{operation:'csv'}),[]);assert.deepEqual(getItemPairs(empty[0]),[]);
});
test('Per-item JSON/text/base64 conversion and text/JSON/base64 extraction preserve original indexes',async()=>{
 const input=[{payload:'QQ==',text:'A'},{payload:'Qg==',text:'B'}];
 for(const parameters of [{operation:'toJson',mode:'each'},{operation:'toText',sourceProperty:'text'},{operation:'toBinary',sourceProperty:'payload'}]){const output=await executeBinaryNode(node(true,parameters),input);assert.deepEqual(getItemPairs(output[0]),[[{item:0}],[{item:1}]]);}
 for(const operation of ['text','fromJson','binaryToPropery']){const output=await executeBinaryNode(node(false,{operation,keepSource:'none'}),[file('{"name":"A"}','a.json'),file('{"name":"B"}','b.json')]);assert.deepEqual(getItemPairs(output[0]),[[{item:0}],[{item:1}]]);}
});
test('Rows extracted from multiple CSV files point to their own file item after empty files',async()=>{
 const inputs=[file('name\n','empty.csv'),file('name\nA\nB','ab.csv'),file('name\nC','c.csv')],output=await executeBinaryNode(node(false,{operation:'csv',keepSource:'none'}),inputs);assert.deepEqual(output,[[{name:'A'},{name:'B'},{name:'C'}]]);assert.deepEqual(getItemPairs(output[0]),[[{item:1}],[{item:1}],[{item:2}]]);assert.equal(JSON.stringify(output),'[[{"name":"A"},{"name":"B"},{"name":"C"}]]');
});
test('Convert HTML makes escaped actual table documents instead of active HTML from input',async()=>{
 const out=await executeBinaryNode(node(true,{operation:'html',fileName:'보고서.html',headerRow:'true'}),[{'<제목>':'<script>alert("x")</script>',data:{a:1}}]);const source=content(out[0][0]);assert.equal(getBinary(out[0][0]).fileName,'보고서.html');assert.equal(getBinary(out[0][0]).mimeType,'text/html; charset=utf-8');assert.match(source,/<!doctype html>/);assert.match(source,/<table>/);assert.match(source,/&lt;제목&gt;/);assert.match(source,/&lt;script&gt;alert\(&quot;x&quot;\)&lt;\/script&gt;/);assert.ok(!source.includes('<script>'));assert.ok(source.includes('default-src \'none\''));assert.ok(!source.includes(BINARY_KEY));
});
test('Convert RTF makes Unicode table documents with escaped commands and multiline cells',async()=>{
 const out=await executeBinaryNode(node(true,{operation:'rtf',headerRow:'false'}),[{name:'민지',memo:'{\\field 악성명령}\n새 줄'}]),source=content(out[0][0]);assert.equal(getBinary(out[0][0]).mimeType,'application/rtf');assert.match(source,/^\{\\rtf1/);assert.match(source,/\\trowd/);assert.match(source,/\\cellx/);assert.match(source,/\\u-\d+\?/);assert.ok(source.includes('\\{\\\\field '));assert.ok(source.includes('\\line '));assert.ok(!source.includes('\\intbl name\\cell'));assert.ok(source.endsWith('}'));
});
test('new file operations obey existing aggregate limits and document cancellation',async()=>{
 await assert.rejects(executeBinaryNode(node(true,{operation:'html'}),[{a:'x'.repeat(MAX_BINARY_BYTES)}]),/8MB/);
 const controller=new AbortController();const pending=executeBinaryNode(node(true,{operation:'xlsx'}),[{name:'민지'}],{signal:controller.signal});controller.abort();await assert.rejects(pending,/중지/);
 await assert.rejects(executeBinaryNode(node(false,{operation:'pdf'}),[file('not PDF')]),/PDF 파일 형식/);
});
test('Extract PDF node reads the shipped real fixture, page options and retained source data',async()=>{
 const bytes=readFileSync(examplePath('초보자_PDF_예제.pdf')),input=withBinary({source:'실제 예제'},'data',createBinaryFile(bytes,{mimeType:'application/pdf',fileName:'초보자_PDF_예제.pdf'}));
 const pages=await executeBinaryNode(node(false,{operation:'pdf',joinPages:'false',keepSource:'both'}),[input]);assert.equal(pages[0][0].numpages,2);assert.equal(pages[0][0].numrender,2);assert.deepEqual(pages[0][0].text,['Easyflow report page one','Second page contains actual text']);assert.equal(pages[0][0].source,'실제 예제');assert.deepEqual(decodeBinaryFile(getBinary(pages[0][0])),bytes);assert.deepEqual(getItemPairs(pages[0]),[[{item:0}]]);
 const first=await executeBinaryNode(node(false,{operation:'pdf',maxPages:1,joinPages:'true',keepSource:'none'}),[input,input]);assert.deepEqual(first,[[{numpages:2,numrender:1,text:'Easyflow report page one'},{numpages:2,numrender:1,text:'Easyflow report page one'}]]);assert.deepEqual(getItemPairs(first[0]),[[{item:0}],[{item:1}]]);await assert.rejects(executeBinaryNode(node(false,{operation:'pdf'}),[input],{timeoutMs:1}),/시간이 초과/);
});
