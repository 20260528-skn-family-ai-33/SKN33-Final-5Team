import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {applyEvaluationProfile} from './qwen-live-profiles.mjs';

test('public 9B profile replaces private routing with the selected local public model',()=>{
 const inherited={A4B_MAIN_MODEL:'a4b-qwen35-9b-final664-pilot32k',
  A4B_CAPABILITY_MODEL:'a4b-qwen35-9b-previous-pilot32k',A4B_MAIN_API_KEY:'synthetic-secret',
  A4B_MAIN_BASE_URL:'https://example.proxy.runpod.net/v1',
  A4B_OLLAMA_MODEL_ALIASES:'{"old":"private"}',A4B_PLANNER_MODE:'direct',A4B_CONDITION_MODE:'off'};
 const child={...inherited};applyEvaluationProfile('local-public-9b-deepagent',child,process.cwd());
 assert.equal(child.A4B_MAIN_MODEL,'a4b-public-qwen35-9b-32k');
 assert.equal(child.A4B_CAPABILITY_MODEL,child.A4B_MAIN_MODEL);
 assert.equal(child.A4B_MAIN_BASE_URL,'http://127.0.0.1:11434/v1');
 assert.equal(child.A4B_MAIN_PROVIDER,'ollama');assert.equal(child.A4B_MAIN_API_KEY,'');
 assert.equal(child.A4B_OLLAMA_MODEL_ALIASES,undefined);
 assert.equal(child.A4B_PLANNER_MODE,'deepagent');
 for(const role of ['CAPABILITY','CONDITION','REQUIREMENT'])assert.equal(child[`A4B_${role}_MODE`],'enforce');
 assert.equal(child.A4B_OLLAMA_NUM_CTX,'32768');assert.equal(child.A4B_OLLAMA_NUM_PREDICT,'4096');
 assert.equal(child.A4B_OLLAMA_TEMPERATURE,'0');assert.equal(child.A4B_MAIN_TIMEOUT_S,'180');
 assert.equal(child.A4B_TOOL_ADVICE_MODE,'hint');assert.equal(child.A4B_TOOL_ADVICE_BACKEND,'svm56');
 assert.equal(child.A4B_VERIFIER_MODE,'off');assert.equal(child.A4B_REQUIREMENT_GENERATION_MODE,'baseline');
 assert.equal(inherited.A4B_MAIN_API_KEY,'synthetic-secret');
 assert.equal(inherited.A4B_MAIN_MODEL,'a4b-qwen35-9b-final664-pilot32k');
});

test('documented private profile retains its original model split and configured does not substitute models',()=>{
 const env={A4B_MAIN_BASE_URL:'https://example.proxy.runpod.net/v1'};
 applyEvaluationProfile('documented-9b-deepagent',env,process.cwd());
 assert.equal(env.A4B_MAIN_MODEL,'a4b-qwen35-9b-final664-pilot32k');
 assert.equal(env.A4B_CAPABILITY_MODEL,'a4b-qwen35-9b-previous-pilot32k');
 const configured={};applyEvaluationProfile('configured',configured,process.cwd());assert.deepEqual(configured,{});
 assert.throws(()=>applyEvaluationProfile('arbitrary-model',{},process.cwd()),/Unknown profile/);
});

test('public model definition keeps public source identity and explicit serving limits',()=>{
 const modelfile=readFileSync('scripts/verify/Modelfile.a4b-public-qwen35-9b-32k','utf8');
 assert.match(modelfile,/^FROM qwen3\.5:9b$/m);
 assert.match(modelfile,/^PARAMETER num_ctx 32768$/m);
 assert.match(modelfile,/^PARAMETER num_predict 4096$/m);
 assert.doesNotMatch(modelfile,/^FROM a4b-qwen35-/m);
});
