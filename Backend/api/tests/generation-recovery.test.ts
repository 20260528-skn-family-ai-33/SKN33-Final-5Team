import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer,type IncomingMessage,type ServerResponse} from 'node:http';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {builderWorker,handleAgentBuilder} from '../src/agent-builder';
import {BACKEND_ROOT,agentPython} from '../src/paths';
import {withDataDirectory} from '../src/paths';
import type {Store} from '../src/store';

test('HTTP recovery resumes abandoned generation and restores a committed save without saving twice',{timeout:30000},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-recover-http-'));
 const keys=['A4B_AGENT_PYTHON','PYTHONPATH','A4B_CAPABILITY_MODE','A4B_CONDITION_MODE','A4B_REQUIREMENT_MODE','A4B_VERIFIER_MODE','A4B_STORAGE','EASYFLOW_PUBLIC_URL','A4B_REQUIRE_LOCAL_MODEL'];
 const previous=Object.fromEntries(keys.map(key=>[key,process.env[key]]));
 const python=agentPython();
 let server:ReturnType<typeof createServer>|undefined;
 try{
  process.env.A4B_AGENT_PYTHON=python;process.env.PYTHONPATH=directory;
  process.env.A4B_STORAGE='sqlite';delete process.env.EASYFLOW_PUBLIC_URL;delete process.env.A4B_REQUIRE_LOCAL_MODEL;
  for(const key of keys.filter(key=>key.endsWith('_MODE')))process.env[key]='off';
  // Real HTTP and Python workers; replace only the model to avoid external inference.
  await writeFile(join(directory,'sitecustomize.py'),`import sys\nsys.path.insert(0,${JSON.stringify(BACKEND_ROOT)})\nimport agent_builder.design.graph as graph\nfrom tests.design.test_builder import fake_conversation\ngraph.make_conversation=lambda *args,**kwargs:fake_conversation\nfrom infrastructure.persistence.storage import Storage\nfrom pathlib import Path\n_original_save=Storage.save_agent\ndef _save(store,state):\n result=_original_save(store,state)\n if Path(${JSON.stringify(join(directory,'crash-after-save'))}).exists():raise OSError('worker exited before save checkpoint')\n return result\nStorage.save_agent=_save\n`);
  const seed=await withDataDirectory(directory,()=>builderWorker({action:'new_session',model:'test',credential_id:randomUUID()}));
  const requestId=randomUUID(),content='stock이 reorder_level보다 작은 행을 숫자 오름차순으로 CSV 저장';
  await withDataDirectory(directory,()=>builderWorker({action:'begin_generation',request_action:'message',id:seed.id,revision:0,content,generation_request_id:requestId}));
  const store={getCredential:()=>({provider:'openai',data:{apiKey:'fixture-not-a-secret'}})} as unknown as Store;
  const json=(_res:ServerResponse,status:number,value:unknown)=>{_res.writeHead(status,{'Content-Type':'application/json'});_res.end(JSON.stringify(value));};
  server=createServer((req,res)=>{void withDataDirectory(directory,async()=>{
   try{
    await handleAgentBuilder(req,res,new URL(req.url!,'http://localhost'),store,
     async(request:IncomingMessage)=>{let text='';for await(const chunk of request)text+=chunk;return JSON.parse(text);},json);
   }catch(error){json(res,400,{error:(error as Error).message});}
  });});
  await new Promise<void>(resolve=>server!.listen(0,'127.0.0.1',resolve));
  const address=server.address();assert.ok(address&&typeof address==='object');
  const base='http://127.0.0.1:'+address.port+'/api/agent-builder/';
  const post=async(action:string,body:unknown)=>{const r=await fetch(base+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const value=await r.json() as any;assert.equal(r.status,200,JSON.stringify(value));return value;};
  const recovered=await post('recover_generation',{id:seed.id,revision:0,generationRequestId:requestId});assert.equal(recovered.recovered,true);
  const history=await (await fetch(base+'generation_history?id='+seed.id)).json() as any;
  assert.equal(history.requests[0].status,'failed');assert.equal(history.requests[0].reason_code,'generation_abandoned');
  const nextId=randomUUID();
  const result=await post('message',{id:seed.id,revision:0,content,generationRequestId:nextId});
  assert.equal(result.id,seed.id);assert.equal(result.revision,1);assert.equal(result.status,'needs_clarification');
  assert.equal(result.last_generation_request_id,nextId);
  assert.equal(result.messages.filter((message:any)=>message.role==='user').length,1);
  const ready=await post('message',{id:seed.id,revision:result.revision,content:'중복은 유지',generationRequestId:randomUUID()});
  assert.equal(ready.status,'ready');
  await writeFile(join(directory,'crash-after-save'),'crash');
  const confirmId=randomUUID();
  const interrupted=await fetch(base+'confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:seed.id,revision:ready.revision,generationRequestId:confirmId})});
  assert.equal(interrupted.status,400);await interrupted.json();
  const unadvanced=await (await fetch(base+'session?id='+seed.id)).json() as any;
  assert.equal(unadvanced.status,'ready');
  const saves=await (await fetch(base+'generation_history?id='+seed.id)).json() as any;
  const save=saves.requests.find((record:any)=>record.request_id===confirmId);
  assert.equal(save.status,'succeeded');assert.equal(save.workflow_version,1);
  assert.equal((await post('recover_generation',{id:seed.id,revision:unadvanced.revision,generationRequestId:confirmId})).recovered,true);
  const restored=await (await fetch(base+'session?id='+seed.id)).json() as any;
  assert.equal(restored.status,'saved');assert.equal(restored.saved_agent_id,save.workflow_id);
  assert.equal(restored.last_generation_request_id,confirmId);
  assert.equal((await post('recover_generation',{id:seed.id,revision:restored.revision,generationRequestId:confirmId})).recovered,true);
  const agents=await (await fetch(base+'agents')).json() as any;
  assert.equal(agents.agents.length,1);assert.equal(agents.agents[0].version,1);

 }finally{
  if(server){server.closeIdleConnections();await new Promise<void>(resolve=>server!.close(()=>resolve()));}
  for(const key of keys){const value=previous[key];if(value===undefined)delete process.env[key];else process.env[key]=value;}
  await rm(directory,{recursive:true,force:true});
 }
});
