import {BACKEND_ROOT,agentPython} from './paths';
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,chmod,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {builderWorker} from './agent-builder';

test('worker timeout records failure before returning and permits same-run checkpoint retry',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'a4b-worker-timeout-'));
 const previous={directory:process.env.EASYFLOW_DATA_DIR,python:process.env.A4B_AGENT_PYTHON,timeout:process.env.A4B_WORKER_TIMEOUT_MS};
 const python=agentPython();
 const worker=join(dir,'slow-python');
 try{
  const seeded=spawnSync(python,['-c',`import sys,json\nsys.path.insert(0,${JSON.stringify(BACKEND_ROOT)})\nfrom pathlib import Path\nfrom agent_builder.test_reply_review import seeded\nst,id=seeded(Path(sys.argv[1]),True)\nprint(id)\nst.close()`,dir],{encoding:'utf8'});
  assert.equal(seeded.status,0,seeded.stderr);const id=seeded.stdout.trim();
  await writeFile(worker,`#!${python}\nimport sys,json,time\nsys.path.insert(0,${JSON.stringify(BACKEND_ROOT)})\nfrom agent_builder.worker import handle\nimport agent_builder.reply_review as review\nfrom agent_builder.test_reply_review import output\ndef slow(*args):\n time.sleep(30)\n return output()\nreview.run_review=slow\ndata=json.load(sys.stdin)\ntry:\n result=handle(data)\n print(json.dumps({'ok':True,'result':result},ensure_ascii=False))\nexcept Exception:\n print(json.dumps({'ok':False,'error':'fixture failure'}))\n`);await chmod(worker,0o755);
  process.env.EASYFLOW_DATA_DIR=dir;process.env.A4B_AGENT_PYTHON=worker;process.env.A4B_WORKER_TIMEOUT_MS='4000';
  await assert.rejects(builderWorker({action:'test',id,content:'timeout fixture',api_key:'unused'}),/실행 시간이 초과/);
  const {tests}=await builderWorker({action:'agent',id});assert.equal(tests.length,1);assert.equal(tests[0].status,'failed');assert.equal(tests[0].error_type,'InterruptedExecution');
  process.env.A4B_AGENT_PYTHON=python;delete process.env.A4B_WORKER_TIMEOUT_MS;
  const retry=spawnSync(python,['-c',`import sys,json\nsys.path.insert(0,${JSON.stringify(BACKEND_ROOT)})\nfrom agent_builder.worker import handle\nimport agent_builder.reply_review as review\nfrom agent_builder.test_reply_review import output\nreview.run_review=lambda *a:output()\nr=handle({'directory':sys.argv[1],'action':'retry_run','id':sys.argv[2],'api_key':'unused'})\nprint(json.dumps({'status':r['status'],'attempts':[a['status'] for a in r['attempts']]}))`,dir,tests[0].id],{encoding:'utf8'});
  assert.equal(retry.status,0,retry.stderr);assert.deepEqual(JSON.parse(retry.stdout),{status:'succeeded',attempts:['failed','succeeded']});
 }finally{
  for(const [key,value] of Object.entries({EASYFLOW_DATA_DIR:previous.directory,A4B_AGENT_PYTHON:previous.python,A4B_WORKER_TIMEOUT_MS:previous.timeout})){if(value===undefined)delete process.env[key];else process.env[key]=value;}
  await rm(dir,{recursive:true,force:true});
 }
});
