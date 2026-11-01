/** Server-only coordinator: caller supplies reviewed task, never a client-authored consent. */
import {createHash} from 'node:crypto';
import {runGapExperiment,reviewSchema} from './gap-bridge';
import type {Generator,PromptVersion} from './generator';

type Worker=(payload:Record<string,unknown>)=>Promise<any>;
class ExistingJob extends Error{constructor(public job:unknown){super('기존 제작 작업 반환');}}
export async function executeReviewedGap(reviewValue:unknown,dataset:unknown,generator:Generator,
 options:{worker:Worker;outputDirectory:string;maxRepairs?:number;promptVersion?:PromptVersion;onProgress?:(message:string)=>void}){
 const start=performance.now(),review=reviewSchema.parse(reviewValue);
 const inputHash=createHash('sha256').update(JSON.stringify({review,dataset,model:generator.identity,
  prompt:options.promptVersion??'v2',repairs:options.maxRepairs??1})).digest('hex');
 let claim:any;
 try{
  await options.worker({action:'tool_gap_job_recover',id:review.session_id});
  const result=await runGapExperiment(review,dataset,generator,{...options,
   loadCurrentGap:async()=>(await options.worker({action:'tool_gap',id:review.session_id,revision:review.revision})).tool_gap,
   beforeGenerate:async()=>{
    claim=await options.worker({action:'tool_gap_job_claim',id:review.session_id,revision:review.revision,input_sha256:inputHash});
    if(!claim.claimed)throw new ExistingJob(claim.job);
   }});
  const final=result.experiment.tasks[0].attempts.at(-1);
  const status=result.status==='stale'?'stale':final?.status==='generation_error'?'generation_error':result.status;
  const summary={status,task_id:result.task_id,model_calls:result.model_calls,
   passed_cases:final?.cases.filter(c=>c.passed).length??0,total_cases:final?.cases.length??0,
   response_seconds:(performance.now()-start)/1000};
  const finished=await options.worker({action:'tool_gap_job_finish',id:review.session_id,job_id:claim.job.id,lease_token:claim.lease_token,result:summary});
  return {job:finished.job,experiment:result,reused_job:false};
 }catch(error){
  if(error instanceof ExistingJob)return {job:error.job,experiment:null,reused_job:true};
  if(claim?.claimed){
   // A failed coordinator cannot silently leave the job marked successful.
   // Store no raw exception (it can contain request data or provider secrets).
   await options.worker({action:'tool_gap_job_finish',id:review.session_id,job_id:claim.job.id,lease_token:claim.lease_token,
    result:{status:'generation_error',task_id:review.reviewed_spec&&typeof review.reviewed_spec==='object'&&'id' in review.reviewed_spec?String(review.reviewed_spec.id):'unknown',
     model_calls:null,passed_cases:0,total_cases:0,response_seconds:(performance.now()-start)/1000}});
  }
  throw error;
 }
}
