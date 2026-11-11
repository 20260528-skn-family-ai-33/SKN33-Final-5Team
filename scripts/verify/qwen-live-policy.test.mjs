import test from 'node:test';
import assert from 'node:assert/strict';
import {endpointPolicy} from './qwen-live-policy.mjs';
import {mkdtempSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
const local='http://127.0.0.1:11434/v1';
test('local preflight requires an exact explicit endpoint',()=>{
 assert.throws(()=>endpointPolicy(local));
 assert.throws(()=>endpointPolicy(local,'http://127.0.0.1:11435/v1'));
 assert.equal(endpointPolicy(local,local).kind,'local');
 assert.equal(endpointPolicy('https://example.proxy.runpod.net/v1').kind,'runpod');
});
test('preflight never substitutes an unselected default model',()=>{
 const directory=mkdtempSync(join(tmpdir(),'qwen-policy-'));
 const output=join(directory,'report.json');
 const env={...process.env,A4B_ENV_FILE:join(directory,'absent.env'),A4B_LLM_BASE_URL:local,A4B_LLM_MODEL:'',A4B_LLM_PROVIDER:'ollama'};
 delete env.A4B_MAIN_MODEL;delete env.A4B_MAIN_BASE_URL;delete env.A4B_MAIN_PROVIDER;
 const result=spawnSync(process.execPath,['scripts/verify/qwen-live-preflight.mjs','--output',output,'--allow-local-endpoint',local],{env,encoding:'utf8'});
 assert.equal(result.status,0);
 const report=JSON.parse(readFileSync(output,'utf8'));
 assert.equal(report.status,'blocked_configuration');
 assert.equal(report.inference_calls,0);assert.deepEqual(report.probes,[]);
 assert.equal(report.configured.A4B_MAIN_MODEL,'');
});
test('preflight blocks credentials, queries, private hosts and non-model base paths',()=>{
 for(const url of ['http://localhost:11434/v1','http://192.168.1.1:11434/v1',local+'?x=1',local+'#x',
  'http://user@127.0.0.1:11434/v1','http://127.0.0.1:11434/other','https://example.proxy.runpod.net.evil.invalid/v1'])
  assert.throws(()=>endpointPolicy(url,url));
});
