import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {createServer} from 'node:http';
import {handleAgentBuilder} from './agent-builder';
import {builderNeedsModel} from './builder-model-requirements';
import {Store} from './store';

const python=process.env.A4B_AGENT_PYTHON||resolve('.venv-agent/bin/python');
test('node library API pins selected versions, rejects stale writes and preserves snapshots after restart',{skip:!existsSync(python)},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-library-api-'));
 const previous=process.env.EASYFLOW_DATA_DIR;process.env.EASYFLOW_DATA_DIR=directory;
 let store=new Store(directory);
 const credential=store.saveCredential({name:'local mock',provider:'openai',data:{apiKey:'not-a-real-provider-key'}});
 const server=createServer((req,res)=>{void(async()=>{
  const json=(response:any,status:number,value:any)=>{response.writeHead(status,{'Content-Type':'application/json'});response.end(JSON.stringify(value));};
  try{const body=async()=>{let raw='';for await(const chunk of req)raw+=chunk;return JSON.parse(raw);};if(!await handleAgentBuilder(req,res,new URL(req.url!,'http://127.0.0.1'),store,body,json))json(res,404,{});}catch(e){json(res,400,{error:(e as Error).message});}
 })();});
 try{
  await new Promise<void>((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
  const base=`http://127.0.0.1:${(server.address() as any).port}/api/agent-builder/`;
  const call=(action:string,data?:unknown)=>fetch(base+action,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});
  const catalog=await(await call('nodes')).json() as any;assert.equal(catalog.defaults.length,5);assert.deepEqual(catalog.nodes,[]);
  const spec={...catalog.defaults.find((n:any)=>n.spec.kind==='agent').spec,name:'고객 문의 분석 역할'};
  const created=await call('nodes',{spec});assert.equal(created.status,200);const v1=(await created.json() as any).node;assert.equal(v1.version,1);
  assert.equal((await call('nodes',{id:catalog.defaults[0].id,baseVersion:0,spec})).status,400);
  const sessionResponse=await call('sessions',{model:'gpt-4.1-mini',credentialId:credential.id,nodeRefs:[v1.ref]});assert.equal(sessionResponse.status,200);
  const session=await sessionResponse.json() as any;assert.equal(session.node_library.find((n:any)=>n.ref===v1.ref).spec.name,spec.name);
  const updated=await call('nodes',{id:v1.id,baseVersion:1,spec:{...spec,name:'새 버전 역할'}});assert.equal(updated.status,200);assert.equal((await updated.json() as any).node.version,2);
  assert.equal((await call('nodes',{id:v1.id,baseVersion:1,spec})).status,400);
  for(const data of [{spec,id:true},{spec,baseVersion:-1},{spec,baseVersion:1.5},{spec:{...spec,api_key:'rejected-private-field'}}])assert.equal((await call('nodes',data)).status,400);
  for(const nodeRefs of [[v1.ref,v1.ref],['../node'],Array(11).fill(v1.ref)])assert.equal((await call('sessions',{model:'gpt-4.1-mini',credentialId:credential.id,nodeRefs})).status,400);
  store.close();store=new Store(directory);
  const restoredResponse=await call('session?id='+session.id);assert.equal(restoredResponse.status,200);
  const restored=await restoredResponse.json() as any;
  assert.equal(restored.node_library.find((n:any)=>n.ref===v1.ref).spec.name,spec.name);
  assert.equal(restored.required_node_refs[0],v1.ref);
  const listed=await(await call('nodes')).json() as any;assert.equal(listed.nodes.length,1);assert.equal(listed.nodes[0].version,2);
 }finally{await new Promise<void>(done=>server.close(()=>done()));store.close();if(previous===undefined)delete process.env.EASYFLOW_DATA_DIR;else process.env.EASYFLOW_DATA_DIR=previous;await rm(directory,{recursive:true,force:true});}
});

test('custom agent nodes require a model even through pinned graph reuse; processing nodes remain deterministic',()=>{
 const agent={workflow:{nodes:[{id:'role',tool:'agent_node',settings:{}}]}};
 assert.equal(builderNeedsModel(agent),true);
 assert.equal(builderNeedsModel({workflow:{nodes:[{id:'reuse',tool:'saved_workflow',settings:{}}]}},{reuse:{config:agent,workflow_dependencies:{}}}),true);
 assert.equal(builderNeedsModel({workflow:{nodes:[{tool:'aggregate_table',settings:{}}]}}),false);
});
