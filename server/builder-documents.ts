import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {existsSync} from 'node:fs';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
const run=promisify(execFile);
export async function convertBuilderPdf(bytes:Uint8Array){
 const candidates=[process.env.EASYFLOW_DOCLING_PYTHON,resolve('.venv-docling/bin/python'),resolve('../.venv-docling/bin/python')].filter((p):p is string=>!!p);
 const python=candidates.find(p=>existsSync(p));if(!python)throw new Error('Docling 환경이 없습니다. setup-document-demo.sh로 설치하세요.');
 const dir=await mkdtemp(join(tmpdir(),'a4b-builder-document-'));
 try{
  const source=join(dir,'input.pdf'),output=join(dir,'output.json');await writeFile(source,bytes);
  try{await run(python,[resolve('python/builder_document_worker.py'),source,output],{timeout:180000,maxBuffer:1024*1024,env:{...process.env,HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1'}});}catch{if(existsSync(output)){const failed=JSON.parse(await readFile(output,'utf8'));if(failed.error)throw new Error(failed.error);}throw new Error('Docling 문서 변환을 완료하지 못했습니다. 실행 환경·180초 제한을 확인하세요.');}
  const data=JSON.parse(await readFile(output,'utf8'));if(data.error)throw new Error(data.error);return data as {text:string;pages:string[];sections:unknown[];tables:unknown[];engine:string;parser_version:string;warnings:string[];page_count:number};
 }finally{await rm(dir,{recursive:true,force:true});}
}
