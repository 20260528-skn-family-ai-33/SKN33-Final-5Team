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
  const deepagent=renderCompose([base,join(root,'infrastructure/env/.env.aws.qwen35-9b.example'),join(root,'infrastructure/env/.env.aws.qwen35-9b-deepagent.example')]);
  const profile={A4B_PLANNER_MODE:'deepagent',A4B_REQUIREMENT_MODE:'enforce',A4B_REQUIREMENT_GENERATION_MODE:'baseline',A4B_TOOL_CATALOG_MODE:'model',A4B_VERIFIER_MODE:'shadow'};
  for(const [key,value] of Object.entries(profile))assert.equal(deepagent[key],value,key);
  for(const key of ['A4B_MAIN_MODEL','A4B_CAPABILITY_MODEL','A4B_MAIN_BASE_URL','A4B_MAIN_API_KEY','A4B_OLLAMA_NUM_CTX','A4B_OLLAMA_NUM_PREDICT'])assert.equal(deepagent[key],tuned[key],key);
  for(const [key,value] of Object.entries(baseline)){
   if(!['A4B_MAIN_MODEL','A4B_CAPABILITY_MODEL','A4B_OLLAMA_MODEL_ALIASES','A4B_OLLAMA_NUM_CTX','A4B_OLLAMA_NUM_PREDICT','A4B_OLLAMA_TEMPERATURE'].includes(key))assert.equal(tuned[key],value,key);
  }
  Object.assign(process.env,tuned);
  const missing=await localModelHealth();assert.equal(missing.status,'unavailable');
  assert.match(missing.message,/설계 모델.*지원 판정 모델/);
  ids=[source,tuned.A4B_MAIN_MODEL+':latest',tuned.A4B_CAPABILITY_MODEL+':latest'];
  assert.equal((await localModelHealth()).status,'ready');
 }finally{globalThis.fetch=original;for(const key of Object.keys(process.env))if(!(key in old))delete process.env[key];Object.assign(process.env,old);}
});
