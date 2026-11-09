import test from 'node:test';
import assert from 'node:assert/strict';
import {localModelConnection,modelHealthTarget,LOCAL_MODEL_ID} from '../src/builder-local-model';

test('health checks alias destinations and rejects ambiguous migration settings before probing',async()=>{
 const old={...process.env},original=globalThis.fetch;let calls=0;
 try{
  Object.assign(process.env,{A4B_MAIN_PROVIDER:'ollama',A4B_MAIN_BASE_URL:'https://fixture.proxy.runpod.net/v1',
   A4B_MAIN_MODEL:'final664',A4B_CAPABILITY_MODE:'enforce',A4B_CAPABILITY_MODEL:'previous105',
   A4B_OLLAMA_MODEL_ALIASES:'{"old4b":"final664"}'});
  let ids=['final664:latest','previous105:latest'];
  globalThis.fetch=(async()=>{calls++;return new Response(JSON.stringify({data:ids.map(id=>({id}))}));}) as typeof fetch;
  const {localModelHealth}=await import('../src/builder-local-model');
  assert.equal((await localModelHealth()).status,'ready');
  process.env.A4B_MAIN_MODEL='old4b:latest';
  assert.equal((await localModelHealth()).status,'ready','health must resolve the same :latest reference as inference');
  process.env.A4B_MAIN_MODEL='final664';
  process.env.A4B_OLLAMA_MODEL_ALIASES='{"old4b":"separate9b"}';
  const missing=await localModelHealth();
  assert.equal(missing.status,'unavailable');assert.match(missing.message,/이전 모델 호환.*separate9b/);
  ids.push('separate9b:latest');assert.equal((await localModelHealth()).status,'ready');
  for(const raw of ['[]','null','not json','{"old":null}','{"old":"bad model"}',
   '{"old":"new","old:latest":"different"}','{"old":"next","next":"final"}','{"old":"old:latest"}']){
   process.env.A4B_OLLAMA_MODEL_ALIASES=raw;const before=calls;
   const invalid=await localModelHealth();assert.equal(invalid.status,'unconfigured');
   assert.match(invalid.message,/A4B_OLLAMA_MODEL_ALIASES/);assert.equal(calls,before);
  }
 }finally{globalThis.fetch=original;for(const key of Object.keys(process.env))if(!(key in old))delete process.env[key];Object.assign(process.env,old);}
});

test('health requires the separate capability model only while capability checks are enforced',async()=>{
 const old={...process.env},original=globalThis.fetch;let calls=0;
 try{
  Object.assign(process.env,{A4B_MAIN_PROVIDER:'ollama',A4B_MAIN_BASE_URL:'https://fixture-11434.proxy.runpod.net/v1',
   A4B_MAIN_MODEL:'final664',A4B_CAPABILITY_MODE:'enforce',A4B_CAPABILITY_MODEL:'previous105'});
  let ids=['final664:latest'];
  globalThis.fetch=(async()=>{calls++;return new Response(JSON.stringify({data:ids.map(id=>({id}))}));}) as typeof fetch;
  const {localModelHealth}=await import('../src/builder-local-model');
  const missing=await localModelHealth();
  assert.equal(missing.status,'unavailable');assert.match(missing.message,/지원 판정 모델 \(previous105\)/);
  assert.doesNotMatch(missing.message,/설계 모델/);assert.equal(calls,1);
  ids.push('previous105:latest');assert.equal((await localModelHealth()).status,'ready');
  ids=['previous105:latest'];assert.match((await localModelHealth()).message,/설계 모델 \(final664\)/);
  ids=[];const both=await localModelHealth();assert.match(both.message,/설계 모델/);assert.match(both.message,/지원 판정 모델/);
  ids=['final664:latest'];process.env.A4B_CAPABILITY_MODE='off';assert.equal((await localModelHealth()).status,'ready');
  delete process.env.A4B_CAPABILITY_MODE;assert.equal((await localModelHealth()).status,'ready');
  process.env.A4B_CAPABILITY_MODE='enforce';process.env.A4B_CAPABILITY_MODEL='';assert.equal((await localModelHealth()).status,'ready');
  delete process.env.A4B_CAPABILITY_MODEL;assert.equal((await localModelHealth()).status,'ready');
  process.env.A4B_CAPABILITY_MODEL='invalid model';const before=calls;
  assert.equal((await localModelHealth()).status,'unconfigured');assert.equal(calls,before);
  process.env.A4B_CAPABILITY_MODEL='previous105:other';ids.push('previous105:latest');
  assert.equal((await localModelHealth()).status,'unavailable');
 }finally{globalThis.fetch=original;for(const k of Object.keys(process.env))if(!(k in old))delete process.env[k];Object.assign(process.env,old);}
});

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

test('only the shared HTTPS RunPod endpoint can make model health ready',async()=>{
 const old={...process.env},original=globalThis.fetch;let calls=0;
 try{
  process.env.A4B_MAIN_PROVIDER='ollama';process.env.A4B_MAIN_MODEL='qwen-test';
  globalThis.fetch=(async()=>{calls++;return new Response(JSON.stringify({data:[{id:'qwen-test'}]}));}) as any;
  const {localModelHealth}=await import('../src/builder-local-model');
  for(const url of ['http://127.0.0.1:11434/v1','http://localhost:11434/v1','http://[::1]:11434/v1','https://models.example.com/v1',
    'http://pod-11434.proxy.runpod.net/v1','https://pod-11434.proxy.runpod.net.example.com/v1','https://fakeproxy.runpod.net/v1']){
   process.env.A4B_MAIN_BASE_URL=url;assert.notEqual((await localModelHealth()).status,'ready');
  }
  assert.equal(calls,0,'local/other endpoints are never probed for the shared service badge');
  delete process.env.A4B_MAIN_PROVIDER;assert.equal((await localModelHealth()).target,'unconfigured');
  assert.equal(modelHealthTarget('https://api.runpod.ai/v2/endpoint/openai/v1'),'runpod');
 }finally{globalThis.fetch=original;for(const k of Object.keys(process.env))if(!(k in old))delete process.env[k];Object.assign(process.env,old);}
});

test('RunPod health distinguishes ready, missing model and unreachable without exposing keys',async()=>{
 const old={...process.env},original=globalThis.fetch;
 try{
  process.env.A4B_MAIN_PROVIDER='ollama';process.env.A4B_MAIN_BASE_URL='https://fixture-11434.proxy.runpod.net/v1';process.env.A4B_MAIN_MODEL='qwen-test';process.env.A4B_MAIN_API_KEY='hidden-health-key';
  const {localModelHealth}=await import('../src/builder-local-model');
  globalThis.fetch=(async(url:any,options:RequestInit)=>{
   assert.equal(url,'https://fixture-11434.proxy.runpod.net/v1/models');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');
   assert.equal((options.headers as Record<string,string>).Authorization,'Bearer hidden-health-key');
   return new Response(JSON.stringify({data:[{id:'qwen-test'}]}));
  }) as any;
  const ready=await localModelHealth();assert.equal(ready.status,'ready');assert.equal(ready.target,'runpod');assert.ok(ready.checked_at);
  globalThis.fetch=(async()=>new Response(JSON.stringify({data:[{id:'qwen-test:latest'}]}))) as any;
  assert.equal((await localModelHealth()).status,'ready');
  process.env.A4B_MAIN_MODEL='qwen-test:other';
  assert.equal((await localModelHealth()).status,'unavailable');
  process.env.A4B_MAIN_MODEL='qwen-test';process.env.A4B_MAIN_PROVIDER='vllm';
  assert.equal((await localModelHealth()).status,'unavailable');
  process.env.A4B_MAIN_PROVIDER='ollama';process.env.A4B_MAIN_MODEL='registry.test:443/team/qwen-test';
  globalThis.fetch=(async()=>new Response(JSON.stringify({data:[{id:'registry.test:443/team/qwen-test:latest'}]}))) as any;
  assert.equal((await localModelHealth()).status,'ready');
  globalThis.fetch=(async()=>new Response(JSON.stringify({data:[]}))) as any;
  assert.equal((await localModelHealth()).status,'unavailable');
  globalThis.fetch=(async()=>new Response('{}',{status:401})) as any;
  assert.equal((await localModelHealth()).status,'unavailable');
  globalThis.fetch=(async()=>new Response('<html>Pod is not running</html>')) as any;
  assert.equal((await localModelHealth()).status,'unavailable');
  globalThis.fetch=(async()=>{throw Error('hidden-health-key');}) as any;
  const result=await localModelHealth();assert.equal(result.status,'unavailable');assert.ok(!JSON.stringify(result).includes('hidden-health-key'));
 }finally{globalThis.fetch=original;for(const k of Object.keys(process.env))if(!(k in old))delete process.env[k];Object.assign(process.env,old);}
});
