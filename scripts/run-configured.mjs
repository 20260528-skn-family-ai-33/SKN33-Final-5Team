import {readFileSync,existsSync} from 'node:fs';
import {parseEnv} from 'node:util';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const envFile=process.env.A4B_ENV_FILE?resolve(root,process.env.A4B_ENV_FILE):
  existsSync(resolve(root,'.env'))?resolve(root,'.env'):resolve(root,'Frontend/.env');
if(existsSync(envFile)){
 for(const [key,value] of Object.entries(parseEnv(readFileSync(envFile,'utf8'))))
  if(process.env[key]===undefined)process.env[key]=value;
}else if(process.env.A4B_ENV_FILE){throw new Error('지정한 환경설정 파일을 찾을 수 없습니다.');}
// Same deployment pattern as LENS: one Ollama /v1 URL, models selected by name.
const sharedUrl=process.env.A4B_LLM_BASE_URL;
if(sharedUrl){
 process.env.A4B_MAIN_PROVIDER??=process.env.A4B_LLM_PROVIDER||'ollama';
 process.env.A4B_MAIN_BASE_URL??=sharedUrl.replace(/\/$/,'');
 process.env.A4B_MAIN_MODEL??=process.env.A4B_LLM_MODEL||'qwen3:8b-q4_K_M';
 process.env.A4B_MAIN_API_KEY??=process.env.A4B_LLM_API_KEY||'';
 process.env.A4B_VERIFIER_PROVIDER??=process.env.A4B_MAIN_PROVIDER;
 process.env.A4B_VERIFIER_BASE_URL??=process.env.A4B_VERIFIER_PROVIDER==='ollama'?sharedUrl.replace(/\/$/,'').replace(/\/v1$/,''):sharedUrl.replace(/\/$/,'');
 process.env.A4B_VERIFIER_API_KEY??=process.env.A4B_LLM_API_KEY||'';
}
for(const [key,value] of Object.entries({HOST:'127.0.0.1',PORT:'3041',EASYFLOW_DATA_DIR:'data-local-integration',
 A4B_MAIN_PROVIDER:'ollama',A4B_MAIN_BASE_URL:'http://127.0.0.1:11434/v1',A4B_MAIN_MODEL:'qwen3:8b-q4_K_M',
 A4B_VERIFIER_MODE:'shadow'}))process.env[key]??=value;
if(process.env.A4B_CONFIG_CHECK==='1'){
 console.log(JSON.stringify({mainProvider:process.env.A4B_MAIN_PROVIDER,mainModel:process.env.A4B_MAIN_MODEL,
  verifierProvider:process.env.A4B_VERIFIER_PROVIDER||'ollama',verifierModel:process.env.A4B_VERIFIER_MODEL||process.env.A4B_QWEN_MODEL||'qwen3.5:4b',
  sharedOllamaEndpoint:process.env.A4B_VERIFIER_BASE_URL===process.env.A4B_MAIN_BASE_URL?.replace(/\/v1\/?$/,''),port:process.env.PORT}));
}else await import(pathToFileURL(resolve(root,'Frontend/build-server/index.mjs')).href);
