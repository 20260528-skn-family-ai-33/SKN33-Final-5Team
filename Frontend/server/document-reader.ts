import {backendPath,doclingPython} from './paths';
import {execFile} from 'node:child_process';import {promisify} from 'node:util';import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join,extname,basename} from 'node:path';
import type {FlowNode,Json} from '../lib/workflow';import {getBinary,decodeBinaryFile} from './binary';import {attachItemPairs} from './provenance';
const run=promisify(execFile);const allowed=new Set(['pdf','docx','dotx','xlsx','xlsm','pptx','ppsx','md','markdown','txt','html','htm','xhtml','csv','adoc','asciidoc','odt','ods','odp','epub','vtt','tex','latex','eml','msg','mhtml','mht','boxnote']);
export async function readDocument(node:FlowNode,input:Json[],signal?:AbortSignal):Promise<Json[][]>{
 if(input.length>10)throw new Error('문서 읽기는 한 번에 10개 이하로 입력해주세요.');const result:Json[]=[];
 for(const item of input){
  const property=node.parameters.binaryPropertyName||'data',file=getBinary(item,property);if(!file)throw new Error('실제 업로드 파일을 문서 읽기 단계로 연결해주세요.');
  const ext=extname(file.fileName).slice(1).toLowerCase();if(!allowed.has(ext))throw new Error('이 형식은 Docling 문서 읽기에서 지원하지 않아요. 스캔 PDF·이미지 OCR은 별도입니다.');
  const dir=await mkdtemp(join(tmpdir(),'easyflow-document-'));try{
   const source=join(dir,'input.'+ext),output=join(dir,'output.json');await writeFile(source,decodeBinaryFile(file));
   const python=doclingPython();if(!python)throw new Error('Docling 환경이 없습니다. Backend/scripts/setup-document-demo.sh로 설치하세요.');
   await run(python,[backendPath('scripts/document_worker.py'),source,output],{timeout:180000,maxBuffer:1024*1024,signal});
   const data=JSON.parse(await readFile(output,'utf8'));if(data.error||!data.text?.trim())throw new Error('문서에서 실제 본문을 추출하지 못했어요.');
   result.push({...item,text:data.text,filename:basename(file.fileName),document:{filename:basename(file.fileName),engine:'docling',sections:data.sections||[],tables:data.tables||[],warnings:data.warnings||[]}});
  }catch(e:any){if(e.message?.startsWith('문서에서'))throw e;throw new Error('Docling 문서 변환 실패: Python 환경·지원 형식·180초 제한을 확인해주세요.');}finally{await rm(dir,{recursive:true,force:true});}
 }
 return [attachItemPairs(result,result.map((_,i)=>[{item:i}]))];
}
