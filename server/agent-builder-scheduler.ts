import {builderNeedsModel} from './builder-model-requirements';
import type {Store} from './store';
import {builderWorker} from './agent-builder';
const state={running:false,lastCheckAt:null as string|null,lastError:null as string|null};
export function builderScheduleHealth(){return {...state};}
export function startBuilderScheduler(store:Store){
 let busy=false;
 const tick=async()=>{
  if(busy)return;busy=true;state.running=true;
  try{
   await builderWorker({action:'recover_runs'});
   const {jobs}=await builderWorker({action:'claim_due'});state.lastCheckAt=new Date().toISOString();state.lastError=null;
   for(const job of jobs){
    try{
     const config=job.agent_snapshot.config;
     const needsModel=builderNeedsModel(config,job.agent_snapshot.workflow_dependencies);
     let apiKey='';if(needsModel){const credential=store.getCredential(job.agent_snapshot.credential_id);apiKey=String(credential.data.apiKey||credential.data.token||'');}
     await builderWorker({action:'scheduled_run',id:job.id,lease_token:job.lease_token,api_key:apiKey});
    }catch{await builderWorker({action:'fail_job',id:job.id,lease_token:job.lease_token}).catch(()=>{});state.lastError='예약 실행 실패가 기록되었습니다.';}
   }
  }catch{state.lastError='예약 검사를 완료하지 못했습니다. 실행 환경을 확인하세요.';}
  finally{busy=false;state.running=false;}
 };
 const timer=setInterval(()=>void tick(),5000);timer.unref();void tick();return()=>clearInterval(timer);
}
