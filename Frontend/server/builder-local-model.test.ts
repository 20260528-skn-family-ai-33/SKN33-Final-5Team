import test from 'node:test';
import assert from 'node:assert/strict';
import {localModelConnection,LOCAL_MODEL_ID} from './builder-local-model';

test('local model config is explicit, accepts served model names and exposes no key',()=>{
 const old={...process.env};
 try{
  delete process.env.A4B_MAIN_PROVIDER;assert.equal(localModelConnection(),null);
  process.env.A4B_MAIN_PROVIDER='ollama';process.env.A4B_MAIN_BASE_URL='http://127.0.0.1:11434/v1';
  process.env.A4B_MAIN_MODEL='qwen3:8b-q4_K_M';process.env.A4B_MAIN_API_KEY='must-not-appear';
  assert.equal(localModelConnection()!.credential.id,LOCAL_MODEL_ID);
  assert.ok(!JSON.stringify(localModelConnection()).includes('must-not-appear'));
  process.env.A4B_MAIN_PROVIDER='vllm';process.env.A4B_MAIN_MODEL='Qwen/Qwen3-8B';
  assert.equal(localModelConnection()!.model,'Qwen/Qwen3-8B');
  process.env.A4B_MAIN_BASE_URL='http://user:secret@127.0.0.1/v1';assert.throws(localModelConnection);
 }finally{for(const k of Object.keys(process.env))if(!(k in old))delete process.env[k];Object.assign(process.env,old);}
});

test('local model health distinguishes ready, missing model and unreachable without exposing keys',async()=>{
 const old={...process.env},original=globalThis.fetch;
 try{
  process.env.A4B_MAIN_PROVIDER='ollama';process.env.A4B_MAIN_BASE_URL='http://127.0.0.1:11434/v1';process.env.A4B_MAIN_MODEL='qwen-test';process.env.A4B_MAIN_API_KEY='hidden-health-key';
  const {localModelHealth}=await import('./builder-local-model');
  globalThis.fetch=(async()=>new Response(JSON.stringify({data:[{id:'qwen-test'}]}))) as any;
  assert.equal((await localModelHealth()).status,'ready');
  globalThis.fetch=(async()=>new Response(JSON.stringify({data:[]}))) as any;
  assert.equal((await localModelHealth()).status,'unavailable');
  globalThis.fetch=(async()=>{throw Error('hidden-health-key');}) as any;
  const result=await localModelHealth();assert.equal(result.status,'unavailable');assert.ok(!JSON.stringify(result).includes('hidden-health-key'));
 }finally{globalThis.fetch=original;for(const k of Object.keys(process.env))if(!(k in old))delete process.env[k];Object.assign(process.env,old);}
});
