import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {parseBuilderAsset} from './agent-builder';
import {analyzeBuilderDocument,validateDocumentAnalysis} from './document-analysis';
import {renderToStaticMarkup} from 'react-dom/server';
import {createElement} from 'react';
import {DocumentAnalysisSummary} from '../app/document-analysis-summary';

test('native analysis preserves quoted CSV fields and shares the existing table view',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'a4b-routing-test-'));
 const prior=process.env.EASYFLOW_DATA_DIR;process.env.EASYFLOW_DATA_DIR=dir;
 try{
  const source='code,note\n000017,"line1\nline2"\n';
  const {asset,rows}=await parseBuilderAsset({fileName:'data.csv',data:Buffer.from(source).toString('base64')});
  assert.equal(rows[0].code,'000017');assert.equal(rows[0].note,'line1\nline2');
  assert.deepEqual(asset.capabilities,['table','document']);
  const analysis=(asset as typeof asset&{parser_metadata:Record<string,any>}).parser_metadata;
  assert.ok(validateDocumentAnalysis(analysis.document_analysis));
  assert.equal(analysis.document_analysis.blocks[1].native_location.logical_row,2);
  assert.deepEqual(analysis.document_analysis.blocks[1].data.fields,['000017','line1\nline2']);
  assert.deepEqual(analysis.document_analysis.routes,[]);
  assert.equal(await readFile(join(dir,analysis.analysis_artifact_directory,'input.csv'),'utf8'),source);
  const invalid=structuredClone(analysis.document_analysis);invalid.blocks[0].parent_object_id=invalid.blocks[0].id;
  assert.equal(validateDocumentAnalysis(invalid),false);
  const badLocation=structuredClone(analysis.document_analysis);badLocation.blocks[0].unit_id='invented-page';
  assert.equal(validateDocumentAnalysis(badLocation),false);
  const badUnit={...structuredClone(analysis.document_analysis),units:[null]};
  assert.equal(validateDocumentAnalysis(badUnit),false);
  const html=renderToStaticMarkup(createElement(DocumentAnalysisSummary,{analysis:analysis.document_analysis}));
  assert.match(html,/문서 분석 완료/);assert.match(html,/처리 경로 확인/);
 }finally{if(prior===undefined)delete process.env.EASYFLOW_DATA_DIR;else process.env.EASYFLOW_DATA_DIR=prior;await rm(dir,{recursive:true,force:true});}
});

test('missing image model returns a reviewable image asset, never a completed OCR claim',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'a4b-image-routing-test-'));
 const previous={data:process.env.EASYFLOW_DATA_DIR,paddle:process.env.A4B_PADDLE_PYTHON};
 process.env.EASYFLOW_DATA_DIR=dir;process.env.A4B_PADDLE_PYTHON=join(dir,'missing-python');
 try{
  const picture=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
  const parsed=await analyzeBuilderDocument(picture,'png');
  const analysis=parsed.parser_metadata.document_analysis as any;
  assert.equal(parsed.parser_status,'partial');assert.equal(parsed.text,'');
  assert.equal(analysis.review_required,true);
  assert.ok(analysis.review_reasons.includes('image_classification:dependency_missing'));
  assert.equal(analysis.routes[0].processor,'layout');assert.equal(analysis.routes[0].status,'failed');
  assert.equal(analysis.blocks[0].content_types[0],'image');
  const html=renderToStaticMarkup(createElement(DocumentAnalysisSummary,{analysis}));
  assert.match(html,/원본 검토 필요/);assert.match(html,/로컬 모델이 준비되지/);
 }finally{
  for(const [key,value] of [['EASYFLOW_DATA_DIR',previous.data],['A4B_PADDLE_PYTHON',previous.paddle]]){if(value===undefined)delete process.env[key!];else process.env[key!]=value;}
  await rm(dir,{recursive:true,force:true});
 }
});
