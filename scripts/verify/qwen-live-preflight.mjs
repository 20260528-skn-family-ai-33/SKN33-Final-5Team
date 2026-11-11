// Read-only discovery. Credentials remain in process memory; never copy .env to reports.
import {readFileSync,writeFileSync,existsSync} from 'node:fs';
import {parseEnv} from 'node:util';
import {resolve} from 'node:path';
import {endpointPolicy} from './qwen-live-policy.mjs';
const root=process.cwd();
const envFile=process.env.A4B_ENV_FILE?resolve(root,process.env.A4B_ENV_FILE):resolve(root,'.env');
if(existsSync(envFile))for(const [key,value] of Object.entries(parseEnv(readFileSync(envFile,'utf8'))))process.env[key]??=value;
// Identical alias mapping to scripts/run-configured.mjs; no localhost/model fallback.
if(process.env.A4B_LLM_BASE_URL){
 process.env.A4B_MAIN_PROVIDER??=process.env.A4B_LLM_PROVIDER||'ollama';
 process.env.A4B_MAIN_BASE_URL??=process.env.A4B_LLM_BASE_URL.replace(/\/$/,'');
 process.env.A4B_MAIN_MODEL??=process.env.A4B_LLM_MODEL||'';
 process.env.A4B_MAIN_API_KEY??=process.env.A4B_LLM_API_KEY||'';
}
const args=process.argv.slice(2),out=args[args.indexOf('--output')+1];
if(!args.includes('--output')||!out)throw Error('--output is required');
if(existsSync(resolve(out)))throw Error('Output exists; preserve prior runs and use a new path');
const localIndex=args.indexOf('--allow-local-endpoint');
if(localIndex>=0&&(!args[localIndex+1]||args[localIndex+1].startsWith('--')))throw Error('--allow-local-endpoint requires an exact URL');
const keys=['A4B_MAIN_PROVIDER','A4B_MAIN_MODEL','A4B_CAPABILITY_MODEL','A4B_PLANNER_MODE',
 'A4B_PLANNER_PROMPT','A4B_OLLAMA_NUM_CTX','A4B_OLLAMA_NUM_PREDICT','A4B_OLLAMA_TEMPERATURE',
 'A4B_MAIN_TIMEOUT_S','A4B_DESIGN_TIMEOUT_MS','A4B_CAPABILITY_MODE','A4B_CONDITION_MODE',
 'A4B_REQUIREMENT_MODE','A4B_VERIFIER_MODE','A4B_TOOL_ADVICE_MODE','A4B_TOOL_ADVICE_BACKEND'];
const report={kind:'live_endpoint_probe',started_at:new Date().toISOString(),env_source:envFile,
 alias_mapping:'scripts/run-configured.mjs:13-21',configured:Object.fromEntries(keys.map(k=>[k,process.env[k]??null])),
 auth_configured:!!process.env.A4B_MAIN_API_KEY,probes:[],inference_calls:0,status:'not_run',actual_model_id:null};
let url;
try{if(!process.env.A4B_MAIN_MODEL||!['ollama','vllm'].includes(process.env.A4B_MAIN_PROVIDER))throw Error();const policy=endpointPolicy(process.env.A4B_MAIN_BASE_URL,localIndex<0?undefined:args[localIndex+1]);url=policy.url;report.endpoint_kind=policy.kind;}
catch{report.status='blocked_configuration';}
if(url&&report.status!=='blocked_configuration'){
 report.endpoint=url.origin+url.pathname;
 const key=process.env.A4B_MAIN_API_KEY;
 const paths=[url.pathname.replace(/\/$/,'')+'/models'];
 if(process.env.A4B_MAIN_PROVIDER==='ollama')paths.push(url.pathname.replace(/\/v1\/?$/,'')+'/api/tags');
 for(const path of paths){
  const start=performance.now();
  try{
   const r=await fetch(url.origin+path,{headers:{'User-Agent':'A4B/0.1',...(key?{Authorization:'Bearer '+key}:{})},redirect:'error',signal:AbortSignal.timeout(15000)});
   const type=r.headers.get('content-type');let body;
   if(type?.includes('json')){try{body=await r.json()}catch{}}
   // No error body, headers, prompts or credentials are persisted.
   report.probes.push({path,status:r.status,content_type:type,elapsed_ms:Math.round(performance.now()-start),
    model_ids:body?.data?.map(m=>m.id)||body?.models?.map(m=>m.name)||null});
  }catch(e){report.probes.push({path,error_type:e.name,cause_code:e.cause?.code||null,elapsed_ms:Math.round(performance.now()-start)});}
 }
 const ids=report.probes.flatMap(p=>p.model_ids||[]),configured=process.env.A4B_MAIN_MODEL;
 const canonical=s=>s?.split('/').at(-1).includes(':')?s:s+':latest';
 const selected=[...new Set([configured,process.env.A4B_CAPABILITY_MODEL||configured])];
 const missing=selected.filter(model=>!ids.some(id=>id===model||(process.env.A4B_MAIN_PROVIDER==='ollama'&&canonical(id)===canonical(model))));
 const found=missing.length?null:ids.find(id=>id===configured||(process.env.A4B_MAIN_PROVIDER==='ollama'&&canonical(id)===canonical(configured)));
 report.status=found?'model_listed_inference_not_tested':report.probes.every(p=>p.status===404)?'blocked_http_404':ids.length?'blocked_model_not_listed':'blocked_endpoint';
 report.listed_model_id=found||null;
 report.missing_selected_models=missing;
}
writeFileSync(resolve(out),JSON.stringify(report,null,2)+'\n',{mode:0o600,flag:'wx'});
console.log(JSON.stringify({status:report.status,probes:report.probes.map(p=>({path:p.path,status:p.status,error_type:p.error_type})),inference_calls:0}));
