import test from 'node:test';
import assert from 'node:assert/strict';
import type {IncomingMessage,IncomingHttpHeaders} from 'node:http';
import {Readable} from 'node:stream';
import {gzipSync,deflateSync} from 'node:zlib';
import type {FlowNode,Flow} from '../lib/workflow';
import {decodeBinaryFile,MAX_BINARY_BYTES} from './binary';
import {validateWebhookConfiguration} from './webhook-auth';
import {readWebhookRequest,validateWebhookRequestOptions,WebhookRequestError,WEBHOOK_JSON_BYTES,WEBHOOK_MULTIPART_BYTES,WEBHOOK_XML_MAX_DEPTH,WEBHOOK_XML_MAX_NODES} from './webhook-request';

const node=(options:Record<string,unknown>={},typeVersion=2.1):FlowNode=>({id:'hook',name:'요청',type:'n8n-nodes-base.webhook',typeVersion,position:[0,0],parameters:{httpMethod:'POST',path:'upload',authentication:'none',options}});
function request(bytes:Buffer,headers:IncomingHttpHeaders={},chunkSize=65537):IncomingMessage {
 const chunks:Buffer[]=[];for(let offset=0;offset<bytes.length;offset+=chunkSize)chunks.push(bytes.subarray(offset,offset+chunkSize));return Object.assign(Readable.from(chunks),{headers,method:'POST',complete:false}) as unknown as IncomingMessage;
}
function multipart(parts:{name:string;value:Buffer|string;fileName?:string;mimeType?:string;encoding?:string}[],boundary='easyflow-boundary'):Buffer {
 const chunks:Buffer[]=[];for(const part of parts){let header=`--${boundary}\r\nContent-Disposition: form-data; name="${part.name}"`;if(part.fileName!==undefined)header+=`; filename="${part.fileName}"`;header+='\r\n';if(part.mimeType)header+=`Content-Type: ${part.mimeType}\r\n`;if(part.encoding)header+=`Content-Transfer-Encoding: ${part.encoding}\r\n`;chunks.push(Buffer.from(header+'\r\n','utf8'),Buffer.isBuffer(part.value)?part.value:Buffer.from(part.value,'utf8'),Buffer.from('\r\n'));}chunks.push(Buffer.from(`--${boundary}--\r\n`));return Buffer.concat(chunks);
}
const multipartHeaders={'content-type':'multipart/form-data; boundary=easyflow-boundary'};
const fails=(status:number,pattern:RegExp)=>((error:unknown)=>error instanceof WebhookRequestError&&error.status===status&&pattern.test(error.message));

test('raw binary preserves every byte and parses safe UTF-8 content-disposition filenames',async()=>{
 const bytes=Buffer.from([0,255,128,13,10,0,1]),input=await readWebhookRequest(request(bytes,{'content-type':'image/png','content-disposition':"attachment; filename=old.png; filename*=UTF-8''%ED%95%9C%EA%B8%80%20%EC%82%AC%EC%A7%84.png"},1),node({binaryPropertyName:'upload'}));assert.deepEqual(input.body,{});assert.equal(input.binary?.upload.fileName,'한글 사진.png');assert.equal(input.binary?.upload.mimeType,'image/png');assert.deepEqual(decodeBinaryFile(input.binary?.upload),bytes);
 const safe=await readWebhookRequest(request(bytes,{'content-type':'application/octet-stream','content-disposition':'attachment; filename="../../가\\\\사진.bin"'}),node());assert.equal(safe.binary?.data.fileName,'사진.bin');assert.deepEqual(decodeBinaryFile(safe.binary?.data),bytes);
 const literal=await readWebhookRequest(request(bytes,{'content-type':'application/octet-stream','content-disposition':'attachment; filename="literal%2e.csv"'}),node());assert.equal(literal.binary?.data.fileName,'literal%2e.csv');
 await assert.rejects(readWebhookRequest(request(bytes,{'content-type':'image/png','content-disposition':"attachment; filename*=UTF-8''%XX"}),node()),fails(400,/파일 이름/));
});
test('native automatic binary depends on MIME and version while explicit binary takes precedence over all parsing',async()=>{
 for(const mime of ['text/csv','text/html','application/pdf','application/octet-stream',undefined]){const bytes=Buffer.from('a,b\n1,2'),result=await readWebhookRequest(request(bytes,mime?{'content-type':mime}:{}),node());assert.deepEqual(decodeBinaryFile(result.binary?.data),bytes);assert.deepEqual(result.body,{});}
 const legacy=await readWebhookRequest(request(Buffer.from('a,b'),{'content-type':'text/csv'}),node({},1));assert.deepEqual(legacy,{body:{}});
 const bytes=multipart([{name:'title',value:'본문'},{name:'file',value:'x',fileName:'x.txt',mimeType:'text/plain'}]),forced=await readWebhookRequest(request(bytes,multipartHeaders),node({binaryData:true,rawBody:true,binaryPropertyName:'whole'},1));assert.deepEqual(forced.body,{});assert.deepEqual(decodeBinaryFile(forced.binary?.whole),bytes);assert.deepEqual(Object.keys(forced.binary||{}),['whole']);
 const empty=await readWebhookRequest(request(Buffer.alloc(0),{'content-type':'application/octet-stream'}),node());assert.deepEqual(empty,{body:{}});
});
test('JSON, text and URLencoded body parsing keep ordinary token fields and rawBody exact octets in fixed data',async()=>{
 const cases:[string,Buffer,unknown][]=[['Application/JSON; charset=utf-8',Buffer.from('\uFEFF {"token":"customer token","name":"민지"}'),{token:'customer token',name:'민지'}],['application/example+json',Buffer.from('[1,true,null]'),[1,true,null]],['text/plain',Buffer.from('\uFEFF 한글\r\n'), '\uFEFF 한글\r\n'],['application/x-www-form-urlencoded',Buffer.from('token=ordinary&tag=a&tag=b&encoded=%26&nested%5Bx%5D=3'),{token:'ordinary',tag:['a','b'],encoded:'&','nested[x]':'3'}]];
 for(const [mime,bytes,expected] of cases){const result=await readWebhookRequest(request(bytes,{'content-type':mime},1),node({rawBody:true,binaryPropertyName:'unused'}));assert.deepEqual(result.body,expected);assert.deepEqual(Object.keys(result.binary||{}),['data']);assert.deepEqual(decodeBinaryFile(result.binary?.data),bytes);}
 const empty=await readWebhookRequest(request(Buffer.alloc(0),{'content-type':'application/json'}),node({rawBody:true}));assert.deepEqual(empty.body,{});assert.equal(empty.binary?.data.fileSize,0);
 await assert.rejects(readWebhookRequest(request(Buffer.from('{bad'),{'content-type':'application/json'}),node()),fails(400,/JSON/));
 await assert.rejects(readWebhookRequest(request(Buffer.from([0xff]),{'content-type':'text/plain'}),node()),fails(400,/UTF-8/));
});
test('XML parser follows native lowercased tags, trimmed normalized text, repeated arrays and attributes',async()=>{
 const bytes=Buffer.from('<ROOT ID="A"><Name>  민지   김 </Name><Row>1</Row><Row>2</Row><Empty/><Text><![CDATA[<& raw>]]></Text></ROOT>'),result=await readWebhookRequest(request(bytes,{'content-type':'application/vendor+xml'},3),node({rawBody:true}));assert.deepEqual(result.body,{root:{$:{ID:'A'},name:'민지 김',row:['1','2'],empty:'',text:'<& raw>'}});assert.deepEqual(decodeBinaryFile(result.binary?.data),bytes);
 for(const text of ['<!DOCTYPE root SYSTEM "file:///etc/passwd"><root/>','<!DOCTYPE root [<!ENTITY x "boom">]><root>&x;</root>','<root><__proto__>value</__proto__></root>','<root constructor="value"/>','<Constructor/>','<root><broken></root>'])await assert.rejects(readWebhookRequest(request(Buffer.from(text),{'content-type':'application/xml'}),node()),fails(400,/DOCTYPE|prototype|XML/));assert.equal(({} as any).value,undefined);
});
test('XML depth and node work are bounded without hanging async parse promises',async()=>{
 const deep='<x>'.repeat(WEBHOOK_XML_MAX_DEPTH+1)+'</x>'.repeat(WEBHOOK_XML_MAX_DEPTH+1);await assert.rejects(readWebhookRequest(request(Buffer.from(deep),{'content-type':'text/xml'}),node()),fails(413,/깊이/));
 const many='<root>'+'<x/>'.repeat(WEBHOOK_XML_MAX_NODES)+'</root>';await assert.rejects(readWebhookRequest(request(Buffer.from(many),{'content-type':'application/xml'}),node()),fails(413,/50,000/));
});
test('gzip and deflate parsed payloads keep decompressed rawBody while automatic and forced raw bytes remain compressed',async()=>{
 for(const [compression,compress] of [['gzip',gzipSync],['deflate',deflateSync]] as const){const plain=Buffer.from('{"name":"압축 요청","count":3}'),bytes=compress(plain),result=await readWebhookRequest(request(bytes,{'content-type':'application/json','content-encoding':compression},5),node({rawBody:true}));assert.deepEqual(result.body,{name:'압축 요청',count:3});assert.deepEqual(decodeBinaryFile(result.binary?.data),plain);
  for(const options of [{binaryData:true},{}]){const raw=await readWebhookRequest(request(bytes,{'content-type':'application/octet-stream','content-encoding':compression}),node(options));assert.deepEqual(decodeBinaryFile(raw.binary?.data),bytes);}
 }
 await assert.rejects(readWebhookRequest(request(Buffer.from('bad compression'),{'content-type':'application/json','content-encoding':'gzip'}),node()),fails(400,/압축/));
 await assert.rejects(readWebhookRequest(request(Buffer.from('{}'),{'content-type':'application/json','content-encoding':'br'}),node()),fails(415,/압축/));
 await assert.rejects(readWebhookRequest(request(multipart([]),{...multipartHeaders,'content-encoding':'gzip'}),node()),fails(415,/multipart/));
 const bomb=gzipSync(Buffer.from('x'.repeat(WEBHOOK_JSON_BYTES+1)));await assert.rejects(readWebhookRequest(request(bomb,{'content-type':'text/plain','content-encoding':'gzip'}),node()),fails(413,/4MB/));
});
test('multipart keeps duplicate plain strings flat and creates byte-identical files with UTF-8 names',async()=>{
 const file=Buffer.from([0,128,255,13,10]),parts=[{name:'title',value:'한글 제목'},{name:'tag',value:'a'},{name:'tag',value:'b'},{name:'token',value:'ordinary'},{name:'nested[x]',value:'flat'},{name:'attachment',value:file,fileName:'한글 문서.bin',mimeType:'application/octet-stream'}],result=await readWebhookRequest(request(multipart(parts),multipartHeaders,1),node({rawBody:true}));assert.deepEqual(result.body,{title:'한글 제목',tag:['a','b'],token:'ordinary','nested[x]':'flat'});assert.deepEqual(Object.keys(result.binary||{}),['attachment']);assert.equal(result.binary?.attachment.fileName,'한글 문서.bin');assert.equal(result.binary?.attachment.fileSize,file.length);assert.deepEqual(decodeBinaryFile(result.binary?.attachment),file);
});
test('multipart naming handles repeated groups, [] suffix, empty names, prefixes, blank inputs and real empty files',async()=>{
 const parts=[{name:'same[]',value:'A',fileName:'a.txt',mimeType:'text/plain'},{name:'other',value:'Z',fileName:'z.txt',mimeType:'text/plain'},{name:'same[]',value:'B',fileName:'b.txt',mimeType:'text/plain'},{name:'',value:'C',fileName:'c.txt',mimeType:'text/plain'},{name:'single[]',value:'D',fileName:'d.txt',mimeType:'text/plain'},{name:'blank',value:'',fileName:'',mimeType:'application/octet-stream'},{name:'realEmpty',value:'',fileName:'empty.txt',mimeType:'text/plain'}],body=multipart(parts);
 const result=await readWebhookRequest(request(body,multipartHeaders),node());assert.deepEqual(Object.keys(result.binary||{}),['same0','same1','other','data3','single','realEmpty']);assert.equal(result.binary?.realEmpty.fileSize,0);assert.equal(result.binary?.realEmpty.fileName,'empty.txt');assert.deepEqual(result.body,{});
 const prefixed=await readWebhookRequest(request(body,multipartHeaders),node({binaryPropertyName:'upload'}));assert.deepEqual(Object.keys(prefixed.binary||{}),Array.from({length:6},(_,i)=>'upload'+i));assert.equal(decodeBinaryFile(prefixed.binary?.upload1).toString(),'B');
 const collision=multipart([{name:'same',value:'A',fileName:'a.txt',mimeType:'text/plain'},{name:'same',value:'B',fileName:'b.txt',mimeType:'text/plain'},{name:'same0',value:'C',fileName:'c.txt',mimeType:'text/plain'}]);await assert.rejects(readWebhookRequest(request(collision,multipartHeaders),node()),fails(400,/겹쳐/));
});
test('multipart file and field base64 transfer encoding is restored across arbitrary chunk boundaries and malformed input fails',async()=>{
 const bytes=Buffer.from([0,255,128,1,2,3,10]),encoded=bytes.toString('base64'),parts=[{name:'file',value:encoded.slice(0,4)+'\r\n'+encoded.slice(4),fileName:'raw.bin',mimeType:'application/octet-stream',encoding:'base64'},{name:'title',value:Buffer.from('한글').toString('base64'),encoding:'base64'}],result=await readWebhookRequest(request(multipart(parts),multipartHeaders,3),node());assert.deepEqual(decodeBinaryFile(result.binary?.file),bytes);assert.deepEqual(result.body,{title:'한글'});
 for(const value of ['!AAA','A===','AA=A','AAA','AB==','AAAA====','AA==A'])await assert.rejects(readWebhookRequest(request(multipart([{name:'file',value,fileName:'raw.bin',mimeType:'application/octet-stream',encoding:'base64'}]),multipartHeaders,1),node()),fails(400,/base64/));
});
test('eight MiB raw and base64 multipart files fit with general data and decoded aggregate limits remain enforced',async()=>{
 const bytes=Buffer.alloc(MAX_BINARY_BYTES,255),raw=await readWebhookRequest(request(bytes,{'content-type':'application/octet-stream'}),node());assert.deepEqual(decodeBinaryFile(raw.binary?.data),bytes);
 const body=multipart([{name:'file',value:bytes.toString('base64'),fileName:'full.bin',mimeType:'application/octet-stream',encoding:'base64'},{name:'label',value:'x'.repeat(WEBHOOK_JSON_BYTES-100)}]);assert.ok(body.length<WEBHOOK_MULTIPART_BYTES);const large=await readWebhookRequest(request(body,multipartHeaders),node());assert.deepEqual(decodeBinaryFile(large.binary?.file),bytes);assert.equal((large.body as any).label.length,WEBHOOK_JSON_BYTES-100);
 const excessive=multipart([{name:'a',value:Buffer.alloc(MAX_BINARY_BYTES/2+1),fileName:'a.bin',mimeType:'application/octet-stream'},{name:'b',value:Buffer.alloc(MAX_BINARY_BYTES/2),fileName:'b.bin',mimeType:'application/octet-stream'}]);await assert.rejects(readWebhookRequest(request(excessive,multipartHeaders),node()),fails(413,/8MiB/));
});
test('normal JSON, multipart fields, wire transport and part counts reject without truncating successful outputs',async()=>{
 await assert.rejects(readWebhookRequest(request(Buffer.from('x'.repeat(WEBHOOK_JSON_BYTES+1)),{'content-type':'text/plain'}),node()),fails(413,/4MB/));
 const escaped=multipart([{name:'text',value:'"'.repeat(WEBHOOK_JSON_BYTES/2+1)}]);await assert.rejects(readWebhookRequest(request(escaped,multipartHeaders),node()),fails(413,/4MB/));
 for(const parts of [Array.from({length:101},(_,i)=>({name:'f'+i,value:'',fileName:'empty'+i+'.txt',mimeType:'text/plain'})),Array.from({length:1001},(_,i)=>({name:'field'+i,value:''}))])await assert.rejects(readWebhookRequest(request(multipart(parts),multipartHeaders),node()),fails(413,/100|1,000/));
 const fullCount=multipart([...Array.from({length:100},(_,i)=>({name:'f'+i,value:'',fileName:'empty'+i+'.txt',mimeType:'text/plain'})),...Array.from({length:1000},(_,i)=>({name:'field'+i,value:''}))]),accepted=await readWebhookRequest(request(fullCount,multipartHeaders),node());assert.equal(Object.keys(accepted.binary||{}).length,100);assert.equal(Object.keys(accepted.body as any).length,1000);
 const oversized=request(Buffer.alloc(0),{...multipartHeaders,'content-length':String(WEBHOOK_MULTIPART_BYTES+1)});await assert.rejects(readWebhookRequest(oversized,node()),fails(413,/크기/));assert.equal(oversized.destroyed,false);assert.equal(oversized.readableFlowing,null);
});
test('malformed multipart, prototype names and interrupted bodies fail without a partial result or destroying live transport',async()=>{
 await assert.rejects(readWebhookRequest(request(Buffer.from('body'),{'content-type':'multipart/form-data'}),node()),fails(400,/경계/));
 const truncated=multipart([{name:'file',value:'abc',fileName:'a.txt',mimeType:'text/plain'}]).subarray(0,-15);await assert.rejects(readWebhookRequest(request(truncated,multipartHeaders),node()),fails(400,/multipart/));
 for(const name of ['__proto__','constructor','prototype','__easyflowBinary'])await assert.rejects(readWebhookRequest(request(multipart([{name,value:'x'}]),multipartHeaders),node()),fails(400,/안전/));assert.equal(({} as any).x,undefined);
 for(const part of [{name:'bad\x7fname',value:'x'},{name:'bad\x7fname',value:'x',fileName:'x.bin',mimeType:'application/octet-stream'}])await assert.rejects(readWebhookRequest(request(multipart([part]),multipartHeaders),node()),fails(400,/안전|multipart/));
 const interrupted=Object.assign(new Readable({read(){this.push(Buffer.from('partial'));this.destroy();}}),{headers:{'content-type':'text/plain'},method:'POST',complete:false}) as unknown as IncomingMessage;await assert.rejects(readWebhookRequest(interrupted,node()),fails(400,/중단/));
 const active=Object.assign(new Readable({read(){this.push(Buffer.alloc(MAX_BINARY_BYTES+1));}}),{headers:{'content-type':'application/octet-stream'},method:'POST',complete:false}) as unknown as IncomingMessage;await assert.rejects(readWebhookRequest(active,node()),fails(413,/8MiB/));assert.equal(active.destroyed,false);assert.equal(active.readableFlowing,false);active.destroy(new Error('late transport close'));
});
test('activation validates receive modes and binary property names before a request can start',()=>{
 const good=node({binaryData:true,rawBody:true,binaryPropertyName:'첨부 파일'}),flow:Flow={id:'f',name:'f',nodes:[good],connections:{},settings:{timezone:'Asia/Seoul'}};assert.doesNotThrow(()=>validateWebhookConfiguration(flow,good));
 for(const options of [{rawBody:'false'},{binaryData:'={{ true }}'},{binaryPropertyName:''},{binaryPropertyName:'__proto__'},{binaryPropertyName:'a'.repeat(121)},{binaryPropertyName:'bad\0name'},{binaryPropertyName:'bad\x7fname'}])assert.throws(()=>validateWebhookRequestOptions(node(options)),WebhookRequestError);
});
