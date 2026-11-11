import {readFileSync,existsSync} from 'node:fs';
import {parseEnv} from 'node:util';
import {spawn} from 'node:child_process';
import {resolve} from 'node:path';
import {applyEvaluationProfile} from './qwen-live-profiles.mjs';
const args=process.argv.slice(2),root=process.cwd();
function take(flag){const i=args.indexOf(flag);if(i<0)return;const value=args[i+1];args.splice(i,2);return value;}
const python=take('--python')||process.env.A4B_AGENT_PYTHON;
if(!python)throw Error('Provide --python or A4B_AGENT_PYTHON with the prepared backend environment');
const profile=take('--profile');
const file=process.env.A4B_ENV_FILE||'.env';
if(existsSync(file))for(const [k,v] of Object.entries(parseEnv(readFileSync(file,'utf8'))))process.env[k]??=v;
if(process.env.A4B_LLM_BASE_URL){
 process.env.A4B_MAIN_PROVIDER??=process.env.A4B_LLM_PROVIDER||'ollama';
 process.env.A4B_MAIN_BASE_URL??=process.env.A4B_LLM_BASE_URL.replace(/\/$/,'');
 process.env.A4B_MAIN_MODEL??=process.env.A4B_LLM_MODEL||'';
 process.env.A4B_MAIN_API_KEY??=process.env.A4B_LLM_API_KEY||'';
}
applyEvaluationProfile(profile,process.env,root);
process.env.PYTHONUTF8='1';
const child=spawn(python,[resolve(root,'scripts/verify/verify-qwen-live-evaluation.py'),...args],
 {cwd:root,env:process.env,stdio:'inherit',windowsHide:true});
child.on('error',()=>{console.error('Evaluation Python process could not start');process.exitCode=1;});
child.on('exit',code=>{process.exitCode=code??1;});
