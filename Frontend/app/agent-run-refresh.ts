type Run = {id:string;status:string};

export function shouldRefreshAgentRuns(scheduled:boolean,current:Run|null,history:readonly Run[]):boolean{
 return scheduled||current?.status==='running'||history.some(run=>run.status==='running');
}

export function refreshedAgentRun<T extends Run>(runs:readonly T[],current:T|null,pinned:boolean):T|null{
 return pinned?runs.find(run=>run.id===current?.id)||current:runs[0]||current;
}

/** One request at a time; disposed or superseded views never receive its response. */
export function startAgentRunRefresh<T>(load:()=>Promise<T>,apply:(result:T)=>void,shouldSkip:()=>boolean):()=>void{
 let cancelled=false,pending=false;
 const timer=setInterval(async()=>{
  if(cancelled||pending||shouldSkip())return;
  pending=true;
  try{
   const result=await load();
   if(!cancelled&&!shouldSkip())apply(result);
  }catch{
   // Keep the last result and retry on the next tick, as the scheduled view does.
  }finally{pending=false;}
 },5000);
 return()=>{cancelled=true;clearInterval(timer);};
}
