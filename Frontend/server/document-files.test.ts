import test from 'node:test';
import assert from 'node:assert/strict';
import {deflateRawSync} from 'node:zlib';
import ExcelJS from 'exceljs';
import {MAX_DOCUMENT_BYTES,MAX_XLSX_EXPANDED_BYTES,validateXlsxArchive,readXlsx,writeXlsx,extractPdfText} from './document-files';

function pdf(pages:string[],imageOnly=false):Buffer {
 const objects:string[]=['<< /Type /Catalog /Pages 2 0 R >>','', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];const kids:number[]=[];
 for(const text of pages){const page=objects.length+1,stream=page+1;kids.push(page);objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents ${stream} 0 R >>`);const content=imageOnly?'q 20 0 0 20 10 10 cm BI /W 1 /H 1 /CS /RGB /BPC 8 /F /AHx ID ff0000> EI Q':`BT /F1 12 Tf 40 720 Td (${text.replace(/[\\()]/g,'\\$&')}) Tj ET`;objects.push(`<< /Length ${Buffer.byteLength(content)} >>\nstream\n${content}\nendstream`);}
 objects[1]=`<< /Type /Pages /Kids [${kids.map(id=>id+' 0 R').join(' ')}] /Count ${kids.length} >>`;
 let result='%PDF-1.7\n';const offsets=[0];objects.forEach((value,index)=>{offsets.push(Buffer.byteLength(result));result+=`${index+1} 0 obj\n${value}\nendobj\n`;});const xref=Buffer.byteLength(result);result+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`+offsets.slice(1).map(offset=>String(offset).padStart(10,'0')+' 00000 n \n').join('')+`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;return Buffer.from(result);
}
function crc32(data:Buffer):number {let crc=0xffffffff;for(const byte of data){crc^=byte;for(let bit=0;bit<8;bit++)crc=crc&1?0xedb88320^(crc>>>1):crc>>>1;}return (crc^0xffffffff)>>>0;}
function zip(entries:{name:string;content:Buffer;method?:number;declaredSize?:number;flags?:number}[]):Buffer {
 const local:Buffer[]=[],central:Buffer[]=[];let offset=0;
 for(const entry of entries){const name=Buffer.from(entry.name),method=entry.method??8,compressed=method===8?deflateRawSync(entry.content):entry.content,header=Buffer.alloc(30),directory=Buffer.alloc(46),size=entry.declaredSize??entry.content.length,crc=crc32(entry.content);header.writeUInt32LE(0x04034b50,0);header.writeUInt16LE(20,4);header.writeUInt16LE(entry.flags??0,6);header.writeUInt16LE(method,8);header.writeUInt32LE(crc,14);header.writeUInt32LE(compressed.length,18);header.writeUInt32LE(size,22);header.writeUInt16LE(name.length,26);directory.writeUInt32LE(0x02014b50,0);directory.writeUInt16LE(20,4);directory.writeUInt16LE(20,6);directory.writeUInt16LE(entry.flags??0,8);directory.writeUInt16LE(method,10);directory.writeUInt32LE(crc,16);directory.writeUInt32LE(compressed.length,20);directory.writeUInt32LE(size,24);directory.writeUInt16LE(name.length,28);directory.writeUInt32LE(offset,42);local.push(header,name,compressed);central.push(directory,name);offset+=header.length+name.length+compressed.length;}
 const directory=Buffer.concat(central),end=Buffer.alloc(22);end.writeUInt32LE(0x06054b50,0);end.writeUInt16LE(entries.length,8);end.writeUInt16LE(entries.length,10);end.writeUInt32LE(directory.length,12);end.writeUInt32LE(offset,16);return Buffer.concat([...local,directory,end]);
}
const basicEntries=()=>[{name:'[Content_Types].xml',content:Buffer.from('<Types/>')},{name:'xl/workbook.xml',content:Buffer.from('<workbook/>')}];
const workbookBytes=async(book:ExcelJS.Workbook)=>new Uint8Array(await book.xlsx.writeBuffer());

test('XLSX writes a real archive and semantically roundtrips Korean, identifiers, numeric/boolean and JSON cells',async()=>{
 const rows=[{이름:'민지',식별자:'001',점수:92,완료:true,메모:'쉼표, "인용"\n새 줄',세부:{department:'운영'},위험해보이는글:'=WEBSERVICE("https://example.invalid/")'},{이름:'지훈',식별자:'002',점수:68,완료:false,메모:'끝',세부:null,위험해보이는글:'+1+1'}];
 const bytes=await writeXlsx(rows,{sheetName:'업무 결과',headerRow:true});assert.equal(Buffer.from(bytes).readUInt32LE(0),0x04034b50);const stats=validateXlsxArchive(bytes);assert.ok(stats.entries>5);assert.ok(stats.expandedBytes<MAX_XLSX_EXPANDED_BYTES);
 const result=await readXlsx(bytes,{sheetName:'업무 결과',includeEmptyCells:true});assert.deepEqual(result,rows.map(row=>({...row,세부:row.세부===null?null:JSON.stringify(row.세부)})));
 const book=new ExcelJS.Workbook();await book.xlsx.load(Buffer.from(bytes) as any);assert.equal(book.worksheets[0].getCell('G2').type,ExcelJS.ValueType.String);assert.equal(book.worksheets[0].getCell('G2').formula,undefined);
});
test('XLSX reads only stored formula results, dates, rich text and errors without executing formulas',async()=>{
 const book=new ExcelJS.Workbook(),sheet=book.addWorksheet('검토');sheet.addRow(['number','boolean','date','rich','cached','uncached','error']);sheet.addRow([42,true,new Date('2026-10-07T00:00:00.000Z'),{richText:[{text:'한'},{text:'글'}]},{formula:'WEBSERVICE("https://example.invalid/")',result:'저장된 결과'},{formula:'1+1'},{error:'#N/A'}]);
 const rows=await readXlsx(await workbookBytes(book),{includeEmptyCells:true});assert.deepEqual(rows,[{number:42,boolean:true,date:'2026-10-07T00:00:00.000Z',rich:'한글',cached:'저장된 결과',uncached:null,error:'#N/A'}]);
 const strings=await readXlsx(await workbookBytes(book),{includeEmptyCells:true,resultTypes:'strings'});assert.equal(strings[0].number,'42');assert.equal(strings[0].boolean,'true');assert.equal(strings[0].uncached,'');
});
test('XLSX selects the named sheet and range, handles no header and optional empty cells',async()=>{
 const book=new ExcelJS.Workbook();book.addWorksheet('첫 시트').addRows([['a'],['ignored']]);const selected=book.addWorksheet('대상');selected.addRows([['ignore','이름','점수'],['x','민지',92],['x','지훈',null]]);
 const bytes=await workbookBytes(book);assert.deepEqual(await readXlsx(bytes,{sheetName:'대상',range:'B1:C3',includeEmptyCells:true}),[{이름:'민지',점수:92},{이름:'지훈',점수:null}]);assert.deepEqual(await readXlsx(bytes,{sheetName:'대상',range:'B2:C3',headerRow:false}),[{column1:'민지',column2:92},{column1:'지훈'}]);
 await assert.rejects(readXlsx(bytes,{sheetName:'없는 시트'}),/시트/);await assert.rejects(readXlsx(bytes,{range:'C3:A1'}),/시작과 끝/);await assert.rejects(readXlsx(bytes,{range:'file:\/\/etc\/passwd'}),/범위/);
});
test('XLSX rejects duplicate/reserved headers, excessive rows and invalid options',async()=>{
 for(const names of [['a','a'],['__proto__','x'],['','x']]){const book=new ExcelJS.Workbook();book.addWorksheet('Sheet').addRows([names,[1,2]]);await assert.rejects(readXlsx(await workbookBytes(book)),/열 이름/);}
 const large=new ExcelJS.Workbook();large.addWorksheet('Sheet').addRows([['a'],...Array.from({length:5001},(_,i)=>[i])]);const bytes=await workbookBytes(large);await assert.rejects(readXlsx(bytes),/5,000/);assert.equal((await readXlsx(bytes,{range:'A1:A5001'})).length,5000);
 await assert.rejects(writeXlsx(Array.from({length:5001},()=>({}))),/5,000/);await assert.rejects(writeXlsx([{}],{sheetName:'../bad'}),/시트 이름/);await assert.rejects(readXlsx(bytes,{resultTypes:'invalid' as any}),/결과 값/);await assert.rejects(writeXlsx([{a:1}],{headerRow:'true' as any}),/옵션/);
});
test('XLSX archive preflight bounds declared and actual expansion before parsing',async()=>{
 assert.throws(()=>validateXlsxArchive(Buffer.alloc(MAX_DOCUMENT_BYTES+1)),/8MB/);
 const oversized=zip([...basicEntries(),{name:'xl/sharedStrings.xml',content:Buffer.from('small'),declaredSize:16*1024*1024+1}]);assert.throws(()=>validateXlsxArchive(oversized),/압축 해제 크기/);
 const actualBomb=zip([...basicEntries(),{name:'xl/sharedStrings.xml',content:Buffer.alloc(16*1024*1024+1,32),declaredSize:1}]);assert.ok(actualBomb.length<100000);assert.throws(()=>validateXlsxArchive(actualBomb),/압축 해제 한도/);await assert.rejects(readXlsx(actualBomb),/압축 해제 한도/);
 const mismatch=zip([...basicEntries(),{name:'xl/a.xml',content:Buffer.from('<x/>'),declaredSize:1}]);assert.throws(()=>validateXlsxArchive(mismatch),/올바른 XLSX/);
});
test('XLSX rejects damaged checksums, traversal, duplicates, encryption and XML external entities',()=>{
 const valid=zip(basicEntries());assert.equal(validateXlsxArchive(valid).entries,2);
 const damaged=Buffer.from(valid);const central=damaged.indexOf(Buffer.from([0x50,0x4b,0x01,0x02]));damaged.writeUInt32LE(0,central+16);assert.throws(()=>validateXlsxArchive(damaged),/내용 검증/);
 for(const name of ['../secret','/etc/passwd','xl\\a.xml'])assert.throws(()=>validateXlsxArchive(zip([...basicEntries(),{name,content:Buffer.from('x')}])));
 assert.throws(()=>validateXlsxArchive(zip([...basicEntries(),basicEntries()[0]])));assert.throws(()=>validateXlsxArchive(zip([{...basicEntries()[0],flags:1},basicEntries()[1]])),/암호화/);
 assert.throws(()=>validateXlsxArchive(zip([...basicEntries(),{name:'xl/a.xml',content:Buffer.from('<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>')}])) ,/외부 엔터티/);
 assert.throws(()=>validateXlsxArchive(zip([...basicEntries(),{name:'xl/vbaProject.bin',content:Buffer.from('macro')}])) ,/매크로/);
 const zip64=Buffer.from(valid);zip64.writeUInt16LE(65535,zip64.length-12);assert.throws(()=>validateXlsxArchive(zip64),/ZIP64|올바른/);assert.throws(()=>validateXlsxArchive(Buffer.from('not a zip')));
});
test('PDF extracts actual text and real page counts, page arrays and maxPages',async()=>{
 const bytes=pdf(['Easyflow report page one','Second page contains actual text']);const result=await extractPdfText(bytes);assert.equal(result.numpages,2);assert.equal(result.numrender,2);assert.match(String(result.text),/Easyflow report page one/);assert.match(String(result.text),/Second page contains actual text/);assert.ok(!String(result.text).includes('-- 1 of'));
 const array=await extractPdfText(bytes,{joinPages:false});assert.equal(Array.isArray(array.text),true);assert.equal(array.text.length,2);assert.match(array.text[1],/Second page/);
 const first=await extractPdfText(bytes,{maxPages:1});assert.equal(first.numrender,1);assert.ok(!String(first.text).includes('Second page'));
});
test('PDF image-only / scan documents report missing OCR instead of a fake empty success',async()=>{
 await assert.rejects(extractPdfText(pdf([''],true)),/텍스트가 없.*OCR/);await assert.rejects(extractPdfText(pdf([''])),/OCR/);
});
test('PDF validates the actual format and options and keeps parser errors free of raw file contents',async()=>{
 await assert.rejects(extractPdfText(Buffer.from('private input not PDF')),/PDF 파일 형식/);await assert.rejects(extractPdfText(Buffer.from('%PDF-1.7\nprivate invalid payload')),error=>!String(error).includes('private invalid payload'));
 await assert.rejects(extractPdfText(pdf(['x']),{maxPages:501}),/최대 페이지/);await assert.rejects(extractPdfText(pdf(['x']),{joinPages:'true' as any}),/페이지 합치기/);await assert.rejects(extractPdfText(Buffer.alloc(MAX_DOCUMENT_BYTES+1)),/8MB/);
});
test('document workers terminate on timeout and cancellation, allowing the next parse to succeed',async()=>{
 const bytes=await writeXlsx([{a:'정상'}]);const start=Date.now();await assert.rejects(readXlsx(bytes,{}, {timeoutMs:1}),/시간이 초과/);assert.ok(Date.now()-start<1000);
 const controller=new AbortController(),pending=extractPdfText(pdf(['text']),{}, {signal:controller.signal});controller.abort();await assert.rejects(pending,/중지/);
 await assert.rejects(readXlsx(bytes,{}, {signal:controller.signal}),/중지/);await assert.rejects(readXlsx(bytes,{}, {timeoutMs:30001}),/제한 시간/);assert.deepEqual(await readXlsx(bytes),[{a:'정상'}]);
});
