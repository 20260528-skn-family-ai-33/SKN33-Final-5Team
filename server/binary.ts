import type {FlowNode,Json} from '../lib/workflow';
import {DOCUMENT_TIMEOUT_MS,readXlsx,writeXlsx,extractPdfText} from './document-files';
import {attachItemPairs,type ItemPair} from './provenance';

export const BINARY_KEY='__easyflowBinary';
export const MAX_BINARY_BYTES=8*1024*1024;
export const MAX_BINARY_ITEMS=5000;
export const binaryNodeTypes=['n8n-nodes-base.convertToFile','n8n-nodes-base.extractFromFile'];
export type BinaryFile={data:string;mimeType:string;fileName:string;fileExtension?:string;fileSize:number};
export type BinaryMap=Record<string,BinaryFile>;
export type BinaryContext={resolveValue?:(value:any,item:Json)=>any;signal?:AbortSignal;timeoutMs?:number};
export type CsvOptions={delimiter?:string;headerRow?:boolean;skipEmptyLines?:boolean;addBOM?:boolean;stripBOM?:boolean;columns?:string[]};
const forbidden=new Set(['__proto__','prototype','constructor',BINARY_KEY]);
const BASE64='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const extensions:Record<string,string>={'application/json':'json','text/plain':'txt','text/csv':'csv','text/html':'html','application/rtf':'rtf','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':'xlsx','application/pdf':'pdf','image/png':'png','image/jpeg':'jpg','application/octet-stream':'bin'};

function validProperty(value:any,label='파일 항목 이름'):string {
 if(typeof value!=='string'||!value.trim()||value.length>120||forbidden.has(value)||/[\x00-\x1f]/.test(value))throw new Error(`${label}을 올바르게 입력해주세요.`);return value;
}
function safeName(value:any,mimeType:string):string {
 if(value!==undefined&&typeof value!=='string')throw new Error('파일 이름은 글자로 입력해주세요.');
 const name=String(value||'').split(/[\\/]/).at(-1)!.replace(/[\x00-\x1f\x7f]/g,'').trim();
 if(name.length>240)throw new Error('파일 이름은 240자 이하로 입력해주세요.');
 if(name==='.'||name==='..')throw new Error('파일 이름을 확인해주세요.');
 return name||`file.${extensions[mimeType.split(';')[0]]||'bin'}`;
}
function validMime(value:any):string {
 const mime=value??'application/octet-stream';
 if(typeof mime!=='string'||mime.length>200||!/^[-a-z\d!#$&^_.+]+\/[-a-z\d!#$&^_.+]+(?:;[ -~]+)?$/i.test(mime))throw new Error('파일 MIME 종류를 확인해주세요.');return mime;
}
function base64Size(data:any):number {
 if(typeof data!=='string')throw new Error('올바른 base64 파일 데이터를 입력해주세요.');
 if(data.length>Math.ceil(MAX_BINARY_BYTES/3)*4)throw new Error('파일은 8MB 이하로 입력해주세요.');
 if(data.length%4!==0||!/^[A-Za-z\d+/]*={0,2}$/.test(data))throw new Error('올바른 base64 파일 데이터를 입력해주세요.');
 const padding=data.endsWith('==')?2:data.endsWith('=')?1:0;
 // RFC 4648 canonical pad bits prevent silently accepting malformed encodings.
 if(padding&&BASE64.indexOf(data[data.length-padding-1])%(padding===2?16:4)!==0)throw new Error('올바른 base64 파일 데이터를 입력해주세요.');
 const size=data.length/4*3-padding;if(size>MAX_BINARY_BYTES)throw new Error('파일은 8MB 이하로 입력해주세요.');return size;
}
function normalizedFile(value:any):BinaryFile {
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('파일 정보가 없거나 잘못됐어요.');
 const size=base64Size(value.data);
 if(!Number.isSafeInteger(value.fileSize)||value.fileSize!==size)throw new Error('파일 크기 정보와 실제 내용이 달라요.');
 const mimeType=validMime(value.mimeType),fileName=safeName(value.fileName,mimeType),extension=/\.([^.]+)$/.exec(fileName)?.[1];
 return {data:value.data,mimeType,fileName,...(extension?{fileExtension:extension}:{}),fileSize:size};
}
export function createBinaryFile(bytes:Uint8Array,options:{mimeType?:string;fileName?:string}={}):BinaryFile {
 if(!(bytes instanceof Uint8Array))throw new Error('파일 내용은 바이트 데이터로 입력해주세요.');
 if(bytes.byteLength>MAX_BINARY_BYTES)throw new Error('파일은 8MB 이하로 입력해주세요.');
 const mimeType=validMime(options.mimeType),fileName=safeName(options.fileName,mimeType),extension=/\.([^.]+)$/.exec(fileName)?.[1];
 return {data:Buffer.from(bytes.buffer,bytes.byteOffset,bytes.byteLength).toString('base64'),mimeType,fileName,...(extension?{fileExtension:extension}:{}),fileSize:bytes.byteLength};
}
export function decodeBinaryFile(file:unknown):Buffer {return Buffer.from(normalizedFile(file).data,'base64');}
export function splitBinaryItem(item:Json):{json:Json;binary:BinaryMap} {
 if(!item||typeof item!=='object'||Array.isArray(item))throw new Error('파일 단계에는 JSON 객체 목록을 입력해주세요.');
 const json=Object.fromEntries(Object.entries(item).filter(([key])=>key!==BINARY_KEY));
 if(!Object.hasOwn(item,BINARY_KEY))return {json,binary:{}};
 const value=item[BINARY_KEY];if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('파일 항목 정보가 올바르지 않아요.');
 const binary=Object.fromEntries(Object.entries(value).map(([key,file])=>[validProperty(key),normalizedFile(file)]));
 return {json,binary};
}
export function jsonWithoutBinary(item:Json):Json {return splitBinaryItem(item).json;}
export function getBinary(item:Json,property='data'):BinaryFile {
 const file=splitBinaryItem(item).binary[validProperty(property)];if(!file)throw new Error(`‘${property}’ 파일이 없어요. 파일 업로드나 파일을 가져오는 단계를 먼저 연결해주세요.`);return file;
}
export function withBinary(item:Json,property:string,file:BinaryFile):Json {
 const {json,binary}=splitBinaryItem(item);return {...json,[BINARY_KEY]:{...binary,[validProperty(property)]:normalizedFile(file)}};
}
export function binaryMetadata(item:Json):Record<string,Omit<BinaryFile,'data'>> {
 return Object.fromEntries(Object.entries(splitBinaryItem(item).binary).map(([property,{data,...metadata}])=>[property,metadata]));
}
export function assertBinaryItems(items:Json[]):void {
 if(!Array.isArray(items)||items.length>MAX_BINARY_ITEMS)throw new Error('파일 단계는 한 번에 5,000개 항목까지 처리할 수 있어요.');
 let bytes=0;for(const item of items)for(const file of Object.values(splitBinaryItem(item).binary)){bytes+=file.fileSize;if(bytes>MAX_BINARY_BYTES)throw new Error('한 단계의 파일 총 크기는 8MB 이하로 줄여주세요.');}
}
export async function readBinaryResponse(response:Response,options:{mimeType?:string;fileName?:string}={},signal?:AbortSignal):Promise<BinaryFile> {
 if(!response.ok){void response.body?.cancel().catch(()=>{});throw new Error(`파일 요청이 HTTP ${response.status}로 응답했어요.`);}
 if(Number(response.headers.get('content-length'))>MAX_BINARY_BYTES){void response.body?.cancel().catch(()=>{});throw new Error('파일은 8MB 이하로 입력해주세요.');}
 const chunks:Uint8Array[]=[];let bytes=0;
 if(response.body){
  const reader=response.body.getReader(),abort=()=>{void reader.cancel().catch(()=>{});};signal?.addEventListener('abort',abort,{once:true});
  try{while(true){if(signal?.aborted){abort();throw new Error('파일 처리를 중지했어요.');}const {done,value}=await reader.read();if(signal?.aborted)throw new Error('파일 처리를 중지했어요.');if(done)break;bytes+=value.byteLength;if(bytes>MAX_BINARY_BYTES){abort();throw new Error('파일은 8MB 이하로 입력해주세요.');}chunks.push(value);}}
  catch(error){if(error instanceof Error&&['파일 처리를 중지했어요.','파일은 8MB 이하로 입력해주세요.'].includes(error.message))throw error;throw new Error('파일 응답을 끝까지 읽지 못했어요.');}
  finally{signal?.removeEventListener('abort',abort);reader.releaseLock();}
 }
 if(signal?.aborted)throw new Error('파일 처리를 중지했어요.');
 return createBinaryFile(Buffer.concat(chunks,bytes),{mimeType:options.mimeType||response.headers.get('content-type')||'application/octet-stream',fileName:options.fileName});
}
function csvDelimiter(value:any):string {const delimiter=value??',';if(typeof delimiter!=='string'||delimiter.length!==1||/["\r\n\0]/.test(delimiter))throw new Error('CSV 구분자는 따옴표나 줄바꿈을 제외한 한 글자로 입력해주세요.');return delimiter;}
/** RFC 4180 quoting, embedded newlines, CRLF/LF/CR and escaped double quotes. */
export function parseCsv(source:string,options:CsvOptions={}):Json[] {
 if(typeof source!=='string'||Buffer.byteLength(source,'utf8')>MAX_BINARY_BYTES)throw new Error('CSV 파일은 8MB 이하로 입력해주세요.');
 if(options.stripBOM!==false&&source.startsWith('\uFEFF'))source=source.slice(1);
 const delimiter=csvDelimiter(options.delimiter),rows:string[][]=[];
 let row:string[]=[],field='',quoted=false,closed=false,used=false;
 const cell=()=>{row.push(field);field='';closed=false;if(row.length>1000)throw new Error('CSV는 한 행에 1,000개 열까지 처리할 수 있어요.');};
 const record=()=>{cell();if(used||options.skipEmptyLines===false)rows.push(row);if(rows.length>MAX_BINARY_ITEMS+(options.headerRow===false?0:1))throw new Error('CSV는 5,000개 데이터 행까지 처리할 수 있어요.');row=[];used=false;};
 for(let i=0;i<source.length;i++){
  const char=source[i];
  if(quoted){if(char==='"'){if(source[i+1]==='"'){field+='"';i++;}else{quoted=false;closed=true;}}else field+=char;continue;}
  if(char===delimiter){used=true;cell();continue;}
  if(char==='\r'||char==='\n'){record();if(char==='\r'&&source[i+1]==='\n')i++;continue;}
  if(closed)throw new Error('CSV 따옴표를 닫은 뒤에는 구분자나 줄바꿈만 올 수 있어요.');
  if(char==='"'){if(field.length)throw new Error('CSV 값 안의 따옴표를 올바르게 감싸주세요.');quoted=true;used=true;}else{field+=char;used=true;}
 }
 if(quoted)throw new Error('CSV에 닫히지 않은 따옴표가 있어요.');
 if(used||field||row.length)record();if(!rows.length)return [];
 const headers=options.headerRow===false?rows[0].map((_,i)=>'column'+(i+1)):rows.shift()!;
 if(headers.some(h=>!h.trim()||forbidden.has(h))||new Set(headers).size!==headers.length)throw new Error('CSV 첫 행의 열 이름이 비어 있거나 중복돼요. 열 이름을 바꿔주세요.');
 return rows.map(cells=>{if(cells.length!==headers.length)throw new Error('CSV 행의 열 개수가 첫 행과 달라요. 파일의 구분자와 빈 칸을 확인해주세요.');return Object.fromEntries(headers.map((name,index)=>[name,cells[index]]));});
}
export function serializeCsv(items:Json[],options:CsvOptions={}):string {
 if(items.length>MAX_BINARY_ITEMS)throw new Error('CSV는 5,000개 데이터 행까지 만들 수 있어요.');
 const delimiter=csvDelimiter(options.delimiter),rows=items.map(jsonWithoutBinary),columns=options.columns??[...new Set(rows.flatMap(row=>Object.keys(row)))];
 if(!Array.isArray(columns)||columns.length>1000||columns.some(c=>typeof c!=='string'||!c.trim()||forbidden.has(c))||new Set(columns).size!==columns.length)throw new Error('CSV 열 이름을 확인해주세요. 한 번에 1,000개 열까지 만들 수 있어요.');
 const escaped=(value:any)=>{let content:string;if(value===null||value===undefined)content='';else if(typeof value==='object')content=JSON.stringify(value);else if(['string','number','boolean'].includes(typeof value))content=String(value);else throw new Error('CSV에는 JSON으로 표현할 수 있는 값을 입력해주세요.');return content.includes(delimiter)||/["\r\n]/.test(content)?'"'+content.replace(/"/g,'""')+'"':content;};
 const lines:string[]=[];let bytes=options.addBOM?3:0;
 const add=(line:string)=>{bytes+=Buffer.byteLength(line,'utf8')+(lines.length?2:0);if(bytes>MAX_BINARY_BYTES)throw new Error('CSV 파일은 8MB 이하로 줄여주세요.');lines.push(line);};
 if(options.headerRow!==false&&columns.length)add(columns.map(escaped).join(delimiter));
 for(const row of rows)add(columns.map(column=>escaped(row[column])).join(delimiter));
 return (options.addBOM?'\uFEFF':'')+lines.join('\r\n');
}
function boolean(value:any,fallback:boolean):boolean {if(value===undefined)return fallback;if(value===true||value==='true')return true;if(value===false||value==='false')return false;throw new Error('파일 옵션은 참 또는 거짓으로 선택해주세요.');}
function parameters(node:FlowNode,item:Json,context:BinaryContext):Json {
 const p=Object.fromEntries(Object.entries(node.parameters).map(([key,value])=>[key,context.resolveValue?context.resolveValue(value,item):value]));
 if(p.options!==undefined&&(!p.options||typeof p.options!=='object'||Array.isArray(p.options)))throw new Error('파일 옵션은 JSON 객체로 입력해주세요.');
 p.options=Object.fromEntries(Object.entries(p.options||{}).map(([key,value])=>[key,context.resolveValue?context.resolveValue(value,item):value]));
 // Flat beginner fields coexist with the original options object.
 for(const key of ['fileName','mimeType','encoding','headerRow','delimiter','addBOM','stripBOM','format','keepSource','skipEmptyLines','sheetName','includeEmptyCells','range','resultTypes','compression','joinPages','maxPages'])if(p[key]!==undefined)p.options[key]=p[key];
 const encoding=String(p.options.encoding??'utf8').toLowerCase();if(!['utf8','utf-8'].includes(encoding))throw new Error('현재 파일 변환은 UTF-8 인코딩을 지원해요. UTF-8 파일로 저장한 뒤 입력해주세요.');
 return p;
}
function sourceValue(item:Json,path:any):any {
 if(typeof path!=='string'||!path.trim())throw new Error('파일로 만들 입력 항목 이름을 입력해주세요.');
 const parts=path.split('.');if(parts.some(p=>!p||forbidden.has(p)))throw new Error('입력 항목 이름을 확인해주세요.');
 let value:any=jsonWithoutBinary(item);for(const part of parts){if(!value||typeof value!=='object'||!Object.hasOwn(value,part))throw new Error(`‘${path}’ 입력 항목이 없어요.`);value=value[part];}return value;
}
function destination(item:Json,path:any,value:any):Json {
 const name=path??'data';if(typeof name!=='string'||!name.trim()||name.length>200)throw new Error('결과를 담을 항목 이름을 입력해주세요.');
 const parts=name.split('.');if(parts.some(p=>!p||forbidden.has(p)))throw new Error('결과 항목 이름을 확인해주세요.');
 let target=item;for(const part of parts.slice(0,-1)){if(Object.hasOwn(target,part)){if(!target[part]||typeof target[part]!=='object'||Array.isArray(target[part]))throw new Error('결과 항목 이름이 기존 값과 겹쳐요. 다른 이름을 입력해주세요.');target[part]={...target[part]};}else target[part]={};target=target[part];}target[parts.at(-1)!]=value;return item;
}
function utf8(file:BinaryFile,stripBOM=true):string {
 let result:string;try{result=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(decodeBinaryFile(file));}catch{throw new Error('파일 내용이 올바른 UTF-8 텍스트가 아니에요.');}
 return stripBOM&&result.startsWith('\uFEFF')?result.slice(1):result;
}
function keepSource(item:Json,mode:any='json'):Json {
 if(!['json','binary','both','none'].includes(mode))throw new Error('원본 유지 방식은 JSON, 파일, 둘 다 또는 유지 안 함으로 선택해주세요.');
 const {json,binary}=splitBinaryItem(item);return {...(mode==='json'||mode==='both'?json:{}),...(mode==='binary'||mode==='both'?{[BINARY_KEY]:binary}:{})};
}
function encodedBase64(value:any):{bytes:Buffer;mimeType?:string} {
 if(typeof value!=='string')throw new Error('base64 입력 항목은 글자로 입력해주세요.');
 if(value.length>Math.ceil(MAX_BINARY_BYTES/3)*4+1024*1024)throw new Error('파일은 8MB 이하로 입력해주세요.');
 const uri=/^data:([^,]*);base64,([\s\S]*)$/i.exec(value);const mimeType=uri?.[1]||undefined;let data=(uri?uri[2]:value).replace(/[\t\r\n ]/g,'');
 if(data.length%4===1)throw new Error('올바른 base64 파일 데이터를 입력해주세요.');
 if(!data.includes('='))data=data.padEnd(Math.ceil(data.length/4)*4,'=');base64Size(data);return {bytes:Buffer.from(data,'base64'),mimeType};
}
export function createBinaryFileFromBase64(data:string,options:{mimeType?:string;fileName?:string}={}):BinaryFile {
 const file=encodedBase64(data);return createBinaryFile(file.bytes,{...options,mimeType:options.mimeType||file.mimeType||'application/octet-stream'});
}
function tableFile(items:Json[],format:'html'|'rtf',headerRow:boolean):string {
 const rows=items.map(jsonWithoutBinary),columns=[...new Set(rows.flatMap(row=>Object.keys(row)))];
 if(columns.length>1000||columns.some(c=>!c.trim()||forbidden.has(c)))throw new Error('표 파일의 열 이름을 확인해주세요. 1,000개 열까지 만들 수 있어요.');
 const string=(value:any)=>value===null||value===undefined?'':typeof value==='object'?JSON.stringify(value):String(value);
 const chunks:string[]=[];let size=0;const add=(value:string)=>{size+=Buffer.byteLength(value,'utf8');if(size>MAX_BINARY_BYTES)throw new Error('표 파일은 8MB 이하로 줄여주세요.');chunks.push(value);};
 if(format==='html'){
  const escape=(value:any)=>string(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
  add('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'"><title>데이터 표</title></head><body><table>');
  if(headerRow)add('<thead><tr>'+columns.map(c=>'<th>'+escape(c)+'</th>').join('')+'</tr></thead>');add('<tbody>');
  for(const row of rows)add('<tr>'+columns.map(c=>'<td>'+escape(row[c])+'</td>').join('')+'</tr>');add('</tbody></table></body></html>');
 } else {
  const escape=(value:any)=>{const valueString=string(value);let result='';for(let i=0;i<valueString.length;i++){const code=valueString.charCodeAt(i),char=valueString[i];if(char==='\\'||char==='{'||char==='}')result+='\\'+char;else if(char==='\r'){if(valueString[i+1]!=='\n')result+='\\line ';}else if(char==='\n')result+='\\line ';else if(char==='\t')result+='\\tab ';else if(code>=128)result+='\\u'+(code>32767?code-65536:code)+'?';else if(code>=32)result+=char;}return result;};
  add('{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Arial;}}\\uc1\\f0\\fs22\n');const width=Math.max(1,Math.floor(9000/Math.max(columns.length,1))),cellDefinitions=columns.map((_,i)=>'\\cellx'+width*(i+1)).join('');
  const addRow=(values:any[])=>add('\\trowd\\trgaph108\\trleft0'+cellDefinitions+'\n'+values.map(value=>'\\intbl '+escape(value)+'\\cell ').join('')+'\\row\n');
  if(headerRow)addRow(columns);for(const row of rows)addRow(columns.map(c=>row[c]));add('}');
 }
 return chunks.join('');
}
/** Memory-only conversion; no filesystem paths, URLs, or external services are accessed. */
export async function executeBinaryNode(node:FlowNode,input:Json[],context:BinaryContext={}):Promise<Json[][]> {
 if(!binaryNodeTypes.includes(node.type))throw new Error('지원하지 않는 파일 단계예요.');
 assertBinaryItems(input);const convert=node.type===binaryNodeTypes[0],operation=node.parameters.operation??'csv';
 const supported=convert?['csv','toJson','toText','toBinary','xlsx','html','rtf']:['csv','fromJson','text','binaryToPropery','binaryToProperty','xlsx','pdf'];
 if(!supported.includes(operation))throw new Error('지원하는 파일 형식은 CSV, JSON, UTF-8 텍스트, base64, XLSX, PDF 읽기, HTML·RTF 작성이에요.');
 if(!input.length)return [attachItemPairs([],[])];if(context.signal?.aborted)throw new Error('파일 처리를 중지했어요.');
 const started=Date.now(),documentContext=()=>{const timeout=context.timeoutMs??DOCUMENT_TIMEOUT_MS;if(!Number.isInteger(timeout)||timeout<1||timeout>30000)throw new Error('파일 처리 제한 시간은 1~30,000ms로 지정해주세요.');const remaining=timeout-(Date.now()-started);if(remaining<=0)throw new Error('파일 처리 시간이 초과됐어요. 문서를 나누거나 파일을 줄여주세요.');return {signal:context.signal,timeoutMs:remaining};};
 const output:Json[]=[],pairs:ItemPair[][]=[];
 const allSources=input.map((_,item)=>({item}));
 const add=(items:Json[],sourceItemIndex?:number)=>{if(output.length+items.length>MAX_BINARY_ITEMS)throw new Error('파일 결과는 한 번에 5,000개 항목까지 처리할 수 있어요.');output.push(...items);for(const _ of items)pairs.push(sourceItemIndex===undefined?[...allSources]:[{item:sourceItemIndex}]);};
 const fileItem=(bytes:Uint8Array,p:Json,mimeType:string,fallbackName:string)=>withBinary({},validProperty(p.binaryPropertyName??'data'),createBinaryFile(bytes,{mimeType,fileName:p.options.fileName||fallbackName}));
 if(convert&&(['csv','xlsx','html','rtf'].includes(operation)||operation==='toJson'&&(node.parameters.mode??'once')==='once')){
  const p=parameters(node,input[0],context);
  if(operation==='csv')add([fileItem(Buffer.from(serializeCsv(input,{delimiter:p.options.delimiter,headerRow:boolean(p.options.headerRow,true),addBOM:boolean(p.options.addBOM,false)}),'utf8'),p,'text/csv','file.csv')]);
  else if(operation==='xlsx'){const bytes=await writeXlsx(input.map(jsonWithoutBinary),{sheetName:p.options.sheetName,headerRow:boolean(p.options.headerRow,true),compression:boolean(p.options.compression,true)},documentContext());add([fileItem(bytes,p,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','file.xlsx')]);}
  else if(operation==='html'||operation==='rtf'){const value=tableFile(input,operation,boolean(p.options.headerRow,true));add([fileItem(Buffer.from(value,'utf8'),p,operation==='html'?'text/html; charset=utf-8':'application/rtf','file.'+operation)]);}
  else{let value:string;try{value=JSON.stringify(input.map(jsonWithoutBinary),null,boolean(p.options.format,false)?2:undefined);}catch{throw new Error('입력 데이터를 JSON 파일로 만들 수 없어요.');}if(boolean(p.options.addBOM,false))value='\uFEFF'+value;add([fileItem(Buffer.from(value,'utf8'),p,'application/json','file.json')]);}
 }else for(const [itemIndex,item] of input.entries()){
  if(context.signal?.aborted)throw new Error('파일 처리를 중지했어요.');const p=parameters(node,item,context);
  if(convert){
   if(operation==='toJson'){if(p.mode!==undefined&&p.mode!=='each')throw new Error('JSON 파일은 전체 항목 또는 항목마다 만들기를 선택해주세요.');let value:string;try{value=JSON.stringify(jsonWithoutBinary(item),null,boolean(p.options.format,false)?2:undefined);}catch{throw new Error('입력 데이터를 JSON 파일로 만들 수 없어요.');}if(boolean(p.options.addBOM,false))value='\uFEFF'+value;add([fileItem(Buffer.from(value,'utf8'),p,'application/json','file.json')],itemIndex);}
   else if(operation==='toText'){let value=sourceValue(item,p.sourceProperty);if(typeof value!=='string')throw new Error('텍스트 파일로 만들 항목은 글자 값이어야 해요.');if(boolean(p.options.addBOM,false))value='\uFEFF'+value;add([fileItem(Buffer.from(value,'utf8'),p,'text/plain','file.txt')],itemIndex);}
   else {const file=encodedBase64(sourceValue(item,p.sourceProperty));add([fileItem(file.bytes,p,p.options.mimeType||file.mimeType||'application/octet-stream','file.bin')],itemIndex);}
  }else{
   const file=getBinary(item,p.binaryPropertyName??'data'),base=keepSource(item,p.options.keepSource??'json');
   if(operation==='xlsx'){const rows=await readXlsx(decodeBinaryFile(file),{sheetName:p.options.sheetName,headerRow:boolean(p.options.headerRow,true),includeEmptyCells:boolean(p.options.includeEmptyCells,false),range:p.options.range,resultTypes:p.options.resultTypes??'typed'},documentContext());add(rows.map(row=>({...base,...row})),itemIndex);}
   else if(operation==='pdf'){if(p.options.password)throw new Error('암호화된 PDF는 먼저 암호를 해제한 뒤 입력해주세요.');const pdf=await extractPdfText(decodeBinaryFile(file),{joinPages:boolean(p.options.joinPages,true),maxPages:p.options.maxPages===undefined||p.options.maxPages===''?0:Number(p.options.maxPages)},documentContext());add([{...base,...pdf}],itemIndex);}
   else if(operation==='binaryToPropery'||operation==='binaryToProperty')add([destination(base,p.destinationKey,file.data)],itemIndex);
   else {
    const content=utf8(file,boolean(p.options.stripBOM,true));
    if(operation==='text')add([destination(base,p.destinationKey,content)],itemIndex);
    else if(operation==='fromJson'){let value:any;try{value=content===''?{}:JSON.parse(content);}catch{throw new Error('파일 내용이 올바른 JSON이 아니에요.');}add([destination(base,p.destinationKey,value)],itemIndex);}
    else {const rows=parseCsv(content,{delimiter:p.options.delimiter,headerRow:boolean(p.options.headerRow,true),skipEmptyLines:boolean(p.options.skipEmptyLines,true),stripBOM:false});add(rows.map(row=>({...base,...row})),itemIndex);}
   }
  }
 }
 assertBinaryItems(output);return [attachItemPairs(output,pairs)];
}
