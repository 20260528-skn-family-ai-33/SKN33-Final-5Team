import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {builderWorker} from '../agent-builder';
import {reviewSchema} from './gap-bridge';
import {executeReviewedGap} from './gap-service';
import {remoteGenerator} from './generator';
import {waitForWorkers} from '../worker-exit';

async function main(){
 const args=process.argv.slice(2),values:Record<string,string>={};
 for(let i=0;i<args.length;i+=2){
  const key=args[i].replace(/^--/,'');
  if(!args[i].startsWith('--')||!['review','dataset','model','output','repairs','prompt'].includes(key)||key in values||!args[i+1])throw new Error('부족 기능 시험 옵션을 확인하세요.');
  values[key]=args[i+1];
 }
 if(!values.review||!values.dataset||!values.model)throw new Error('--review·--dataset·--model을 명시하세요.');
 if(values.prompt&&!['v2','v3','v4','v5','v6','v7'].includes(values.prompt))throw new Error('--prompt는 v2/v3/v4/v5/v6/v7이어야 합니다.');
 const review=reviewSchema.parse(JSON.parse(await readFile(resolve(values.review),'utf8')));
 const dataset=JSON.parse(await readFile(resolve(values.dataset),'utf8'));
 const generator=remoteGenerator({provider:'ollama',model:values.model,conditionalStatus:values.prompt==='v7'});
 const result=await executeReviewedGap(review,dataset,generator,{outputDirectory:resolve(values.output||'../../research/a4b_tool_gap_20261027/live'),
  maxRepairs:values.repairs===undefined?1:Number(values.repairs),
  promptVersion:values.prompt==='v7'?'v7':values.prompt==='v6'?'v6':values.prompt==='v5'?'v5':values.prompt==='v4'?'v4':values.prompt==='v3'?'v3':'v2',
  worker:builderWorker,
  onProgress:message=>process.stderr.write(message+'\n')});
 console.log(JSON.stringify({...result.experiment,job:result.job,reused_job:result.reused_job},null,2));
}
try{await main();}catch(error){console.error(error instanceof Error?error.message:'부족 기능 시험 실패');process.exitCode=1;}
finally{await waitForWorkers();}
