import {readFile,realpath,stat} from 'node:fs/promises';
import {extname,resolve,sep} from 'node:path';
import {dataDirectory} from './paths';
import type {ReviewableAsset} from '../lib/document-review';

export function parseDocumentReviewCommand(data:any){
 const allowed=new Set(['id','expectedRevision','requestId','sourceSha256','analysisSha256','action','groupId','decision','note','acknowledge']);
 if(!data||typeof data!=='object'||Array.isArray(data)||Object.keys(data).some(key=>!allowed.has(key))||
  !Number.isInteger(data.expectedRevision)||data.expectedRevision<0||
  typeof data.requestId!=='string'||!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(data.requestId)||
  typeof data.sourceSha256!=='string'||!/^[a-f0-9]{64}$/.test(data.sourceSha256)||
  typeof data.analysisSha256!=='string'||!/^[a-f0-9]{64}$/.test(data.analysisSha256)||
  !['decide','complete','reopen'].includes(data.action)||data.note!==undefined&&(typeof data.note!=='string'||data.note.length>2000)||
  data.acknowledge!==undefined&&typeof data.acknowledge!=='boolean')throw new Error('문서 검토 요청 형식을 확인하세요.');
 if(data.action==='decide'&&(typeof data.groupId!=='string'||!/^[a-f0-9]{24}$/.test(data.groupId)||!['adopt','keep','hold'].includes(data.decision)))
  throw new Error('검토할 후보와 결정을 확인하세요.');
 if(data.action!=='decide'&&(data.groupId!==undefined||data.decision!==undefined))throw new Error('완료·다시 열기에는 개별 후보 결정을 넣지 마세요.');
 return {expected_revision:data.expectedRevision,request_id:data.requestId,source_sha256:data.sourceSha256,analysis_sha256:data.analysisSha256,
  action:data.action,...(data.action==='decide'?{group_id:data.groupId,decision:data.decision}:{}),note:data.note||'',acknowledge:data.acknowledge===true};
}

export const originalMime=(format:string)=>({pdf:'application/pdf',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',bmp:'image/bmp'} as Record<string,string>)[format]||'application/octet-stream';

export async function documentPreview(asset:ReviewableAsset,blockId:string|null,converted:string|null){
 const analysis=asset.parser_metadata?.document_analysis;
 const directory=asset.parser_metadata?.analysis_artifact_directory;
 if(!analysis||typeof directory!=='string'||!/^document-results\/[a-f0-9]{64}\/[a-f0-9-]{36}$/.test(directory))throw new Error('문서 미리보기 기록이 없습니다.');
 let artifact:unknown;
 if(blockId&&converted===null){
  const block=analysis.blocks.find(b=>b.id===blockId);
  artifact=block?.artifact;
 }else if(!blockId&&converted==='0')artifact=analysis.conversions?.[0]?.artifact;
 if(typeof artifact!=='string'||!artifact||artifact.includes('\\'))throw new Error('미리보기 대상을 확인하세요.');
 const folder=await realpath(dataDirectory(directory)),path=await realpath(resolve(folder,artifact));
 if(!path.startsWith(folder+sep))throw new Error('문서 작업 폴더 밖의 파일은 열 수 없습니다.');
 const mime=originalMime(extname(path).slice(1).toLowerCase());
 if(mime==='application/octet-stream'||(await stat(path)).size>16*1024*1024)throw new Error('이 객체는 미리보기를 지원하지 않습니다. 원본을 내려받아 확인하세요.');
 return {bytes:await readFile(path),mime};
}
