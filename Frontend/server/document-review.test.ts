import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {renderToStaticMarkup} from 'react-dom/server';
import {createElement} from 'react';
import {parseDocumentReviewCommand,documentPreview,originalMime} from './document-review';
import {ReviewBlocks} from '../app/document-review-panel';
import type {ReviewableAsset} from '../lib/document-review';
import type {AnalysisBlock} from './document-analysis-contract';

test('review commands accept explicit choices and reject forged fields and malformed revisions',()=>{
 const valid={id:randomUUID(),requestId:randomUUID(),expectedRevision:0,sourceSha256:'a'.repeat(64),analysisSha256:'b'.repeat(64),action:'decide',groupId:'c'.repeat(24),decision:'hold',note:'원본 확인 중'};
 assert.equal(parseDocumentReviewCommand(valid).decision,'hold');
 for(const override of [{expectedRevision:true},{expectedRevision:-1},{requestId:'bad'},{decision:'auto'},{selected:true},{note:'x'.repeat(2001)},{action:'complete'}])
  assert.throws(()=>parseDocumentReviewCommand({...valid,...override}));
 const {groupId,decision,...document}=valid;
 assert.equal(parseDocumentReviewCommand({...document,action:'complete',acknowledge:true}).acknowledge,true);
 assert.equal(originalMime('png'),'image/png');assert.equal(originalMime('svg'),'application/octet-stream');
});

test('candidate text and table values render as escaped text with cell positions',()=>{
 const block={id:'candidate',engine:'docling',content_types:['table'],text:'',data:{cells:[{start_row_offset_idx:0,start_col_offset_idx:0,text:'<img src=x onerror=alert(1)>',row_span:1,col_span:2}]}} as unknown as AnalysisBlock;
 const html=renderToStaticMarkup(createElement(ReviewBlocks,{blocks:[block]}));
 assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<img src=x'));
 assert.ok(html.includes('행·열'));assert.ok(html.includes('셀 내용'));
});

test('preview serves only recorded objects within the asset job directory',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-document-preview-')),prior=process.env.EASYFLOW_DATA_DIR;
 process.env.EASYFLOW_DATA_DIR=directory;
 try{
  const relative='document-results/'+'a'.repeat(64)+'/'+randomUUID(),job=join(directory,relative);
  await mkdir(join(job,'images'),{recursive:true});await writeFile(join(job,'images/source.png'),'test-image');
  await writeFile(join(directory,'outside.png'),'outside');
  const asset={parser_metadata:{analysis_artifact_directory:relative,document_analysis:{blocks:[{id:'image',artifact:'images/source.png'}]}}} as unknown as ReviewableAsset;
  const preview=await documentPreview(asset,'image',null);
  assert.equal(preview.mime,'image/png');assert.equal(preview.bytes.toString(),'test-image');
  await assert.rejects(()=>documentPreview(asset,'missing',null));
  asset.parser_metadata.document_analysis.blocks[0].artifact='../../../outside.png';
  await assert.rejects(()=>documentPreview(asset,'image',null),/폴더 밖/);
 }finally{if(prior===undefined)delete process.env.EASYFLOW_DATA_DIR;else process.env.EASYFLOW_DATA_DIR=prior;await rm(directory,{recursive:true,force:true});}
});
