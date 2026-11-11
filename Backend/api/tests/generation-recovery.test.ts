import test,{type TestContext} from 'node:test';
import assert from 'node:assert/strict';
import {createServer,type IncomingMessage,type ServerResponse} from 'node:http';
import {mkdtemp,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {builderWorker,handleAgentBuilder} from '../src/agent-builder';
import {BACKEND_ROOT,agentPython,withDataDirectory} from '../src/paths';
import {removeDirectory,scaled} from '../src/test-process';
import type {Store} from '../src/store';

// Each scenario starts several real Python processes. A shared 30 s deadline was
// shorter than their combined cold starts during the full concurrent CI suite.
const scenarioTimeout=scaled(180000),requestTimeout=scaled(60000);
const content='stock이 reorder_level보다 작은 행을 숫자 오름차순으로 CSV 저장';

type Fixture={id:string;directory:string;get:(action:string)=>Promise<any>;
 post:(action:string,body:unknown,status?:number)=>Promise<any>};
async function withRecoveryServer(t:TestContext,run:(fixture:Fixture)=>Promise<void>){
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
  t.signal.throwIfAborted();
  const seed=await withDataDirectory(directory,()=>builderWorker({action:'new_session',model:'test',credential_id:randomUUID()}));
  t.signal.throwIfAborted();
  const store={getCredential:()=>({provider:'openai',data:{apiKey:'fixture-not-a-secret'}})} as unknown as Store;
  const json=(_res:ServerResponse,status:number,value:unknown)=>{if(_res.destroyed)return;_res.writeHead(status,{'Content-Type':'application/json'});_res.end(JSON.stringify(value));};
  server=createServer((req,res)=>{void withDataDirectory(directory,async()=>{
   try{
    await handleAgentBuilder(req,res,new URL(req.url!,'http://localhost'),store,
     async(request:IncomingMessage)=>{let text='';for await(const chunk of request)text+=chunk;return JSON.parse(text);},json);
   }catch(error){json(res,400,{error:(error as Error).message});}
  });});
  await new Promise<void>(resolve=>server!.listen(0,'127.0.0.1',resolve));
  const address=server.address();assert.ok(address&&typeof address==='object');
  const base='http://127.0.0.1:'+address.port+'/api/agent-builder/';
  const request=async(action:string,body:unknown,status:number)=>{
   const started=Date.now();
   try{
    const response=await fetch(base+action,{method:body===undefined?'GET':'POST',
     headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),
     signal:AbortSignal.any([t.signal,AbortSignal.timeout(requestTimeout)])});
    const value=await response.json();assert.equal(response.status,status,JSON.stringify(value));return value;
   }finally{t.diagnostic(`${action.split('?')[0]}: ${Date.now()-started} ms`);}
  };
  await run({id:seed.id,directory,get:action=>request(action,undefined,200),post:(action,body,status=200)=>request(action,body,status)});
 }finally{
  if(server){server.closeAllConnections();await new Promise<void>(resolve=>server!.close(()=>resolve()));}
  for(const key of keys){const value=previous[key];if(value===undefined)delete process.env[key];else process.env[key]=value;}
  await removeDirectory(directory);
 }
}

test('HTTP recovery resumes abandoned generation in the same session without duplicating input',{timeout:scenarioTimeout},async t=>{
 await withRecoveryServer(t,async({id,directory,get,post})=>{
  const requestId=randomUUID();
  await withDataDirectory(directory,()=>builderWorker({action:'begin_generation',request_action:'message',id,revision:0,content,generation_request_id:requestId}));
  assert.equal((await post('recover_generation',{id,revision:0,generationRequestId:requestId})).recovered,true);
  const history=await get('generation_history?id='+id);
  assert.equal(history.requests[0].status,'failed');assert.equal(history.requests[0].reason_code,'generation_abandoned');
  const nextId=randomUUID();
  const result=await post('message',{id,revision:0,content,generationRequestId:nextId});
  assert.equal(result.id,id);assert.equal(result.revision,1);assert.equal(result.status,'needs_clarification');
  assert.equal(result.last_generation_request_id,nextId);
  assert.equal(result.messages.filter((message:any)=>message.role==='user').length,1);
 });
});

test('HTTP recovery restores a committed save without creating another workflow version',{timeout:scenarioTimeout},async t=>{
 await withRecoveryServer(t,async({id,directory,get,post})=>{
  const first=await post('message',{id,revision:0,content,generationRequestId:randomUUID()});
  const ready=await post('message',{id,revision:first.revision,content:'중복은 유지',generationRequestId:randomUUID()});
  assert.equal(ready.status,'ready');
  await writeFile(join(directory,'crash-after-save'),'crash');
  const confirmId=randomUUID();
  await post('confirm',{id,revision:ready.revision,generationRequestId:confirmId},400);
  const unadvanced=await get('session?id='+id);assert.equal(unadvanced.status,'ready');
  const history=await get('generation_history?id='+id);
  const save=history.requests.find((record:any)=>record.request_id===confirmId);
  assert.equal(save.status,'succeeded');assert.equal(save.workflow_version,1);
  assert.equal((await post('recover_generation',{id,revision:unadvanced.revision,generationRequestId:confirmId})).recovered,true);
  const restored=await get('session?id='+id);
  assert.equal(restored.status,'saved');assert.equal(restored.saved_agent_id,save.workflow_id);
  assert.equal(restored.last_generation_request_id,confirmId);
  assert.equal((await post('recover_generation',{id,revision:restored.revision,generationRequestId:confirmId})).recovered,true);
  const agents=await get('agents');
  assert.equal(agents.agents.length,1);assert.equal(agents.agents[0].version,1);
 });
});
