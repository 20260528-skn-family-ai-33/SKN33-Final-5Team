import {examplePath} from './paths';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeFlow} from '../lib/workflow';
import {executeFlow} from './engine';
import {createBinaryFile,decodeBinaryFile,getBinary,parseCsv,withBinary} from './binary';
import {readXlsx} from './document-files';

const credential=()=>{throw new Error('이 예제는 연결 계정을 사용하지 않아야 해요.');};
test('beginner data-cleaning template recovers exact original names after duplicates and ordering change',async()=>{
 const result=await executeFlow(makeFlow('clean-top-data'),[{}],{mode:'learning',credential});assert.equal(result.status,'success',result.error);
 const recovered=result.runs.find(run=>run.name==='원본 이름 다시 가져오기')!;
 assert.deepEqual(recovered.output[0],[{name:'예시 고객 A',score:92},{name:'예시 고객 C',score:84}]);
 assert.deepEqual(result.output,[{data:[{name:'예시 고객 A',score:92},{name:'예시 고객 C',score:84}]}]);
 assert.equal(JSON.stringify(result.output).includes('pairedItem'),false);
});
test('beginner Merge template matches differently named IDs and reuses earlier data in a real CSV',async()=>{
 const result=await executeFlow(makeFlow('merge-customers'),[{}],{mode:'learning',credential});assert.equal(result.status,'success',result.error);
 assert.equal(result.runs.length,6);const merged=result.runs.find(run=>run.name==='두 흐름 합치기')!;
 assert.deepEqual(merged.output[0].map(item=>[item.customerId,item.total]),[[101,30000],[102,45000]]);
 const rows=parseCsv(decodeBinaryFile(getBinary(result.runs.at(-1)!.output[0][0])).toString('utf8').replace(/^\uFEFF/,''));
 assert.deepEqual(rows,[{customerId:'101',name:'예시 고객 A',total:'30000',sourceCount:'2'},{customerId:'102',name:'예시 고객 B',total:'45000',sourceCount:'2'}]);
});
test('beginner loop template actually processes every example once and creates a downloadable CSV',async()=>{
 const result=await executeFlow(makeFlow('loop-items'),[{}],{mode:'learning',credential});assert.equal(result.status,'success',result.error);
 const body=result.runs.filter(r=>r.name==='처리 표시 붙이기');assert.equal(body.length,3);assert.deepEqual(body.map(r=>r.runIndex),[0,1,2]);assert.equal(new Set(body.map(r=>r.executionKey)).size,3);
 const rows=parseCsv(decodeBinaryFile(getBinary(result.runs.at(-1)!.output[0][0])).toString('utf8').replace(/^\uFEFF/,''));assert.equal(rows.length,3);assert.deepEqual(rows.map(r=>r.status),['확인 완료','확인 완료','확인 완료']);assert.deepEqual(rows.map(r=>r.score),['92','68','84']);
});
test('beginner CSV template casts imported scores before filtering and writes only matching real rows',async()=>{
 const input=[withBinary({},'data',createBinaryFile(Buffer.from('name,score\n예시 고객 A,92\n예시 고객 B,68\n예시 고객 C,84\n'),{fileName:'입력.csv',mimeType:'text/csv'}))];
 const result=await executeFlow(makeFlow('csv-filter'),input,{mode:'learning',credential});assert.equal(result.status,'success',result.error);
 const rows=parseCsv(decodeBinaryFile(getBinary(result.runs.at(-1)!.output[0][0])).toString('utf8').replace(/^\uFEFF/,''));assert.deepEqual(rows,[{name:'예시 고객 A',score:'92'},{name:'예시 고객 C',score:'84'}]);
});

test('beginner XLSX template imports actual typed rows and creates an Excel file with only matching rows',async()=>{
 const bytes=await readFile(examplePath('초보자_Excel_예제.xlsx'));
 const input=[withBinary({},'data',createBinaryFile(bytes,{fileName:'입력.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}))];
 const result=await executeFlow(makeFlow('xlsx-filter'),input,{mode:'learning',credential});assert.equal(result.status,'success',result.error);
 assert.equal(result.runs.length,4);assert.equal(result.runs.some(run=>run.name==='사용 방법'),false);
 const output=getBinary(result.runs.at(-1)!.output[0][0]);assert.equal(output.fileName,'80점_이상.xlsx');
 assert.deepEqual(await readXlsx(decodeBinaryFile(output),{sheetName:'결과'}),[{name:'예시 고객 A',score:92},{name:'예시 고객 C',score:84}]);
});

test('beginner PDF template extracts real page text and creates a UTF-8 download without executing its annotation',async()=>{
 const bytes=await readFile(examplePath('초보자_PDF_예제.pdf'));
 const input=[withBinary({},'data',createBinaryFile(bytes,{fileName:'입력.pdf',mimeType:'application/pdf'}))];
 const result=await executeFlow(makeFlow('pdf-text'),input,{mode:'learning',credential});assert.equal(result.status,'success',result.error);
 assert.equal(result.runs.length,3);assert.equal(result.runs[1].output[0][0].numpages,2);
 const text=decodeBinaryFile(getBinary(result.runs.at(-1)!.output[0][0])).toString('utf8');assert.match(text,/Easyflow report page one/);assert.match(text,/Second page contains actual text/);
});
