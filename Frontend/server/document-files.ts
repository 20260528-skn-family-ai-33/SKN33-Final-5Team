import {Worker} from 'node:worker_threads';
import {createRequire} from 'node:module';
import {inflateRawSync} from 'node:zlib';
import type {Json} from '../lib/workflow';

export const MAX_DOCUMENT_BYTES=8*1024*1024;
export const MAX_XLSX_EXPANDED_BYTES=64*1024*1024;
export const DOCUMENT_TIMEOUT_MS=15000;
export type DocumentContext={signal?:AbortSignal;timeoutMs?:number};
export type XlsxOptions={sheetName?:string;headerRow?:boolean;includeEmptyCells?:boolean;range?:string;resultTypes?:'typed'|'strings';compression?:boolean};
export type PdfOptions={joinPages?:boolean;maxPages?:number};
export type PdfText={numpages:number;numrender:number;text:string|string[]};
export type ArchiveStats={entries:number;expandedBytes:number};

// Self-contained so the same preflight runs inside the terminable parser worker.
function inspectZip(bytes:Buffer,inflate:(data:Uint8Array,options:{maxOutputLength:number})=>Buffer):ArchiveStats {
 const invalid=()=>new Error('올바른 XLSX 압축 파일이 아니에요. Excel에서 XLSX로 다시 저장해주세요.');
 if(bytes.length>8*1024*1024)throw new Error('파일은 8MB 이하로 입력해주세요.');
 let end=-1;for(let i=bytes.length-22;i>=Math.max(0,bytes.length-65557);i--)if(bytes.readUInt32LE(i)===0x06054b50&&i+22+bytes.readUInt16LE(i+20)===bytes.length){end=i;break;}
 if(end<0)throw invalid();
 const count=bytes.readUInt16LE(end+10),directorySize=bytes.readUInt32LE(end+12),directoryOffset=bytes.readUInt32LE(end+16);
 if(bytes.readUInt16LE(end+4)||bytes.readUInt16LE(end+6)||bytes.readUInt16LE(end+8)!==count||count===65535||directoryOffset===0xffffffff||directorySize===0xffffffff)throw new Error('여러 조각 또는 ZIP64 XLSX는 지원하지 않아요. 일반 XLSX로 저장해주세요.');
 if(count>2000)throw new Error('Excel 파일 안의 항목은 2,000개 이하로 줄여주세요.');
 if(directoryOffset+directorySize!==end)throw invalid();
 let offset=directoryOffset,total=0;const names=new Set<string>(),ranges:{start:number;end:number}[]=[];
 const crcTable=new Uint32Array(256);for(let n=0;n<256;n++){let crc=n;for(let bit=0;bit<8;bit++)crc=crc&1?0xedb88320^(crc>>>1):crc>>>1;crcTable[n]=crc>>>0;}
 for(let index=0;index<count;index++){
  if(offset+46>end||bytes.readUInt32LE(offset)!==0x02014b50)throw invalid();
  const flags=bytes.readUInt16LE(offset+8),method=bytes.readUInt16LE(offset+10),compressed=bytes.readUInt32LE(offset+20),expanded=bytes.readUInt32LE(offset+24),nameLength=bytes.readUInt16LE(offset+28),extraLength=bytes.readUInt16LE(offset+30),commentLength=bytes.readUInt16LE(offset+32),local=bytes.readUInt32LE(offset+42);
  if(offset+46+nameLength+extraLength+commentLength>end||!nameLength||local+30>directoryOffset)throw invalid();
  if(flags&1)throw new Error('암호화된 Excel 파일은 먼저 암호를 해제해주세요.');
  if(![0,8].includes(method)||compressed===0xffffffff||expanded===0xffffffff||local===0xffffffff)throw invalid();
  const rawName=bytes.subarray(offset+46,offset+46+nameLength),name=rawName.toString('utf8');
  if(/[\\\x00]/.test(name)||name.startsWith('/')||name.split('/').some(p=>p==='..'||p==='.')||names.has(name))throw invalid();names.add(name);
  if(expanded>16*1024*1024||total+expanded>64*1024*1024)throw new Error('Excel 압축 해제 크기가 너무 커요. 파일 내부 총 크기는 64MB, 항목 하나는 16MB 이하로 줄여주세요.');
  if(bytes.readUInt32LE(local)!==0x04034b50||bytes.readUInt16LE(local+8)!==method||(bytes.readUInt16LE(local+6)&1))throw invalid();
  const localNameLength=bytes.readUInt16LE(local+26),localExtraLength=bytes.readUInt16LE(local+28),start=local+30+localNameLength+localExtraLength,finish=start+compressed;
  if(start>directoryOffset||finish>directoryOffset||!bytes.subarray(local+30,local+30+localNameLength).equals(rawName))throw invalid();
  ranges.push({start:local,end:finish});
  let content:Buffer;try{content=method===0?bytes.subarray(start,finish):inflate(bytes.subarray(start,finish),{maxOutputLength:Math.min(16*1024*1024,64*1024*1024-total)});}catch{throw new Error('Excel 압축 내용이 손상됐거나 압축 해제 한도를 초과했어요.');}
  if(content.length!==expanded)throw invalid();let crc=0xffffffff;for(const byte of content)crc=crcTable[(crc^byte)&255]^(crc>>>8);if(((crc^0xffffffff)>>>0)!==bytes.readUInt32LE(offset+16))throw new Error('Excel 압축 파일의 내용 검증에 실패했어요. 파일을 다시 저장해주세요.');total+=content.length;
  if(/\.(xml|rels)$/i.test(name)&&/<!DOCTYPE|<!ENTITY/i.test(content.toString('utf8')))throw new Error('외부 엔터티가 포함된 Excel XML은 읽을 수 없어요. 일반 XLSX로 다시 저장해주세요.');
  if(/vbaProject\.bin$/i.test(name))throw new Error('매크로가 포함된 파일은 XLSX로 다시 저장해주세요.');
  offset+=46+nameLength+extraLength+commentLength;
 }
 if(offset!==end||!names.has('[Content_Types].xml')||!names.has('xl/workbook.xml'))throw invalid();
 ranges.sort((a,b)=>a.start-b.start);for(let i=1;i<ranges.length;i++)if(ranges[i].start<ranges[i-1].end)throw invalid();
 return {entries:count,expandedBytes:total};
}
export function validateXlsxArchive(bytes:Uint8Array):ArchiveStats {if(!(bytes instanceof Uint8Array))throw new Error('파일 내용은 바이트 데이터로 입력해주세요.');return inspectZip(Buffer.from(bytes.buffer,bytes.byteOffset,bytes.byteLength),inflateRawSync);}

// Receives only uploaded bytes / JSON and fixed, locally installed library paths.
// No filename/URL input is accepted by either library. Formula strings are data.
async function documentWorkerMain(workerData:any,parentPort:any,require:any,inspectArchive:any) {
 const {task,data,options,modulePath}=workerData,MAX_BYTES=8*1024*1024,MAX_ITEMS=5000,forbidden=new Set(['__proto__','prototype','constructor','__easyflowBinary']);
 const fail=(message:string)=>{const error=new Error(message) as Error&{easyflowDocument?:boolean};error.easyflowDocument=true;throw error;};
 const bounded=(value:any)=>{if(Buffer.byteLength(JSON.stringify(value),'utf8')>MAX_BYTES)fail('파일에서 읽은 데이터는 8MB 이하로 줄여주세요.');return value;};
 try {
  if(task==='pdf'){
   // Disallow ancillary CMap/font/wasm fetches. Only document-provided bytes are read.
   globalThis.fetch=async()=>{throw new Error('파일 변환에서는 외부 주소를 읽을 수 없어요.');};
   const {PDFParse}=require(modulePath),parser=new PDFParse({data:new Uint8Array(data),verbosity:0,isEvalSupported:false,useWorkerFetch:false,useWasm:false,useSystemFonts:false,enableXfa:false,disableFontFace:true,stopAtErrors:true,disableRange:true,disableStream:true,disableAutoFetch:true});
   try {
    const info=await parser.getInfo();if(info.total>500)fail('PDF는 한 번에 500페이지까지 읽을 수 있어요. 문서를 나눠주세요.');
    const count=options.maxPages||info.total,pages:string[]=[];let length=0;
    for(let number=1;number<=Math.min(count,info.total);number++){
     const result=await parser.getText({partial:[number],parseHyperlinks:false,pageJoiner:''});const page=result.pages[0]?.text||'';
     length+=Buffer.byteLength(page,'utf8');if(length>MAX_BYTES)fail('PDF에서 읽은 글자는 8MB 이하로 줄여주세요.');pages.push(page);
    }
    if(!pages.some(text=>text.trim()))fail('PDF에 추출할 텍스트가 없어요. 스캔·이미지 PDF는 OCR(문자 인식)이 먼저 필요해요.');
    parentPort.postMessage({ok:true,result:bounded({numpages:info.total,numrender:pages.length,text:options.joinPages===false?pages:pages.join('\n\n')})});
   } finally {await parser.destroy();}
  } else {
   const ExcelJS=require(modulePath),workbook=new ExcelJS.Workbook();
   const scalar=(value:any):any=>{
    if(value===null||value===undefined)return null;
    if(value instanceof Date)return value.toISOString();
    if(typeof value==='string'||typeof value==='boolean'||typeof value==='number')return value;
    if(value.formula!==undefined||value.sharedFormula!==undefined)return scalar(value.result);
    if(Array.isArray(value.richText))return value.richText.map((part:any)=>part.text||'').join('');
    if(value.hyperlink!==undefined)return String(value.text||'');
    if(value.error!==undefined)return String(value.error);
    fail('Excel 셀에 지원하지 않는 값이 있어요. 값으로 저장한 뒤 입력해주세요.');
   };
   if(task==='writeXlsx'){
    const rows=data as Json[],columns=[...new Set(rows.flatMap(row=>Object.keys(row)))];if(columns.length>1000)fail('Excel은 한 번에 1,000개 열까지 만들 수 있어요.');
    if(columns.some(name=>!name.trim()||forbidden.has(name)))fail('Excel 열 이름이 비어 있거나 사용할 수 없는 이름이에요.');
    const sheet=workbook.addWorksheet(options.sheetName||'Sheet');if(options.headerRow!==false&&columns.length)sheet.addRow(columns);
    for(const row of rows)sheet.addRow(columns.map(column=>{const value=row[column];if(value===null||value===undefined)return null;if(typeof value==='object')return JSON.stringify(value);if(typeof value==='number'&&!Number.isFinite(value))fail('Excel 숫자는 유한한 값이어야 해요.');if(!['string','number','boolean'].includes(typeof value))fail('Excel에는 JSON 데이터만 넣을 수 있어요.');return value;}));
    const bytes=await workbook.xlsx.writeBuffer({zip:{zlib:{level:options.compression===false?0:6}}});if(bytes.byteLength>MAX_BYTES)fail('Excel 파일은 8MB 이하로 줄여주세요.');parentPort.postMessage({ok:true,result:new Uint8Array(bytes)});
   } else {
    const bytes=Buffer.from(data);try{inspectArchive(bytes,require('node:zlib').inflateRawSync);}catch(error:any){error.easyflowDocument=true;throw error;}
    await workbook.xlsx.load(bytes,{ignoreNodes:['picture','drawing','sheetProtection','tableParts','conditionalFormatting','extLst','dataValidations','hyperlinks']});
    const sheet=options.sheetName?workbook.getWorksheet(options.sheetName):workbook.worksheets[0];if(!sheet)fail('읽을 Excel 시트를 찾을 수 없어요. 시트 이름을 확인해주세요.');
    let firstRow=1,lastRow=sheet.rowCount,firstCol=1,lastCol=sheet.columnCount;
    if(options.range){
     const match=/^([A-Z]{1,3})([1-9]\d{0,6})(?::([A-Z]{1,3})([1-9]\d{0,6}))?$/i.exec(options.range);if(!match)fail('Excel 범위는 A1:C20처럼 입력해주세요.');
     const column=(name:string)=>[...name.toUpperCase()].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0);
     firstRow=Number(match![2]);lastRow=Number(match![4]||match![2]);firstCol=column(match![1]);lastCol=column(match![3]||match![1]);
     if(lastRow<firstRow||lastCol<firstCol||lastRow>1048576||lastCol>16384)fail('Excel 범위의 시작과 끝을 확인해주세요.');
     lastRow=Math.min(lastRow,sheet.rowCount);lastCol=Math.min(lastCol,sheet.columnCount);
    }
    if(lastRow-firstRow+1>MAX_ITEMS+(options.headerRow===false?0:1)||lastCol-firstCol+1>1000)fail('Excel 읽기는 5,000개 데이터 행과 1,000개 열까지 지원해요. 범위를 지정하거나 파일을 나눠주세요.');
    if(lastRow<firstRow||lastCol<firstCol){parentPort.postMessage({ok:true,result:[]});return;}
    const headers:string[]=[];
    for(let col=firstCol;col<=lastCol;col++)headers.push(options.headerRow===false?'column'+(col-firstCol+1):String(scalar(sheet.getRow(firstRow).getCell(col).value)??''));
    if(headers.some(name=>!name.trim()||forbidden.has(name))||new Set(headers).size!==headers.length)fail('Excel 첫 행의 열 이름이 비어 있거나 중복돼요. 열 이름을 바꿔주세요.');
    const rows:Json[]=[];let size=2;
    for(let rowNumber=firstRow+(options.headerRow===false?0:1);rowNumber<=lastRow;rowNumber++){
     const row=sheet.getRow(rowNumber),values=headers.map((_,index)=>scalar(row.getCell(firstCol+index).value));if(values.every(value=>value===null))continue;
     const result=Object.fromEntries(headers.flatMap((name,index)=>values[index]===null&&!options.includeEmptyCells?[]:[[name,options.resultTypes==='strings'?(values[index]===null?'':String(values[index])):values[index]]]));
     size+=Buffer.byteLength(JSON.stringify(result),'utf8')+1;if(size>MAX_BYTES)fail('Excel에서 읽은 데이터는 8MB 이하로 줄여주세요.');rows.push(result);
    }
    parentPort.postMessage({ok:true,result:rows});
   }
  }
 }catch(error:any){
  const message=String(error?.message||'');let publicMessage='파일 내용을 읽지 못했어요. 파일 형식과 손상 여부를 확인해주세요.';
  if(error?.easyflowDocument===true)publicMessage=message;
  else if(error?.name==='PasswordException'||/password|encrypted/i.test(message))publicMessage='암호화된 PDF는 먼저 암호를 해제한 뒤 입력해주세요.';
  parentPort.postMessage({ok:false,error:publicMessage});
 }
}

async function runDocumentTask(task:'readXlsx'|'writeXlsx'|'pdf',data:Uint8Array|Json[],options:Json,context:DocumentContext):Promise<any> {
 if(context.signal?.aborted)throw new Error('파일 처리를 중지했어요.');
 const timeout=context.timeoutMs??DOCUMENT_TIMEOUT_MS;if(!Number.isInteger(timeout)||timeout<1||timeout>30000)throw new Error('파일 처리 제한 시간은 1~30,000ms로 지정해주세요.');
 const require=createRequire(import.meta.url);let modulePath:string;try{modulePath=require.resolve(task==='pdf'?'pdf-parse':'exceljs');}catch{throw new Error('파일 처리 라이브러리가 설치되지 않았어요. 서버 설정을 확인해주세요.');}
 // tsx/esbuild may preserve nested function names with their __name helper.
 const source=`const __name=(fn)=>fn;(${documentWorkerMain.toString()})(require('node:worker_threads').workerData,require('node:worker_threads').parentPort,require,(${inspectZip.toString()}));`;
 return new Promise((resolve,reject)=>{
  const worker=new Worker(source,{eval:true,workerData:{task,data,options,modulePath},execArgv:[],resourceLimits:{maxOldGenerationSizeMb:192,maxYoungGenerationSizeMb:32,stackSizeMb:4}});
  let settled=false;const finish=(error?:Error,result?:any)=>{if(settled)return;settled=true;clearTimeout(timer);context.signal?.removeEventListener('abort',abort);void worker.terminate();if(error)reject(error);else resolve(result);};
  const abort=()=>finish(new Error('파일 처리를 중지했어요.')),timer=setTimeout(()=>finish(new Error('파일 처리 시간이 초과됐어요. 문서를 나누거나 파일을 줄여주세요.')),timeout);
  context.signal?.addEventListener('abort',abort,{once:true});if(context.signal?.aborted)abort();
  worker.once('message',(message:any)=>message?.ok?finish(undefined,message.result):finish(new Error(message?.error||'파일을 읽지 못했어요.')));
  worker.once('error',()=>finish(new Error('파일 처리 중 메모리 또는 형식 오류가 발생했어요. 파일을 줄이거나 다시 저장해주세요.')));
  worker.once('exit',()=>{if(!settled)finish(new Error('파일 처리가 완료되기 전에 종료됐어요.'));});
 });
}
function bytesInput(bytes:Uint8Array):Uint8Array {if(!(bytes instanceof Uint8Array))throw new Error('파일 내용은 바이트 데이터로 입력해주세요.');if(bytes.byteLength>MAX_DOCUMENT_BYTES)throw new Error('파일은 8MB 이하로 입력해주세요.');return bytes;}
function xlsxOptions(options:XlsxOptions):XlsxOptions {
 if(options.sheetName!==undefined&&(typeof options.sheetName!=='string'||options.sheetName.length>31||/[\\/*?:\[\]\x00-\x1f]/.test(options.sheetName)))throw new Error('Excel 시트 이름은 특수문자 없이 31자 이하로 입력해주세요.');
 if(options.resultTypes!==undefined&&!['typed','strings'].includes(options.resultTypes))throw new Error('Excel 결과 값은 원래 종류 또는 문자열로 선택해주세요.');
 for(const key of ['headerRow','includeEmptyCells','compression'] as const)if(options[key]!==undefined&&typeof options[key]!=='boolean')throw new Error('Excel 옵션은 참 또는 거짓으로 선택해주세요.');
 if(options.range!==undefined&&(typeof options.range!=='string'||options.range.length>30))throw new Error('Excel 범위는 A1:C20처럼 입력해주세요.');
 return {...options};
}
export async function readXlsx(bytes:Uint8Array,options:XlsxOptions={},context:DocumentContext={}):Promise<Json[]> {return runDocumentTask('readXlsx',bytesInput(bytes),xlsxOptions(options),context);}
export async function writeXlsx(rows:Json[],options:XlsxOptions={},context:DocumentContext={}):Promise<Uint8Array> {
 if(!Array.isArray(rows)||rows.length>5000||rows.some(row=>!row||typeof row!=='object'||Array.isArray(row)||Object.hasOwn(row,'__easyflowBinary')))throw new Error('Excel에는 파일 메타데이터를 제외한 JSON 객체를 최대 5,000개 입력해주세요.');
 if(Buffer.byteLength(JSON.stringify(rows),'utf8')>MAX_DOCUMENT_BYTES)throw new Error('Excel로 만들 입력 데이터는 8MB 이하로 줄여주세요.');return runDocumentTask('writeXlsx',rows,xlsxOptions(options),context);
}
export async function extractPdfText(bytes:Uint8Array,options:PdfOptions={},context:DocumentContext={}):Promise<PdfText> {
 bytesInput(bytes);if(Buffer.from(bytes.buffer,bytes.byteOffset,Math.min(bytes.byteLength,1024)).indexOf('%PDF-')<0)throw new Error('PDF 파일 형식을 확인해주세요.');
 if(options.joinPages!==undefined&&typeof options.joinPages!=='boolean')throw new Error('PDF 페이지 합치기는 참 또는 거짓으로 선택해주세요.');
 if(options.maxPages!==undefined&&(!Number.isInteger(options.maxPages)||options.maxPages<0||options.maxPages>500))throw new Error('PDF 최대 페이지는 0~500으로 입력해주세요. 0은 전체 페이지예요.');
 return runDocumentTask('pdf',bytes,{joinPages:options.joinPages??true,maxPages:options.maxPages??0},context);
}
