import {randomUUID} from 'node:crypto';
import type {IncomingMessage} from 'node:http';
import {Readable,Transform} from 'node:stream';
import {createGunzip,createInflate} from 'node:zlib';
import busboy from 'busboy';
import {Parser as XmlParser} from 'xml2js';
import type {FlowNode,Json} from '../lib/workflow';
import {assertBinaryItems,createBinaryFile,MAX_BINARY_BYTES,withBinary,type BinaryFile,type BinaryMap} from './binary';
import {assertWebhookJsonSize} from './webhook-response';

export const WEBHOOK_JSON_BYTES=4_000_000;
export const WEBHOOK_OVERHEAD_BYTES=1024*1024;
export const WEBHOOK_MULTIPART_BYTES=Math.ceil(MAX_BINARY_BYTES/3)*4+WEBHOOK_JSON_BYTES+WEBHOOK_OVERHEAD_BYTES;
export const WEBHOOK_MAX_FILES=100;
export const WEBHOOK_MAX_FIELDS=1000;
export const WEBHOOK_MAX_PARTS=1100;
export const WEBHOOK_READ_TIMEOUT_MS=60_000;
export const WEBHOOK_XML_MAX_NODES=50_000;
export const WEBHOOK_XML_MAX_DEPTH=100;
export const WEBHOOK_XML_TIMEOUT_MS=2000;
const forbidden=new Set(['__proto__','prototype','constructor','__easyflowBinary']);
const encodingAliases:Record<string,BufferEncoding>={'utf-8':'utf8',utf8:'utf8','utf-16le':'utf16le',utf16le:'utf16le',latin1:'latin1','iso-8859-1':'latin1',ascii:'ascii'};
export class WebhookRequestError extends Error {
 constructor(message:string,readonly status:400|408|413|415=400){super(message);this.name='WebhookRequestError';}
}
type RequestBody={body:unknown;binary?:BinaryMap};
/** Tiny incoming chunks must not allocate one retained Buffer/array entry each. */
class ByteCollector {
 private blocks:Buffer[]=[];
 private active?:Buffer;
 private offset=0;
 private size=0;
 add(bytes:Buffer):void {
  this.size+=bytes.length;let position=0;
  while(position<bytes.length){this.active??=Buffer.allocUnsafe(64*1024);const length=Math.min(this.active.length-this.offset,bytes.length-position);bytes.copy(this.active,this.offset,position,position+length);this.offset+=length;position+=length;if(this.offset===this.active.length){this.blocks.push(this.active);this.active=undefined;this.offset=0;}}
 }
 finish():Buffer {return Buffer.concat([...this.blocks,...(this.active?[this.active.subarray(0,this.offset)]:[])],this.size);}
 clear():void {this.blocks=[];this.active=undefined;this.offset=0;this.size=0;}
}

function property(value:unknown,label='파일 항목 이름'):string {
 if(typeof value!=='string'||!value.trim()||value.length>120||forbidden.has(value)||/[\x00-\x1f\x7f]/.test(value))throw new WebhookRequestError(`${label}은 1~120자의 안전한 이름으로 입력해주세요.`);return value;
}
export function validateWebhookRequestOptions(node:FlowNode):void {
 const options=node.parameters.options;if(options!==undefined&&(!options||typeof options!=='object'||Array.isArray(options)))throw new WebhookRequestError('Webhook 옵션은 JSON 객체로 입력해주세요.');
 for(const key of ['binaryData','rawBody'])if(options?.[key]!==undefined&&typeof options[key]!=='boolean')throw new WebhookRequestError('파일 수신과 원문 보관은 켜기 또는 끄기로 선택해주세요.');
 if(options?.binaryPropertyName!==undefined)property(options.binaryPropertyName);
}
function parameters(header:string):{type:string;params:Record<string,string>} {
 const parts:string[]=[];let part='',quoted=false,escaped=false;
 for(const char of header){if(escaped){part+=char;escaped=false;}else if(quoted&&char==='\\'){part+=char;escaped=true;}else if(char==='"'){quoted=!quoted;part+=char;}else if(char===';'&&!quoted){parts.push(part.trim());part='';}else part+=char;}parts.push(part.trim());
 const entries:[string,string][]=[];for(const token of parts.slice(1)){const match=/^([^=\s]+)\s*=\s*(.*)$/.exec(token);if(!match)continue;let value=match[2];if(value.startsWith('"')&&value.endsWith('"'))value=value.slice(1,-1).replace(/\\(.)/g,'$1');entries.push([match[1].toLowerCase(),value]);}
 return {type:parts[0].toLowerCase(),params:Object.fromEntries(entries)};
}
function filename(req:IncomingMessage):string {
 const header=req.headers['content-disposition'];if(typeof header!=='string')return randomUUID();const info=parameters(/^(?:attachment|inline)(?:\s*;|$)/i.test(header)?header:`attachment; ${header}`);
 if(info.params['filename*']){const match=/^([^']*)'[^']*'(.*)$/.exec(info.params['filename*']);if(!match||!['utf-8','utf8'].includes(match[1].toLowerCase()))throw new WebhookRequestError('파일 이름의 문자 인코딩은 UTF-8로 보내주세요.');try{return decodeURIComponent(match[2]);}catch{throw new WebhookRequestError('파일 이름의 UTF-8 값을 확인해주세요.');}}
 return info.params.filename??randomUUID();
}
function contentLength(req:IncomingMessage,limit:number):void {
 const value=req.headers['content-length'];if(value!==undefined&&(typeof value!=='string'||!/^\d+$/.test(value)||!Number.isSafeInteger(Number(value))))throw new WebhookRequestError('요청 본문의 크기 헤더를 확인해주세요.');if(value!==undefined&&Number(value)>limit)throw new WebhookRequestError('Webhook 요청 본문이 허용 크기를 넘었어요.',413);
}
function decode(bytes:Buffer,charset:string):string {
 const encoding=encodingAliases[charset.toLowerCase()];if(!encoding)throw new WebhookRequestError('지원되는 UTF-8, UTF-16LE 또는 Latin-1 인코딩으로 보내주세요.',415);
 if(encoding==='utf8'){try{return new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);}catch{throw new WebhookRequestError('요청 본문의 UTF-8 글자가 잘못됐어요.');}}return bytes.toString(encoding);
}
function binary(bytes:Buffer,mimeType:string,fileName?:string):BinaryFile {
 try{return createBinaryFile(bytes,{mimeType,fileName});}catch(error){throw new WebhookRequestError((error as Error).message,bytes.length>MAX_BINARY_BYTES?413:400);}
}
function jsonSize(body:unknown):void {
 try{assertWebhookJsonSize({body});}catch(error){throw new WebhookRequestError((error as Error).message,/4MB|한도|너무|깊이/.test((error as Error).message)?413:400);}
}
/** Retains original octets unless a parsed-body mode requests native gzip/deflate decoding. */
function readBytes(req:IncomingMessage,limit:number,encoding='identity'):Promise<Buffer> {
 const wireLimit=encoding==='identity'?limit:limit+WEBHOOK_OVERHEAD_BYTES;contentLength(req,wireLimit);
 return new Promise((resolve,reject)=>{
  let settled=false,bytes=0,wireBytes=0;const chunks=new ByteCollector();const decompressor=encoding==='gzip'?createGunzip():encoding==='deflate'?createInflate():undefined;const source:Readable=decompressor||req;
  const timer=setTimeout(()=>fail(new WebhookRequestError('Webhook 본문을 받는 제한 시간이 지났어요.',408)),WEBHOOK_READ_TIMEOUT_MS);timer.unref();
  const cleanup=(success=false)=>{clearTimeout(timer);req.off('aborted',aborted);if(success)req.off('error',error);req.off('close',closed);req.off('data',countWire);source.off('data',data);source.off('end',end);if(decompressor){req.unpipe(decompressor);if(success)decompressor.off('error',error);}};
  const fail=(reason:Error)=>{if(settled)return;settled=true;cleanup();req.pause();decompressor?.destroy();chunks.clear();reject(reason);};
  const error=()=>fail(new WebhookRequestError('Webhook 본문을 끝까지 읽거나 압축을 해제하지 못했어요.'));
  const aborted=()=>fail(new WebhookRequestError('Webhook 본문 수신이 중단됐어요.'));
  const closed=()=>{if(!req.complete&&!req.readableEnded)aborted();};
  const countWire=(chunk:Buffer)=>{wireBytes+=chunk.length;if(wireBytes>wireLimit)fail(new WebhookRequestError('Webhook 요청 본문이 허용 크기를 넘었어요.',413));};
  const data=(chunk:Buffer)=>{if(settled)return;bytes+=chunk.length;if(bytes>limit){fail(new WebhookRequestError(limit===MAX_BINARY_BYTES?'파일 총 크기는 8MiB 이하로 줄여주세요.':'Webhook 일반 데이터는 4MB 이하로 줄여주세요.',413));return;}chunks.add(chunk);};
  const end=()=>{if(settled)return;settled=true;cleanup(true);resolve(chunks.finish());};
  req.once('aborted',aborted);req.once('error',error);req.once('close',closed);source.on('data',data);source.once('end',end);if(decompressor){req.on('data',countWire);decompressor.once('error',error);req.pipe(decompressor);}if(req.destroyed&&!req.readableEnded)aborted();else if(req.readableEnded)end();
 });
}
function parsedEncoding(req:IncomingMessage):string {
 const value=String(req.headers['content-encoding']||'identity').toLowerCase().trim();if(!['identity','gzip','deflate'].includes(value))throw new WebhookRequestError('일반 본문의 압축은 gzip 또는 deflate로 보내주세요.',415);return value;
}
function parseXml(text:string):Promise<unknown> {
 return new Promise((resolve,reject)=>{
  let settled=false,nodes=0,depth=0;const started=Date.now();let parser:XmlParser;
  const finish=(error:Error|null,result?:unknown)=>{if(settled)return;settled=true;clearTimeout(timer);if(error){(parser as any).remaining='';(parser as any).saxParser.errThrown=true;reject(error);}else resolve(result);};
  const unsafe=(message:string,status:400|413=400):never=>{const error=new WebhookRequestError(message,status);finish(error);throw error;};
  const safeName=(name:string)=>{if(['__proto__','prototype','constructor'].includes(name))unsafe('XML의 prototype 관련 항목 이름은 사용할 수 없어요.');return name;};
  parser=new XmlParser({async:true,chunkSize:4096,normalize:true,normalizeTags:true,explicitArray:false,strict:true,tagNameProcessors:[safeName],attrNameProcessors:[safeName]});
  const sax=(parser as any).saxParser,open=sax.onopentag,close=sax.onclosetag;
  sax.ondoctype=()=>unsafe('XML의 DOCTYPE과 외부 엔터티는 지원하지 않아요.');
  sax.onopentag=(tag:any)=>{if(settled)throw new WebhookRequestError('XML 처리를 중단했어요.');if(++nodes>WEBHOOK_XML_MAX_NODES||++depth>WEBHOOK_XML_MAX_DEPTH)unsafe('XML은 항목 50,000개와 깊이 100까지 처리할 수 있어요.',413);if(Date.now()-started>WEBHOOK_XML_TIMEOUT_MS)unsafe('XML 처리 제한 시간이 지났어요.',413);open.call(sax,tag);};
  sax.onclosetag=(tag:string)=>{close.call(sax,tag);depth--;};
  const timer=setTimeout(()=>finish(new WebhookRequestError('XML 처리 제한 시간이 지났어요.',413)),WEBHOOK_XML_TIMEOUT_MS);
  parser.parseString(text,(error,result)=>finish(error instanceof WebhookRequestError?error:error?new WebhookRequestError('요청 본문이 올바른 XML이 아니에요.'):null,result));
 });
}
type Upload={name:string;fileName?:string;mimeType:string;chunks:ByteCollector;size:number};
/** Decodes complete base64 groups without assuming request chunks align with groups. */
function base64Decoder(add:(bytes:Buffer)=>void):{write:(bytes:Buffer)=>void;end:()=>void} {
 let pending='',padded=false;
 return {write(bytes){const text=bytes.toString('latin1').replace(/[ \t\r\n]/g,'');if(!/^[A-Za-z0-9+/=]*$/.test(text)||padded&&text)throw new WebhookRequestError('파일의 base64 전송 인코딩이 잘못됐어요.');pending+=text;const length=pending.length-pending.length%4;if(!length)return;const part=pending.slice(0,length);pending=pending.slice(length);if(!/^[A-Za-z0-9+/]*={0,2}$/.test(part))throw new WebhookRequestError('파일의 base64 전송 인코딩이 잘못됐어요.');const decoded=Buffer.from(part,'base64');if(decoded.toString('base64')!==part)throw new WebhookRequestError('파일의 base64 전송 인코딩이 잘못됐어요.');padded=part.endsWith('=');if(padded&&pending)throw new WebhookRequestError('파일의 base64 전송 인코딩이 잘못됐어요.');add(decoded);},end(){if(pending)throw new WebhookRequestError('파일의 base64 전송 인코딩이 잘못됐어요.');}};
}
function readMultipart(req:IncomingMessage,node:FlowNode,charset:string):Promise<RequestBody> {
 const compression=String(req.headers['content-encoding']||'identity').toLowerCase().trim();if(compression!=='identity')throw new WebhookRequestError('multipart 파일 요청의 압축은 아직 지원하지 않아요. 압축하지 않고 보내주세요.',415);
 if(!encodingAliases[charset.toLowerCase()])throw new WebhookRequestError('양식 문자 인코딩을 확인해주세요.',415);contentLength(req,WEBHOOK_MULTIPART_BYTES);
 let parser:busboy.Busboy;try{parser=busboy({headers:req.headers,defCharset:charset,defParamCharset:'utf8',preservePath:false,limits:{fileSize:WEBHOOK_MULTIPART_BYTES+1,fieldSize:WEBHOOK_JSON_BYTES+1,files:WEBHOOK_MAX_FILES,fields:WEBHOOK_MAX_FIELDS,parts:WEBHOOK_MAX_PARTS+1,headerPairs:100}});}catch{throw new WebhookRequestError('multipart 요청의 경계와 Content-Type을 확인해주세요.');}
 return new Promise((resolve,reject)=>{
  let settled=false,wireBytes=0,fileBytes=0,textBytes=0;const fields=new Map<string,string[]>(),uploads:Upload[]=[],streams=new Set<Readable>();
  const limiter=new Transform({transform(chunk:Buffer,_encoding,callback){wireBytes+=chunk.length;if(wireBytes>WEBHOOK_MULTIPART_BYTES)callback(new WebhookRequestError('multipart 본문은 파일 8MiB, 일반 데이터 4MB와 요청 정보 1MiB 범위로 줄여주세요.',413));else callback(null,chunk);}});
  const timer=setTimeout(()=>fail(new WebhookRequestError('Webhook 본문을 받는 제한 시간이 지났어요.',408)),WEBHOOK_READ_TIMEOUT_MS);timer.unref();
  const cleanup=(success=false)=>{clearTimeout(timer);req.off('aborted',aborted);if(success)req.off('error',error);req.off('close',closed);req.unpipe(limiter);limiter.unpipe(parser);};
  const fail=(reason:Error)=>{if(settled)return;settled=true;cleanup();req.pause();for(const stream of streams)stream.destroy();limiter.destroy();parser.destroy();for(const upload of uploads)upload.chunks.clear();reject(reason instanceof WebhookRequestError?reason:new WebhookRequestError('multipart 요청을 끝까지 해석하지 못했어요.'));};
  const error=()=>fail(new WebhookRequestError('Webhook 본문 수신이 중단됐어요.'));
  const aborted=error,closed=()=>{if(!req.complete&&!req.readableEnded)error();};
  req.once('aborted',aborted);req.once('error',error);req.once('close',closed);limiter.on('error',fail);parser.on('error',fail);
  parser.on('filesLimit',()=>fail(new WebhookRequestError('파일은 한 요청에 100개까지 보낼 수 있어요.',413)));parser.on('fieldsLimit',()=>fail(new WebhookRequestError('일반 양식 항목은 1,000개까지 보낼 수 있어요.',413)));parser.on('partsLimit',()=>fail(new WebhookRequestError('양식과 파일 항목은 합해서 1,100개까지 보낼 수 있어요.',413)));
  parser.on('field',(name,value,info)=>{
   if(settled)return;try{if(typeof name!=='string'||name.length>120||/[\x00-\x1f\x7f]/.test(name)||forbidden.has(name))throw new WebhookRequestError('양식 항목 이름은 120자 이하의 안전한 글자로 보내주세요.');if(info.nameTruncated||info.valueTruncated)throw new WebhookRequestError('양식 항목의 이름이나 내용이 허용 크기를 넘었어요.',413);if(!['7bit','8bit','binary','utf-8','base64'].includes(info.encoding))throw new WebhookRequestError('양식 항목의 전송 인코딩을 확인해주세요.',415);if(info.encoding==='base64'){const chunks=new ByteCollector();const decoder=base64Decoder(bytes=>chunks.add(bytes));decoder.write(Buffer.from(value,'utf8'));decoder.end();value=decode(chunks.finish(),charset);}textBytes+=Buffer.byteLength(value,'utf8');if(textBytes>WEBHOOK_JSON_BYTES)throw new WebhookRequestError('양식 일반 데이터는 합해서 4MB 이하로 줄여주세요.',413);const values=fields.get(name)||[];values.push(value);fields.set(name,values);}catch(reason){fail(reason as Error);}
  });
  parser.on('file',(name,stream,info)=>{
   streams.add(stream);stream.on('error',fail);if(settled){stream.resume();return;}
   const upload:Upload={name:name??'',fileName:info.filename,mimeType:info.mimeType||'application/octet-stream',chunks:new ByteCollector(),size:0};uploads.push(upload);
   const add=(bytes:Buffer)=>{if(settled)return;fileBytes+=bytes.length;upload.size+=bytes.length;if(fileBytes>MAX_BINARY_BYTES)throw new WebhookRequestError('파일 총 크기는 8MiB 이하로 줄여주세요.',413);upload.chunks.add(bytes);};
   if(!['7bit','8bit','binary','utf-8','base64'].includes(info.encoding)){fail(new WebhookRequestError('파일의 전송 인코딩을 확인해주세요.',415));return;}const decoder=info.encoding==='base64'?base64Decoder(add):undefined;
   stream.on('data',(chunk:Buffer)=>{try{if(decoder)decoder.write(chunk);else add(chunk);}catch(reason){fail(reason as Error);}});stream.on('limit',()=>fail(new WebhookRequestError('파일 본문이 허용 크기를 넘었어요.',413)));stream.once('end',()=>{streams.delete(stream);try{decoder?.end();}catch(reason){fail(reason as Error);}});
  });
  parser.once('close',()=>{
   if(settled)return;try{const body=Object.fromEntries([...fields].map(([key,values])=>[key,values.length===1?values[0]:values]));jsonSize(body);const groups:Record<string,Upload[]>=Object.fromEntries([...new Set(uploads.map(upload=>upload.name))].map(key=>[key,uploads.filter(upload=>upload.name===key&&!(upload.size===0&&!upload.fileName))]));let item:Json={},count=0;
    for(const [key,group] of Object.entries(groups))for(const [index,upload] of group.entries()){let name=key.endsWith('[]')?key.slice(0,-2):key;if(!name.trim())name=`data${count}`;else if(group.length>1)name+=index;const prefix=node.parameters.options?.binaryPropertyName;if(prefix)name=`${prefix}${count}`;property(name);if(Object.hasOwn(item.__easyflowBinary||{},name))throw new WebhookRequestError('여러 첨부 파일의 항목 이름이 겹쳐요. 서로 다른 이름이나 파일 이름 접두어를 사용해주세요.');item=withBinary(item,name,binary(upload.chunks.finish(),upload.mimeType,upload.fileName));count++;}
    assertBinaryItems([item]);settled=true;cleanup(true);resolve({body,...(count?{binary:item.__easyflowBinary}: {})});
   }catch(reason){fail(reason as Error);}
  });
  req.pipe(limiter).pipe(parser);if(req.destroyed&&!req.readableEnded)error();
 });
}

export async function readWebhookRequest(req:IncomingMessage,node:FlowNode):Promise<RequestBody> {
 validateWebhookRequestOptions(node);const options=node.parameters.options||{},header=req.headers['content-type'],mime=parameters(typeof header==='string'?header:''),type=mime.type||'application/octet-stream',charset=mime.params.charset||'utf-8';
 const rawFile=async():Promise<RequestBody>=>{const bytes=await readBytes(req,MAX_BINARY_BYTES);return {body:{},...(bytes.length?{binary:{[property(options.binaryPropertyName??'data')]:binary(bytes,type,filename(req))}}:{})};};
 if(options.binaryData===true)return rawFile();
 if(type==='multipart/form-data')return readMultipart(req,node,charset);
 const kind=type==='application/json'||type.endsWith('+json')?'json':type==='text/plain'?'text':type==='application/x-www-form-urlencoded'?'form':type.endsWith('/xml')||type.endsWith('+xml')?'xml':undefined;
 if(!kind&&options.rawBody!==true&&node.typeVersion>1)return rawFile();
 const bytes=await readBytes(req,kind?WEBHOOK_JSON_BYTES:options.rawBody===true?MAX_BINARY_BYTES:WEBHOOK_JSON_BYTES,parsedEncoding(req));let body:unknown={};
 if(bytes.length&&kind){const text=decode(bytes,charset);try{if(kind==='json')body=JSON.parse(text.replace(/^\uFEFF/,''));else if(kind==='text')body=text;else if(kind==='form'){const values=new URLSearchParams(text.replace(/^\uFEFF/,''));if([...values].length>WEBHOOK_MAX_FIELDS)throw new WebhookRequestError('일반 양식 항목은 1,000개까지 보낼 수 있어요.',413);body=Object.fromEntries([...new Set(values.keys())].map(key=>{const items=values.getAll(key);return [key,items.length===1?items[0]:items];}));}else body=await parseXml(text);}catch(error){throw error instanceof WebhookRequestError?error:new WebhookRequestError('Webhook 본문을 올바른 JSON으로 보내주세요.');}}
 jsonSize(body);return {body,...(options.rawBody===true?{binary:{data:binary(bytes,type)}}:{})};
}
