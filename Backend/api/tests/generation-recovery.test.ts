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

test('HTTP recovery closes an abandoned audit and permits a new request in the same session',{timeout:30000},async()=>{
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
  await writeFile(join(directory,'sitecustomize.py'),`import sys\nsys.path.insert(0,${JSON.stringify(BACKEND_ROOT)})\nimport agent_builder.design.graph as graph\nfrom tests.design.test_builder import fake_conversation\ngraph.make_conversation=lambda *args,**kwargs:fake_conversation\n`);
  const seed=await withDataDirectory(directory,()=>builderWorker({action:'new_session',model:'test',credential_id:randomUUID()}));
  const requestId=randomUUID(),content='재고가 부족한 행을 정리해줘';
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
  const post=async(action:string,body:unknown)=>{const r=await fetch(base+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});assert.equal(r.status,200);return r.json() as Promise<any>;};
  const recovered=await post('recover_generation',{id:seed.id,revision:0,generationRequestId:requestId});assert.equal(recovered.recovered,true);
  const history=await (await fetch(base+'generation_history?id='+seed.id)).json() as any;
  assert.equal(history.requests[0].status,'failed');assert.equal(history.requests[0].reason_code,'generation_abandoned');
  const nextId=randomUUID();
  const result=await post('message',{id:seed.id,revision:0,content,generationRequestId:nextId});
  assert.equal(result.id,seed.id);assert.equal(result.revision,1);assert.equal(result.status,'needs_clarification');
  assert.equal(result.last_generation_request_id,nextId);
  assert.equal(result.messages.filter((message:any)=>message.role==='user').length,1);
 }finally{
  if(server){server.closeIdleConnections();await new Promise<void>(resolve=>server!.close(()=>resolve()));}
  for(const key of keys){const value=previous[key];if(value===undefined)delete process.env[key];else process.env[key]=value;}
  await rm(directory,{recursive:true,force:true});
 }
});
