import test from 'node:test';
import assert from 'node:assert/strict';
import {builderWorkerTimeout} from './agent-builder';

test('remote design budget includes multiple model turns and verifier without changing read requests',()=>{
 const env={A4B_MAIN_PROVIDER:'ollama',A4B_DESIGN_TIMEOUT_MS:'480000'};
 assert.equal(builderWorkerTimeout('message',env),480000);
 assert.equal(builderWorkerTimeout('session',env),180000);
 assert.equal(builderWorkerTimeout('message',{...env,A4B_MAIN_PROVIDER:'openai'}),180000);
 assert.equal(builderWorkerTimeout('test',{...env,A4B_WORKER_TIMEOUT_MS:'4000'}),4000);
});
test('invalid or unbounded timeout values retain a finite default',()=>{
 for(const value of ['0','-1','NaN','Infinity','999999999']){
  assert.equal(builderWorkerTimeout('message',{A4B_MAIN_PROVIDER:'ollama',A4B_DESIGN_TIMEOUT_MS:value}),600000);
 }
});
