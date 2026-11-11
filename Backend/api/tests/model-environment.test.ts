import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {applyConfiguredModelEnvironment} from '../../../scripts/model-environment.mjs';

test('shared model settings reach both the main model and verifier without touching secrets',()=>{
 const env:Record<string,string|undefined>={A4B_LLM_BASE_URL:'http://127.0.0.1:11434/v1/',A4B_LLM_MODEL:'configured-model',A4B_LLM_API_KEY:'fixture-secret'};
 applyConfiguredModelEnvironment(env);
 assert.equal(env.A4B_MAIN_PROVIDER,'ollama');
 assert.equal(env.A4B_MAIN_BASE_URL,'http://127.0.0.1:11434/v1');
 assert.equal(env.A4B_MAIN_MODEL,'configured-model');
 assert.equal(env.A4B_MAIN_API_KEY,'fixture-secret');
 assert.equal(env.A4B_VERIFIER_PROVIDER,'ollama');
 assert.equal(env.A4B_VERIFIER_BASE_URL,'http://127.0.0.1:11434');
 assert.equal(env.A4B_VERIFIER_API_KEY,'fixture-secret');
});

test('explicit main and verifier connections retain precedence',()=>{
 const env:Record<string,string|undefined>={A4B_LLM_BASE_URL:'https://shared.example/v1',A4B_LLM_MODEL:'shared-model',A4B_LLM_API_KEY:'shared-fixture',
  A4B_MAIN_PROVIDER:'vllm',A4B_MAIN_BASE_URL:'https://main.example/v1',A4B_MAIN_MODEL:'explicit-main',A4B_MAIN_API_KEY:'main-fixture',
  A4B_VERIFIER_PROVIDER:'ollama',A4B_VERIFIER_BASE_URL:'https://verify.example',A4B_VERIFIER_API_KEY:'verify-fixture'};
 const original={...env};applyConfiguredModelEnvironment(env);assert.deepEqual(env,original);
});

test('unconfigured environments are unchanged and repeated application is stable',()=>{
 const empty:Record<string,string|undefined>={PORT:'3041'};applyConfiguredModelEnvironment(empty);assert.deepEqual(empty,{PORT:'3041'});
 const configured:Record<string,string|undefined>={A4B_LLM_BASE_URL:'https://model.example/v1',A4B_LLM_MODEL:'test',A4B_LLM_PROVIDER:'vllm'};
 applyConfiguredModelEnvironment(configured);const once={...configured};applyConfiguredModelEnvironment(configured);assert.deepEqual(configured,once);
 assert.equal(configured.A4B_VERIFIER_BASE_URL,'https://model.example/v1');
});

test('dev/start and demo entrypoints apply the same model mapping before loading the server',()=>{
 const launch=readFileSync(new URL('../src/launch.ts',import.meta.url),'utf8');
 const demo=readFileSync(new URL('../../../scripts/launch-demo.mjs',import.meta.url),'utf8');
 const configured=readFileSync(new URL('../../../scripts/run-configured.mjs',import.meta.url),'utf8');
 assert.ok(launch.indexOf('applyConfiguredModelEnvironment();')<launch.indexOf("await import('./index')"));
 assert.ok(demo.includes('applyConfiguredModelEnvironment();'));
 assert.ok(configured.includes('applyConfiguredModelEnvironment();'));
});
