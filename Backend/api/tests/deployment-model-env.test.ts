import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {localModelHealth} from '../src/builder-local-model';

const root=fileURLToPath(new URL('../../..',import.meta.url));
const compose=spawnSync('docker',['compose','version'],{encoding:'utf8'});
function renderCompose(files:string[]){
 const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('A4B_')&&!key.startsWith('EASYFLOW_')&&!key.startsWith('COMPOSE_')));
 const result=spawnSync('docker',['compose','--project-directory',root,...files.flatMap(file=>['--env-file',file]),'-f',join(root,'docker-compose.yml'),'config','--format','json'],{env,encoding:'utf8'});
 assert.equal(result.status,0,'Docker Compose should render without starting containers');
 return JSON.parse(result.stdout).services.app.environment as Record<string,string>;
}
test('Compose forwards all model overrides and keeps omitted overrides empty',{
 skip:compose.status!==0?'Docker Compose is required to render the deployment configuration':false,
},()=>{
 const dir=mkdtempSync(join(tmpdir(),'a4b-model-env-'));
 try{
  const file=join(dir,'fixture.env');
  const settings={A4B_MAIN_MODEL:'final664',A4B_CAPABILITY_MODEL:'previous105',A4B_OLLAMA_MODEL_ALIASES:'{"old":"final664"}',A4B_OLLAMA_NUM_CTX:'32768',A4B_OLLAMA_NUM_PREDICT:'6000',A4B_OLLAMA_TEMPERATURE:'0'};
  const render=()=>renderCompose([file]);
  writeFileSync(file,Object.entries(settings).map(([key,value])=>`${key}=${value}`).join('\n'));
  const configured=render();for(const [key,value] of Object.entries(settings))assert.equal(configured[key],value,key);
  writeFileSync(file,'');const defaults=render();
  assert.equal(defaults.A4B_PLANNER_MODE,'deepagent');
  assert.equal(defaults.A4B_VERIFIER_MODE,'off');
  for(const key of Object.keys(settings).filter(key=>key!=='A4B_MAIN_MODEL'))assert.equal(defaults[key],'',key);
 }finally{rmSync(dir,{recursive:true,force:true});}
});

test('default AWS settings match the baseline RunPod models; the 9B overlay is explicit',{
 skip:compose.status!==0?'Docker Compose is required to render the deployment configuration':false,
},async()=>{
 const old={...process.env},original=globalThis.fetch;
 try{
  const base=join(root,'infrastructure/env/.env.aws.example');
  const baseline=renderCompose([base]);
  const startup=readFileSync(join(root,'scripts/start-runpod-qwen-parser.sh'),'utf8');
  const modelfile=readFileSync(join(root,'scripts/setup/ollama/Modelfile.a4b-qwen35-4b-16k'),'utf8');
  const registered=startup.match(/"\$ollama" create ([\w-]+) -f/)?.[1];
  const source=modelfile.match(/^FROM (\S+)/m)?.[1];
  assert.ok(registered);assert.ok(source);
  assert.equal(baseline.A4B_MAIN_MODEL,registered);
  assert.equal(baseline.A4B_CAPABILITY_MODEL,'');
  assert.equal(baseline.A4B_OLLAMA_MODEL_ALIASES,'');
  assert.equal(baseline.A4B_VERIFIER_MODEL,source);
  assert.equal(baseline.A4B_OLLAMA_NUM_CTX,modelfile.match(/^PARAMETER num_ctx (\d+)/m)?.[1]);
  Object.assign(process.env,baseline);
  let ids=[registered+':latest',source];
  globalThis.fetch=(async()=>new Response(JSON.stringify({data:ids.map(id=>({id}))}))) as typeof fetch;
  assert.equal((await localModelHealth()).status,'ready');
  const tuned=renderCompose([base,join(root,'infrastructure/env/.env.aws.qwen35-9b.example')]);
  assert.equal(tuned.A4B_MAIN_MODEL,'a4b-qwen35-9b-final664-pilot32k');
  assert.equal(tuned.A4B_CAPABILITY_MODEL,'a4b-qwen35-9b-previous-pilot32k');
  assert.deepEqual(JSON.parse(tuned.A4B_OLLAMA_MODEL_ALIASES),{[registered]:tuned.A4B_MAIN_MODEL});
  assert.equal(tuned.A4B_OLLAMA_NUM_CTX,'32768');
  assert.equal(tuned.A4B_OLLAMA_NUM_PREDICT,'4096');assert.equal(tuned.A4B_OLLAMA_TEMPERATURE,'0');
  assert.equal(tuned.A4B_PLANNER_MODE,'deepagent');assert.equal(tuned.A4B_VERIFIER_MODE,'off');
  for(const [key,value] of Object.entries(baseline)){
   if(!['A4B_MAIN_MODEL','A4B_CAPABILITY_MODEL','A4B_OLLAMA_MODEL_ALIASES','A4B_OLLAMA_NUM_CTX','A4B_OLLAMA_NUM_PREDICT','A4B_OLLAMA_TEMPERATURE'].includes(key))assert.equal(tuned[key],value,key);
  }
  Object.assign(process.env,tuned);
  const missing=await localModelHealth();assert.equal(missing.status,'unavailable');
  assert.match(missing.message,/설계 모델.*지원 판정 모델/);
  ids=[tuned.A4B_MAIN_MODEL+':latest',tuned.A4B_CAPABILITY_MODEL+':latest'];
  assert.equal((await localModelHealth()).status,'ready');
 }finally{globalThis.fetch=original;for(const key of Object.keys(process.env))if(!(key in old))delete process.env[key];Object.assign(process.env,old);}
});

test('EC2 9B overlay overrides old direct/shadow settings while preserving the endpoint and native checks',{
 skip:compose.status!==0?'Docker Compose is required to render the deployment configuration':false,
},async()=>{
 const old={...process.env},original=globalThis.fetch;
 const dir=mkdtempSync(join(tmpdir(),'a4b-ec2-9b-'));
 try{
  const file=join(dir,'server.env');
  writeFileSync(file,'A4B_MAIN_BASE_URL=http://host.docker.internal:11434/v1\nA4B_MAIN_API_KEY=fixture-only\nA4B_PLANNER_MODE=direct\nA4B_PLANNER_PROMPT=v8\nA4B_VERIFIER_MODE=shadow\nA4B_REQUIREMENT_MODE=enforce\nA4B_REQUIREMENT_GENERATION_MODE=baseline\nA4B_MAIN_MODEL=a4b-qwen35-4b-16k\n');
  const baseline=renderCompose([file]);
  assert.equal(baseline.A4B_SHARED_MODEL_TARGET,'runpod');
  const overlay=join(root,'infrastructure/env/.env.ec2.qwen35-9b.example');
  const configured=renderCompose([file,overlay]);
  assert.equal(configured.A4B_SHARED_MODEL_TARGET,'ec2');
  assert.equal(configured.A4B_MAIN_MODEL,'a4b-qwen35-9b-final664-pilot32k');
  assert.equal(configured.A4B_CAPABILITY_MODEL,'a4b-qwen35-9b-previous-pilot32k');
  assert.equal(configured.A4B_OLLAMA_NUM_CTX,'32768');
  assert.equal(configured.A4B_OLLAMA_NUM_PREDICT,'4096');
  assert.equal(configured.A4B_OLLAMA_TEMPERATURE,'0');
  assert.equal(configured.A4B_PLANNER_MODE,'deepagent');
  assert.equal(configured.A4B_VERIFIER_MODE,'off');
  assert.deepEqual(JSON.parse(configured.A4B_OLLAMA_MODEL_ALIASES),{'a4b-qwen35-4b-16k':configured.A4B_MAIN_MODEL});
  for(const key of ['A4B_MAIN_BASE_URL','A4B_MAIN_API_KEY','A4B_PLANNER_PROMPT',
   'A4B_REQUIREMENT_MODE','A4B_REQUIREMENT_GENERATION_MODE','A4B_VERIFIER_MODEL']){
   assert.equal(configured[key],baseline[key],key);
  }
  Object.assign(process.env,configured);
  globalThis.fetch=(async()=>new Response(JSON.stringify({data:[configured.A4B_MAIN_MODEL,configured.A4B_CAPABILITY_MODEL].map(id=>({id:id+':latest'}))}))) as typeof fetch;
  const ready=await localModelHealth();assert.equal(ready.status,'ready');assert.equal(ready.target,'ec2');
 }finally{rmSync(dir,{recursive:true,force:true});globalThis.fetch=original;for(const key of Object.keys(process.env))if(!(key in old))delete process.env[key];Object.assign(process.env,old);}
});

test('configured launcher defaults to DeepAgent with no reviewer and preserves explicit experiment modes',()=>{
 const dir=mkdtempSync(join(tmpdir(),'a4b-planner-defaults-'));
 try{
  const file=join(dir,'fixture.env');
  const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('A4B_')));
  const run=()=>{
   const result=spawnSync(process.execPath,[join(root,'scripts/run-configured.mjs')],{
    cwd:root,env:{...env,A4B_ENV_FILE:file,A4B_CONFIG_CHECK:'1'},encoding:'utf8'});
   assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
  };
  writeFileSync(file,'');const defaults=run();
  assert.equal(defaults.plannerMode,'deepagent');assert.equal(defaults.verifierMode,'off');
  writeFileSync(file,'A4B_PLANNER_MODE=direct\nA4B_VERIFIER_MODE=shadow\n');
  const experiment=run();assert.equal(experiment.plannerMode,'direct');assert.equal(experiment.verifierMode,'shadow');
 }finally{rmSync(dir,{recursive:true,force:true});}
});

test('both 9B overlays replace inherited direct/shadow even without a 4B model',{
 skip:compose.status!==0?'Docker Compose is required to render the deployment configuration':false,
},()=>{
 const dir=mkdtempSync(join(tmpdir(),'a4b-9b-no-reviewer-'));
 try{
  const file=join(dir,'fixture.env');
  writeFileSync(file,'A4B_PLANNER_MODE=direct\nA4B_VERIFIER_MODE=shadow\n');
  for(const name of ['.env.aws.qwen35-9b.example','.env.ec2.qwen35-9b.example']){
   const configured=renderCompose([file,join(root,'infrastructure/env',name)]);
   assert.equal(configured.A4B_PLANNER_MODE,'deepagent',name);
   assert.equal(configured.A4B_VERIFIER_MODE,'off',name);
  }
 }finally{rmSync(dir,{recursive:true,force:true});}
});
