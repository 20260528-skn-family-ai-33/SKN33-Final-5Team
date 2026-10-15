import {BACKEND_ROOT,agentPython} from './paths';
import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdtemp,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {builderWorker} from './agent-builder';
import {removeDirectory,scaled} from './test-process';

// Readiness uses wall time even while the worker's deadline and heartbeat clock are frozen.
const realSetInterval=setInterval,realClearInterval=clearInterval;

test('worker timeout records failure before returning and permits same-run checkpoint retry',{timeout:scaled(60000)},async t=>{
 const dir=await mkdtemp(join(tmpdir(),'a4b-worker-timeout-'));
 const previous={directory:process.env.EASYFLOW_DATA_DIR,python:process.env.A4B_AGENT_PYTHON,timeout:process.env.A4B_WORKER_TIMEOUT_MS,pythonPath:process.env.PYTHONPATH};
 const python=agentPython();
 const started=join(dir,'review-started');
 let execution:Promise<any>|undefined,clockActive=false,readinessTimer:ReturnType<typeof setInterval>|undefined;
 try{
  const seeded=spawnSync(python,['-c',`import sys,json\nsys.path.insert(0,${JSON.stringify(BACKEND_ROOT)})\nfrom pathlib import Path\nfrom agent_builder.test_reply_review import seeded\nst,id=seeded(Path(sys.argv[1]),True)\nprint(id)\nst.close()`,dir],{encoding:'utf8'});
  assert.equal(seeded.status,0,seeded.stderr);const id=seeded.stdout.trim();
  // Use the real worker entry point on every OS. Signal only after the run and its checkpoint exist.
  await writeFile(join(dir,'sitecustomize.py'),`import sys,time\nfrom pathlib import Path\nsys.path.insert(0,${JSON.stringify(BACKEND_ROOT)})\nimport agent_builder.reply_review as review\nfrom agent_builder.test_reply_review import output\ndef slow(*args):\n Path(${JSON.stringify(started)}).write_text('ready')\n time.sleep(120)\n return output()\nreview.run_review=slow\n`);
  process.env.EASYFLOW_DATA_DIR=dir;process.env.A4B_AGENT_PYTHON=python;process.env.PYTHONPATH=dir;process.env.A4B_WORKER_TIMEOUT_MS='4000';
  // Freeze the Node deadline and heartbeat: Python imports and the worker still run normally. Advancing it
  // after readiness tests the real timeout/kill/interrupt path without racing machine startup speed.
  t.mock.timers.enable({apis:['setTimeout','setInterval']});clockActive=true;
  const ready=new Promise<void>((accept,reject)=>{
   const deadline=Date.now()+scaled(45000);
   readinessTimer=realSetInterval(()=>{
    if(existsSync(started)){realClearInterval(readinessTimer);accept();}
    else if(Date.now()>=deadline){realClearInterval(readinessTimer);reject(new Error('worker did not enter the review node'));}
   },25);
  });
  let settled=false;
  execution=builderWorker({action:'test',id,content:'timeout fixture',api_key:'unused'});
  const prematureExit=execution.then(()=>{settled=true;throw new Error('worker finished before its timeout');},error=>{settled=true;throw error;});
  await Promise.race([ready,prematureExit]);
  t.mock.timers.tick(3999);await Promise.resolve();assert.equal(settled,false);
  t.mock.timers.tick(1);await assert.rejects(execution,/실행 시간이 초과/);
  t.mock.timers.reset();clockActive=false;
  const {tests}=await builderWorker({action:'agent',id});assert.equal(tests.length,1);assert.equal(tests[0].status,'failed');assert.equal(tests[0].error_type,'InterruptedExecution');
  process.env.A4B_AGENT_PYTHON=python;delete process.env.A4B_WORKER_TIMEOUT_MS;if(previous.pythonPath===undefined)delete process.env.PYTHONPATH;else process.env.PYTHONPATH=previous.pythonPath;
  const retry=spawnSync(python,['-c',`import sys,json\nsys.path.insert(0,${JSON.stringify(BACKEND_ROOT)})\nfrom agent_builder.worker import handle\nimport agent_builder.reply_review as review\nfrom agent_builder.test_reply_review import output\nreview.run_review=lambda *a:output()\nr=handle({'directory':sys.argv[1],'action':'retry_run','id':sys.argv[2],'api_key':'unused'})\nprint(json.dumps({'status':r['status'],'attempts':[a['status'] for a in r['attempts']]}))`,dir,tests[0].id],{encoding:'utf8'});
  assert.equal(retry.status,0,retry.stderr);assert.deepEqual(JSON.parse(retry.stdout),{status:'succeeded',attempts:['failed','succeeded']});
 }finally{
  if(readinessTimer)realClearInterval(readinessTimer);
  if(clockActive){t.mock.timers.tick(4000);await execution?.catch(()=>{});t.mock.timers.reset();}
  for(const [key,value] of Object.entries({EASYFLOW_DATA_DIR:previous.directory,A4B_AGENT_PYTHON:previous.python,A4B_WORKER_TIMEOUT_MS:previous.timeout,PYTHONPATH:previous.pythonPath})){if(value===undefined)delete process.env[key];else process.env[key]=value;}
  await removeDirectory(dir);
 }
});
