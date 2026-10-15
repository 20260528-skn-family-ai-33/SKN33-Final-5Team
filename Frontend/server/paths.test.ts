import assert from 'node:assert/strict';
import {SERVER_STDIO,requestShutdown,removeDirectory,scaled} from './test-process';
import test from 'node:test';
import {spawn,spawnSync,type ChildProcess} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdtemp,rm} from 'node:fs/promises';
import {createServer} from 'node:net';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {agentPython,BACKEND_ROOT,FRONTEND_ROOT,PROJECT_ROOT,frontendPath} from './paths';

const loader=import.meta.resolve('tsx');
const pause=(milliseconds:number)=>new Promise<void>(done=>setTimeout(done,milliseconds));

test('repository paths keep existing data and sample files independent of the working directory',async()=>{
 const elsewhere=await mkdtemp(join(tmpdir(),'a4b-path-cwd-'));
 const module=pathToFileURL(frontendPath('server/paths.ts')).href;
 const script=`import {dataDirectory,examplePath,agentPython} from ${JSON.stringify(module)};import {existsSync} from 'node:fs';console.log(JSON.stringify({data:dataDirectory(),sample:existsSync(examplePath('초보자_CSV_예제.csv')),python:agentPython()}));`;
 try{
  for(const cwd of [PROJECT_ROOT,FRONTEND_ROOT,elsewhere]){
   const env={...process.env};delete env.EASYFLOW_DATA_DIR;env.A4B_AGENT_PYTHON='Backend/.venv-agent/bin/python';
   const result=spawnSync(process.execPath,['--import',loader,'--input-type=module','-e',script],{cwd,env,encoding:'utf8'});
   assert.equal(result.status,0,result.stderr);
   assert.deepEqual(JSON.parse(result.stdout),{data:join(PROJECT_ROOT,'data'),sample:true,python:join(BACKEND_ROOT,'.venv-agent/bin/python')});
   env.EASYFLOW_DATA_DIR='custom-data';
   const configured=spawnSync(process.execPath,['--import',loader,'--input-type=module','-e',script],{cwd,env,encoding:'utf8'});
   assert.equal(configured.status,0,configured.stderr);
   assert.equal(JSON.parse(configured.stdout).data,join(PROJECT_ROOT,'custom-data'));
  }
 }finally{await removeDirectory(elsewhere);}
});

async function freePort():Promise<number>{
 const socket=createServer();
 await new Promise<void>((done,reject)=>{socket.once('error',reject);socket.listen(0,'127.0.0.1',done);});
 const address=socket.address();assert.ok(address&&typeof address!=='string');
 await new Promise<void>((done,reject)=>socket.close(error=>error?reject(error):done()));
 return address.port;
}

async function stop(child:ChildProcess){
 if(child.exitCode!==null||child.signalCode)return;
 const exited=new Promise<void>(done=>child.once('exit',()=>done()));
 requestShutdown(child);
 const timer=setTimeout(()=>child.kill('SIGKILL'),scaled(4000));
 try{await exited;}finally{clearTimeout(timer);}
}

test('source and built servers serve the UI and reach the Python catalog from another directory',{skip:!existsSync(agentPython())},async()=>{
 const elsewhere=await mkdtemp(join(tmpdir(),'a4b-server-cwd-'));
 const python=agentPython();
 try{
  for(const [name,args] of [
   ['source',['--import',loader,frontendPath('server/launch.ts')]],
   ['built',[frontendPath('build-server/index.mjs')]],
  ] as const){
   const port=await freePort(),directory=join(elsewhere,name);
   const child=spawn(process.execPath,[...args],{cwd:elsewhere,env:{...process.env,HOST:'127.0.0.1',PORT:String(port),A4B_FOCUSED_DEMO:'1',EASYFLOW_DATA_DIR:directory,A4B_AGENT_PYTHON:python},stdio:SERVER_STDIO});
   let output='';child.stdout?.on('data',chunk=>output+=String(chunk));child.stderr?.on('data',chunk=>output+=String(chunk));
   try{
    const base=`http://127.0.0.1:${port}`,deadline=Date.now()+scaled(12000);let ready=false;
    while(Date.now()<deadline){
     assert.equal(child.exitCode,null,output);
     try{ready=(await fetch(base+'/api/health',{signal:AbortSignal.timeout(500)})).ok;}catch{}
     if(ready)break;await pause(40);
    }
    assert.ok(ready,output);
    const page=await fetch(base);assert.equal(page.status,200);assert.match(await page.text(),/<div id="root"><\/div>/);
    const catalog=await fetch(base+'/api/agent-builder/catalog',{signal:AbortSignal.timeout(scaled(12000))});assert.equal(catalog.status,200);
    const value=await catalog.json() as any;assert.equal(value.engine.conversation,'deepagents');assert.ok(value.tools.length>0);
    assert.ok(existsSync(join(directory,'easyflow.sqlite')));assert.ok(existsSync(join(directory,'agent-builder.sqlite')));
    assert.equal(existsSync(join(elsewhere,'data')),false);
   }finally{await stop(child);}
  }
 }finally{await removeDirectory(elsewhere);}
});
