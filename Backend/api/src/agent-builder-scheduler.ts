import {localModelConnection,LOCAL_MODEL_ID} from './builder-local-model';
import {builderNeedsGmail,builderNeedsModel} from './builder-model-requirements';
import type {Store} from './store';
import {builderWorker} from './agent-builder';
import {WorkerBusyError} from './worker-slots';
export const createBuilderScheduleState=()=>({running:false,lastCheckAt:null as string|null,lastError:null as string|null});
const state=createBuilderScheduleState();
export function builderScheduleHealth(){return {...state};}
type Worker=(payload:Record<string,unknown>)=>Promise<any>;
export function createBuilderScheduleRunner(store:Store,worker:Worker=builderWorker,scheduleState=state,gmailAccessToken?:()=>Promise<string|undefined>){
 const state=scheduleState;
 let busy=false;
 const tick=async()=>{
  if(busy)return;busy=true;state.running=true;
  try{
   await worker({action:'recover_runs'});
   const {jobs}=await worker({action:'claim_due'});state.lastCheckAt=new Date().toISOString();state.lastError=null;
   for(const job of jobs){
    try{
     const config=job.agent_snapshot.config;
     const needsModel=builderNeedsModel(config,job.agent_snapshot.workflow_dependencies);
     let apiKey='';if(needsModel){const local=localModelConnection();const credential=job.agent_snapshot.credential_id===LOCAL_MODEL_ID&&local?{data:{apiKey:'local-not-a-secret',token:''}}:store.getCredential(job.agent_snapshot.credential_id);apiKey=String(credential.data.apiKey||credential.data.token||'');}
     const gmailToken=builderNeedsGmail(config,job.agent_snapshot.workflow_dependencies)?await gmailAccessToken?.():undefined;
     await worker({action:'scheduled_run',id:job.id,lease_token:job.lease_token,api_key:apiKey,...(gmailToken?{gmail_access_token:gmailToken}:{})});
    }catch(error){
     // Busy means the run never started: its lease expires and claim_due offers the job again.
     if(error instanceof WorkerBusyError){state.lastError='서버가 바빠 예약 실행을 미뤘습니다. 잠시 후 다시 시도합니다.';continue;}
     await worker({action:'fail_job',id:job.id,lease_token:job.lease_token}).catch(()=>{});state.lastError='예약 실행 실패가 기록되었습니다.';
    }
   }
  }catch{state.lastError='예약 검사를 완료하지 못했습니다. 실행 환경을 확인하세요.';}
  finally{busy=false;state.running=false;}
 };
 return {tick};
}
export function startBuilderScheduler(store:Store,scheduleState=state,gmailAccessToken?:()=>Promise<string|undefined>){
 const {tick}=createBuilderScheduleRunner(store,builderWorker,scheduleState,gmailAccessToken);
 const timer=setInterval(()=>void tick(),5000);timer.unref();void tick();return()=>clearInterval(timer);
}
