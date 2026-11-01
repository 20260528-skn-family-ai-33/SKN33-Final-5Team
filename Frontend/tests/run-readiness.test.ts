import {test} from 'node:test';
import assert from 'node:assert/strict';
import {missingStartFiles} from '../lib/run-readiness';
import {makeFlow} from '../lib/workflow';
function example(){const flow=makeFlow();flow.nodes=[{id:'start',name:'시작',type:'n8n-nodes-base.manualTrigger',parameters:{},position:[0,0],typeVersion:1},{id:'read',name:'문서 읽기',type:'easyflow.documentRead',parameters:{binaryPropertyName:'data'},position:[200,0],typeVersion:1}];flow.connections={'시작':{main:[[{node:'문서 읽기',type:'main',index:0}]]}};return flow;}
test('missing required upload is identified before execution',()=>{assert.equal(missingStartFiles(example(),[{}]).length,1);});
test('uploaded required binary satisfies start input',()=>{assert.deepEqual(missingStartFiles(example(),[{__easyflowBinary:{data:{fileName:'test.txt'}}}]),[]);});
test('different binary field does not satisfy requirement',()=>{assert.equal(missingStartFiles(example(),[{__easyflowBinary:{other:{}}}]).length,1);});
test('upstream file generation does not require upload',()=>{const flow=example();flow.nodes[0].type='n8n-nodes-base.convertToFile';assert.deepEqual(missingStartFiles(flow,[{}]),[]);});
