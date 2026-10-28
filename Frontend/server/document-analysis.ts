import {spawn} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {createWriteStream} from 'node:fs';
import {mkdir,writeFile,readFile,stat} from 'node:fs/promises';
import {join,sep} from 'node:path';
import {backendPath,dataDirectory,documentPython,PROJECT_ROOT} from './paths';
import {normalizeBuilderDocument,type BuilderParsedDocument} from './builder-documents';

import {DOCUMENT_FORMATS,validateDocumentAnalysis} from './document-analysis-contract';
export {DOCUMENT_FORMATS,routingEnabled,validateDocumentAnalysis} from './document-analysis-contract';
export type {DocumentAnalysis,AnalysisBlock} from './document-analysis-contract';

export async function analyzeBuilderDocument(bytes:Uint8Array,format:string):Promise<BuilderParsedDocument>{
 if(!DOCUMENT_FORMATS.has(format))throw new Error('지원하지 않는 문서 분석 형식입니다.');
 const hash=createHash('sha256').update(bytes).digest('hex');
 const relative=join('document-results',hash,randomUUID()),dir=dataDirectory(relative);
 await mkdir(dir,{recursive:true});
 const source=join(dir,'input.'+format),output=join(dir,'result.json');
 await writeFile(source,bytes);
 const configured=Number(process.env.A4B_DOCUMENT_TIMEOUT_MS||process.env.A4B_PDF_TIMEOUT_MS);
 const timeout=Number.isFinite(configured)&&configured>0&&configured<=1800000?configured:300000;
 const log=createWriteStream(join(dir,'analysis.log'));
 try{
  await new Promise<void>((accept,reject)=>{
   const child=spawn(documentPython(),[backendPath('scripts/analyze_document.py'),source,output,'--work-dir',dir],{
    cwd:PROJECT_ROOT,detached:process.platform!=='win32',stdio:['ignore','pipe','pipe'],env:{...process.env,HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1'}});
   let settled=false;
   const finish=(error?:Error)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):accept();};
   const timer=setTimeout(()=>{
    if(process.platform!=='win32'&&child.pid){try{process.kill(-child.pid,'SIGKILL');}catch{child.kill('SIGKILL');}}
    else child.kill('SIGKILL');
    finish(new Error('문서 분석 시간이 초과됐습니다. 처리 기록과 A4B_DOCUMENT_TIMEOUT_MS를 확인하세요.'));
   },timeout);
   child.stdout.pipe(log,{end:false});child.stderr.pipe(log,{end:false});
   child.on('error',()=>finish(new Error('문서 분석 Python 환경을 시작할 수 없습니다. setup-document-analysis.sh로 설치하세요.')));
   child.on('close',()=>finish()); // The output file contains structured failure details.
  });
  if((await stat(output)).size>12*1024*1024)throw new Error('문서 분석 결과 크기 제한을 넘었습니다.');
  const raw=JSON.parse(await readFile(output,'utf8'));
  if(raw.error)throw new Error(raw.error);
  if(!validateDocumentAnalysis(raw.document_analysis)||raw.document_analysis.source_sha256!==hash||raw.document_analysis.source_format!==format)
   throw new Error('문서 분석 결과의 계약 또는 원본 해시가 올바르지 않습니다.');
  const result=normalizeBuilderDocument(raw);
  result.parser_metadata.analysis_artifact_directory=relative.split(sep).join('/');
  return result;
 }finally{log.end();}
}
