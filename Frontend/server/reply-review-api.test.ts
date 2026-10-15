import {BACKEND_ROOT,agentPython} from './paths';
import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {createServer} from 'node:http';
import {handleAgentBuilder} from './agent-builder';
import {Store} from './store';
const python=agentPython();
test('integrated review API persists edited approvals, rejects conflicts, restores history and exports only approved replies',{skip:!existsSync(python)},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-review-api-'));const previous=process.env.EASYFLOW_DATA_DIR;process.env.EASYFLOW_DATA_DIR=directory;
 const store=new Store(directory);
 const setup=spawnSync(python,['-c',`import sys,json\nsys.path.insert(0,${JSON.stringify(BACKEND_ROOT)})\nfrom pathlib import Path\nfrom agent_builder.test_reply_review import seeded,output,REQUEST\nfrom agent_builder.workflow_runtime import run_workflow\nimport agent_builder.reply_review as module\nmodule.run_review=lambda *args:output()\nst,id=seeded(Path(sys.argv[1]),True)\nr=run_workflow(st,id,REQUEST.customer_message,'unused',llm=object())\nassert r['status']=='succeeded',r.get('error')\nprint(json.dumps({'id':id,'run':r['id']}))\nst.close()`,directory],{encoding:'utf8'});
 assert.equal(setup.status,0,setup.stderr);const fixture=JSON.parse(setup.stdout.trim());
 const server=createServer((req,res)=>{void (async()=>{try{const json=(response:any,status:number,value:any)=>{response.writeHead(status,{'Content-Type':'application/json'});response.end(JSON.stringify(value));};const body=async()=>{let raw='';for await(const chunk of req)raw+=chunk;return JSON.parse(raw);};if(!await handleAgentBuilder(req,res,new URL(req.url!,'http://127.0.0.1'),store,body,json))json(res,404,{});}catch(error){res.writeHead(400,{'Content-Type':'application/json'});res.end(JSON.stringify({error:(error as Error).message}));}})();});
 try{
  await new Promise<void>((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});const address=server.address() as any;const base=`http://127.0.0.1:${address.port}/api/agent-builder/`;
  async function call(action:string,data?:any){return fetch(base+action,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});}
  const pending=await (await call('run?id='+fixture.run)).json() as any;assert.equal(pending.run.approval_status,'pending');
  const payload={id:fixture.run,reviewId:'review:0',decision:'approve',editedReply:'수정한 최종 문장'};
  const approved=await (await call('review_decision',payload)).json() as any;assert.equal(approved.reviews[0].decision.edited_reply,payload.editedReply);
  const duplicated=await (await call('review_decision',payload)).json() as any;assert.deepEqual(duplicated.reviews[0].decision,approved.reviews[0].decision);
  assert.equal((await call('review_decision',{...payload,decision:'reject'})).status,400);
  assert.equal((await call('review_decision',{...payload,reviewId:'review:999'})).status,400);
  assert.equal((await call('review_decision',{...payload,reviewId:'../bad'})).status,400);
  assert.equal((await call('review_decision',{...payload,editedReply:' '})).status,400);
  assert.equal((await call('review_decision',{...payload,reviewId:'review:1',decision:'reject',editedReply:''})).status,200);
  const reopened=await (await call('agent?id='+fixture.id)).json() as any;assert.equal(reopened.tests[0].reviews[0].decision.edited_reply,payload.editedReply);
  const csv=await call('approved_replies?id='+fixture.id);assert.equal(csv.status,200);const content=await csv.text();assert.ok(content.includes(payload.editedReply));assert.ok(!content.includes('review:1'));
  const report=await call('review_report?id='+fixture.run);assert.equal(report.status,200);assert.match(report.headers.get('content-disposition')||'',/attachment/);const json=await report.json() as any;assert.equal(json.reviews[0].decision.edited_reply,payload.editedReply);assert.equal(json.reviews[1].decision.decision,'reject');
  assert.equal((await call('approved_replies?id=../../x')).status,400);
 }finally{await new Promise<void>(done=>server.close(()=>done()));store.close();if(previous===undefined)delete process.env.EASYFLOW_DATA_DIR;else process.env.EASYFLOW_DATA_DIR=previous;await rm(directory,{recursive:true,force:true});}
});
