/** No slot freed up in time, so the worker process was never started. */
export class WorkerBusyError extends Error {}

/** FIFO counting semaphore for Python worker processes. */
export class WorkerSlots {
 private active=0;private waiting:Array<()=>void>=[];
 constructor(readonly limit:number){}
 get size(){return {active:this.active,waiting:this.waiting.length};}
 /** Resolves with a release function; rejects with WorkerBusyError when no slot frees up within timeoutMs. */
 acquire(timeoutMs:number,message:string):Promise<()=>void>{
  const grant=()=>{let released=false;return ()=>{if(released)return;released=true;this.release();};};
  if(this.active<this.limit){this.active++;return Promise.resolve(grant());}
  return new Promise((accept,reject)=>{
   const entry=()=>{clearTimeout(timer);accept(grant());};
   const timer=setTimeout(()=>{const index=this.waiting.indexOf(entry);if(index>=0)this.waiting.splice(index,1);reject(new WorkerBusyError(message));},timeoutMs);
   timer.unref?.();
   this.waiting.push(entry);
  });
 }
 private release(){
  // Hand the slot straight to the next waiter so the active count never exceeds the limit.
  const next=this.waiting.shift();
  if(next)next();else this.active--;
 }
}

// Ownership calls (renewing or ending a run's lease) must never queue behind long runs.
const CONTROL_ACTIONS=new Set(['heartbeat_run','interrupt_run','begin_generation','interrupt_generation','recover_generation']);
// Model calls and runs last minutes. Anything else (schedule checks, screens) ends in seconds.
const LONG_ACTIONS=new Set(['message','confirm','test','scheduled_run','retry_run','resume_run']);
const BUSY='서버에서 처리 중인 작업이 많습니다. 잠시 후 다시 시도하세요.';

export function createWorkerLanes(limits:{general:number;long:number;control:number;queueMs:number}){
 const general=new WorkerSlots(limits.general),long=new WorkerSlots(limits.long),control=new WorkerSlots(limits.control);
 return {general,long,control,async acquire(action:string):Promise<()=>void>{
  if(CONTROL_ACTIONS.has(action))return control.acquire(limits.queueMs,BUSY);
  if(!LONG_ACTIONS.has(action))return general.acquire(limits.queueMs,BUSY);
  const started=Date.now(),releaseLong=await long.acquire(limits.queueMs,BUSY);
  try{
   const releaseGeneral=await general.acquire(Math.max(1,limits.queueMs-(Date.now()-started)),BUSY);
   return ()=>{releaseGeneral();releaseLong();};
  }catch(error){releaseLong();throw error;}
 }};
}
