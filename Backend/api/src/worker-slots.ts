/** FIFO counting semaphore for Python worker processes. */
export class WorkerSlots {
 private active=0;private waiting:Array<()=>void>=[];
 constructor(readonly limit:number){}
 get size(){return {active:this.active,waiting:this.waiting.length};}
 /** Resolves with a release function; rejects when no slot frees up within timeoutMs. */
 acquire(timeoutMs:number,message:string):Promise<()=>void>{
  const grant=()=>{let released=false;return ()=>{if(released)return;released=true;this.release();};};
  if(this.active<this.limit){this.active++;return Promise.resolve(grant());}
  return new Promise((accept,reject)=>{
   const entry=()=>{clearTimeout(timer);accept(grant());};
   const timer=setTimeout(()=>{const index=this.waiting.indexOf(entry);if(index>=0)this.waiting.splice(index,1);reject(new Error(message));},timeoutMs);
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
