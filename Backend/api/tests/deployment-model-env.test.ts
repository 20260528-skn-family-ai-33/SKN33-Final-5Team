import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../../..',import.meta.url));
const compose=spawnSync('docker',['compose','version'],{encoding:'utf8'});
test('Compose forwards all model overrides and keeps omitted overrides empty',{
 skip:compose.status!==0?'Docker Compose is required to render the deployment configuration':false,
},()=>{
 const dir=mkdtempSync(join(tmpdir(),'a4b-model-env-'));
 const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('A4B_')&&!key.startsWith('EASYFLOW_')&&!key.startsWith('COMPOSE_')));
 try{
  const file=join(dir,'fixture.env');
  const settings={A4B_MAIN_MODEL:'final664',A4B_CAPABILITY_MODEL:'previous105',A4B_OLLAMA_NUM_CTX:'32768',A4B_OLLAMA_NUM_PREDICT:'6000',A4B_OLLAMA_TEMPERATURE:'0'};
  const render=()=>{
   const result=spawnSync('docker',['compose','--project-directory',root,'--env-file',file,'-f',join(root,'docker-compose.yml'),'config','--format','json'],{env,encoding:'utf8'});
   assert.equal(result.status,0,'Docker Compose should render without starting containers');
   return JSON.parse(result.stdout).services.app.environment;
  };
  writeFileSync(file,Object.entries(settings).map(([key,value])=>`${key}=${value}`).join('\n'));
  const configured=render();for(const [key,value] of Object.entries(settings))assert.equal(configured[key],value,key);
  writeFileSync(file,'');const defaults=render();
  for(const key of Object.keys(settings).filter(key=>key!=='A4B_MAIN_MODEL'))assert.equal(defaults[key],'',key);
 }finally{rmSync(dir,{recursive:true,force:true});}
});
