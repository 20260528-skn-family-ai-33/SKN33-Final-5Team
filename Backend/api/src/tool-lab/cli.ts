import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {runExperiment} from './experiment';
import {demoDataset,fixtureGenerator} from './examples';
import {remoteGenerator} from './generator';
import {waitForWorkers} from '../worker-exit';
import {compareExperiments} from './compare';

async function main(){
 const args=process.argv.slice(2),allowed=new Set(['demo','compare','provider','model','endpoint','dataset','output','repairs']);
 const values:Record<string,string>={};
 for(let i=0;i<args.length;i++){
  const key=args[i].replace(/^--/,'');
  if(!args[i].startsWith('--')||!allowed.has(key)||key in values)throw new Error('실험 옵션을 확인하세요.');
  if(key==='demo'){values[key]='true';continue;}
  if(!args[i+1]||args[i+1].startsWith('--'))throw new Error('옵션 값이 필요합니다: '+key);
  values[key]=args[++i];
 }
 const outputDirectory=resolve(values.output||'../../research/a4b_tool_lab_20261026');
 const progress=(message:string)=>process.stderr.write(message+'\n');
 if(values.compare){
  if(Object.keys(values).some(key=>key!=='compare'))throw new Error('비교 모드에는 --compare만 사용하세요.');
  console.log(JSON.stringify(await compareExperiments(values.compare.split(',').map(p=>resolve(p))),null,2));return;
 }
 if(values.demo){
  if(values.provider||values.model||values.endpoint||values.dataset)throw new Error('시연과 모델 호출 옵션을 함께 사용할 수 없습니다.');
  const reference=await runExperiment(demoDataset(),fixtureGenerator(),{outputDirectory,maxRepairs:0,onProgress:progress});
  const boundary=await runExperiment(demoDataset(),fixtureGenerator(true),{outputDirectory,maxRepairs:0,onProgress:progress});
  console.log(JSON.stringify(await compareExperiments([reference.directory,boundary.directory]),null,2));return;
 }
 if(!['ollama','openai','vllm'].includes(values.provider)||!values.model||!values.dataset)throw new Error('--demo 또는 --provider·--model·--dataset을 명시하세요.');
 const dataset=JSON.parse(await readFile(resolve(values.dataset),'utf8'));
 const provider=values.provider as 'ollama'|'openai'|'vllm';
 const generator=remoteGenerator({provider,model:values.model,endpoint:values.endpoint,
  apiKey:provider==='openai'?process.env.OPENAI_API_KEY:provider==='vllm'?process.env.A4B_TOOL_LAB_API_KEY:undefined});
 console.log(JSON.stringify(await runExperiment(dataset,generator,{outputDirectory,maxRepairs:values.repairs===undefined?1:Number(values.repairs),onProgress:progress}),null,2));
}
try{await main();}catch(error){console.error(error instanceof Error?error.message:'실험을 완료하지 못했습니다.');process.exitCode=1;}
finally{await waitForWorkers();}
