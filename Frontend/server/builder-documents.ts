import {backendPath,doclingPython} from './paths';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {existsSync} from 'node:fs';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const run=promisify(execFile);
export interface BuilderParsedDocument {
 text:string;pages:string[];sections:unknown[];tables:unknown[];engine:string;parser_version:string;
 warnings:string[];page_count:number;parser_status:'succeeded'|'partial';parser_metadata:Record<string,unknown>;
}
export function normalizeBuilderDocument(data:unknown):BuilderParsedDocument {
 if(!data||typeof data!=='object'||Array.isArray(data))throw new Error('파서 응답 형식을 확인하세요.');
 const value=data as Record<string,any>;
 if(value.error)throw new Error(typeof value.error==='string'?value.error:'문서 파싱에 실패했습니다.');
 // The DB proposal has canonical_text/blocks; it is not this worker's output.
 if(typeof value.text!=='string'||!value.text.trim()||value.text.length>1000000||
    !Array.isArray(value.pages)||!value.pages.length||value.pages.some((p:unknown)=>typeof p!=='string')||
    !Array.isArray(value.sections)||!Array.isArray(value.tables)||!Array.isArray(value.warnings)||
    value.warnings.some((w:unknown)=>typeof w!=='string')||!Number.isInteger(value.page_count)||value.page_count!==value.pages.length||
    typeof value.engine!=='string'||typeof value.parser_version!=='string'||!['succeeded','partial'].includes(value.parser_status))
  throw new Error('파서 워커 출력 계약이 올바르지 않습니다. DB 계약 예시와 워커 응답을 구분하세요.');
 const content=new Set(['text','pages','sections','tables','engine','parser_version','warnings','page_count','parser_status']);
 const metadata=Object.fromEntries(Object.entries(value).filter(([key])=>!content.has(key)));
 return {text:value.text,pages:value.pages,sections:value.sections,tables:value.tables,engine:value.engine,
  parser_version:value.parser_version,warnings:value.warnings,page_count:value.page_count,parser_status:value.parser_status,parser_metadata:metadata};
}
export async function convertBuilderPdf(bytes:Uint8Array):Promise<BuilderParsedDocument>{
 const python=doclingPython();if(!python||!existsSync(python))throw new Error('Docling 환경이 없습니다. Backend/scripts/setup-document-demo.sh로 설치하세요.');
 const configured=Number(process.env.A4B_PDF_TIMEOUT_MS);
 const timeout=Number.isFinite(configured)&&configured>0&&configured<=1800000?configured:300000;
 const dir=await mkdtemp(join(tmpdir(),'a4b-builder-document-'));
 try{
  const source=join(dir,'input.pdf'),output=join(dir,'output.json');await writeFile(source,bytes);
  try{await run(python,[backendPath('scripts/builder_document_worker.py'),source,output],{timeout,maxBuffer:1024*1024,env:{...process.env,HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1'}});}catch{
   if(existsSync(output)){const failed=JSON.parse(await readFile(output,'utf8'));if(failed.error)throw new Error(failed.error);}
   throw new Error('Docling 문서 변환을 완료하지 못했습니다. OCR·표 모델 설치와 A4B_PDF_TIMEOUT_MS 제한을 확인하세요.');
  }
  return normalizeBuilderDocument(JSON.parse(await readFile(output,'utf8')));
 }finally{await rm(dir,{recursive:true,force:true});}
}
