import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeBuilderDocument} from './builder-documents';
const payload=()=>({text:'실제 본문',pages:['실제 본문'],sections:[{text:'실제 본문',pages:[1]}],tables:[{text:'표',html:'<table></table>',pages:[1]}],engine:'docling',parser_version:'2.133.0',warnings:[],page_count:1,parser_status:'partial',parser_pipeline_version:'docling-structure-hybrid-v2',parser_code_sha256:{'parser_converter.py':'a'.repeat(64)},supplement_report:{added:1},lighton_report:{enabled:false}});
test('worker content and partial status retain parser provenance and supplement reports',()=>{
 const source=payload(),result=normalizeBuilderDocument(source);
 assert.equal(result.parser_status,'partial');assert.deepEqual(result.tables,source.tables);
 assert.deepEqual(result.parser_metadata.parser_code_sha256,source.parser_code_sha256);
 assert.deepEqual(result.parser_metadata.supplement_report,{added:1});
 assert.deepEqual(result.parser_metadata.lighton_report,{enabled:false});
 assert.equal(result.parser_metadata.text,undefined);
});
test('DB contract proposal cannot masquerade as completed worker output',()=>{
 assert.throws(()=>normalizeBuilderDocument({contract_version:'a4b.parser_result.v0.1-draft',canonical_text:'본문',blocks:[]}),/워커 출력 계약/);
 assert.throws(()=>normalizeBuilderDocument({...payload(),page_count:87}),/워커 출력 계약/);
 assert.throws(()=>normalizeBuilderDocument({...payload(),parser_status:'failed'}),/워커 출력 계약/);
 assert.throws(()=>normalizeBuilderDocument({error:'파서 실패'}),/파서 실패/);
});
