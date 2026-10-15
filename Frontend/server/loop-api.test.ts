import test from 'node:test';
import {SERVER_STDIO,requestShutdown,removeDirectory,scaled} from './test-process';
import assert from 'node:assert/strict';
import {spawn,type ChildProcess} from 'node:child_process';
import {createServer as createHttpServer,type Server as HttpServer} from 'node:http';
import {createServer as createSocketServer} from 'node:net';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import type {Execution,Flow,FlowNode,Json} from '../lib/workflow';

const project=resolve(fileURLToPath(new URL('..',import.meta.url)));
const delay=(milliseconds:number)=>new Promise<void>(resolve=>setTimeout(resolve,milliseconds));
async function freePort():Promise<number>{
 const socket=createSocketServer();await new Promise<void>((resolve,reject)=>{socket.once('error',reject);socket.listen(0,'127.0.0.1',resolve);});
 const address=socket.address();assert.ok(address&&typeof address!=='string');const port=address.port;
 await new Promise<void>((resolve,reject)=>socket.close(error=>error?reject(error):resolve()));return port;
}
class OwnServer {
 child?:ChildProcess;output='';
 constructor(readonly port:number,readonly directory:string){}
 get base(){return `http://127.0.0.1:${this.port}`;}
 async start():Promise<void>{
  assert.ok(!this.child);this.output='';const environment={...process.env,HOST:'127.0.0.1',PORT:String(this.port),EASYFLOW_DATA_DIR:this.directory};
  for(const key of Object.keys(environment))if(/^N8N_/i.test(key))delete (environment as NodeJS.ProcessEnv)[key];
  const child=this.child=spawn(process.execPath,['--import','tsx','server/index.ts'],{cwd:project,env:environment,stdio:SERVER_STDIO});
  child.stdout?.on('data',chunk=>{this.output=(this.output+String(chunk)).slice(-16000);});child.stderr?.on('data',chunk=>{this.output=(this.output+String(chunk)).slice(-16000);});
  let launchError:Error|undefined;child.once('error',error=>{launchError=error;});
  const deadline=Date.now()+scaled(12000);
  while(Date.now()<deadline){
   if(launchError)throw launchError;if(child.exitCode!==null||child.signalCode)throw new Error(`own server exited during startup: ${this.output}`);
   try{const response=await fetch(this.base+'/api/health',{signal:AbortSignal.timeout(500)});if(response.ok){assert.equal((await response.json() as Json).standalone,true);return;}}catch{}
   await delay(25);
  }
  throw new Error(`own server did not become ready: ${this.output}`);
 }
 async stop():Promise<void>{
  const child=this.child;if(!child)return;this.child=undefined;if(child.exitCode!==null||child.signalCode)return;
  const exit=new Promise<{code:number|null;signal:NodeJS.Signals|null}>(resolve=>child.once('exit',(code,signal)=>resolve({code,signal})));requestShutdown(child);
  let timer:ReturnType<typeof setTimeout>|undefined;
  try{const result=await Promise.race([exit,new Promise<never>((_,reject)=>{timer=setTimeout(()=>{child.kill('SIGKILL');reject(new Error(`own server shutdown timed out: ${this.output}`));},scaled(4000));})]);assert.equal(result.code,0,this.output);assert.equal(result.signal,null);}finally{if(timer)clearTimeout(timer);}
 }
 async request(path:string,method='GET',data?:unknown):Promise<{status:number;data:Json}>{
  const response=await fetch(this.base+path,{method,headers:{'Content-Type':'application/json'},...(data!==undefined?{body:JSON.stringify(data)}:{}),signal:AbortSignal.timeout(5000)});
  return {status:response.status,data:await response.json() as Json};
 }
 async execution(id:string,predicate:(execution:Execution)=>boolean):Promise<Execution>{
  const deadline=Date.now()+10000;let last:Execution|undefined;
  while(Date.now()<deadline){const response=await this.request('/api/executions?id='+encodeURIComponent(id));assert.equal(response.status,200);last=response.data.execution as Execution;if(predicate(last))return last;await delay(20);}
  throw new Error(`loop execution did not reach expected state: ${JSON.stringify(last)}`);
 }
}
function stage(id:string,type:string,parameters:Json={}):FlowNode{return {id,name:id,type:`n8n-nodes-base.${type}`,typeVersion:type==='splitInBatches'?3:type==='httpRequest'?4.2:type==='convertToFile'?1.1:1,position:[0,0],parameters};}
function loopWorkflow(url:string,wait:number,csv=true):Flow{
 const nodes=[stage('start','manualTrigger'),stage('loop','splitInBatches',{batchSize:1,options:{}}),stage('post','httpRequest',{method:'POST',url,sendBody:true,contentType:'json',specifyBody:'json',jsonBody:'={{ $json }}'}),stage('wait','wait',{resume:'timeInterval',amount:wait,unit:'seconds'}),csv?stage('result','convertToFile',{operation:'csv',binaryPropertyName:'result',options:{fileName:'processed.csv'}}):stage('result','set',{mode:'raw',jsonOutput:'={{ $json }}',includeOtherFields:false})];
 return {id:randomUUID(),name:'실제 반복 재시작 검증',nodes,connections:{start:{main:[[{node:'loop',type:'main',index:0}]]},loop:{main:[[{node:'result',type:'main',index:0}],[{node:'post',type:'main',index:0}]]},post:{main:[[{node:'wait',type:'main',index:0}]]},wait:{main:[[{node:'loop',type:'main',index:0}]]}},settings:{timezone:'Asia/Seoul',executionOrder:'v1'},active:false};
}
async function closeFixture(server:HttpServer):Promise<void>{server.closeAllConnections();await new Promise<void>((resolve,reject)=>server.close(error=>error?reject(error):resolve()));}

test('own loop API resumes persisted batch waits across a process restart and cancellation stops remaining external requests',{timeout:scaled(40000)},async t=>{
 const directory=await mkdtemp(join(tmpdir(),'easyflow-loop-api-')),server=new OwnServer(await freePort(),directory),effects:{path:string;item:Json}[]=[];
 const fixture=createHttpServer(async(req,res)=>{
  try{const chunks:Buffer[]=[];for await(const chunk of req)chunks.push(chunk);const item=JSON.parse(Buffer.concat(chunks).toString('utf8')) as Json;effects.push({path:req.url||'',item});res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({...item,handled:true}));}catch{res.writeHead(400);res.end('{}');}
 });
 try{
  await new Promise<void>((resolve,reject)=>{fixture.once('error',reject);fixture.listen(0,'127.0.0.1',resolve);});
  const address=fixture.address();assert.ok(address&&typeof address!=='string');const target=`http://127.0.0.1:${address.port}`;
  await server.start();
  await t.test('second-batch wait survives actual shutdown, all three writes remain unique, and done CSV is downloadable',async()=>{
   const flow=loopWorkflow(target+'/restart',0.4),input=[{id:1,name:'민지'},{id:2,name:'준호'},{id:3,name:'수아'}];
   assert.equal((await server.request('/api/workflows','POST',flow)).status,200);
   const run=await server.request('/api/runs','POST',{workflowId:flow.id,mode:'live',input});assert.equal(run.status,202);const id=run.data.executionId as string;
   const waiting=await server.execution(id,e=>e.status==='waiting'&&e.runs.filter(r=>r.nodeId==='post').length===2);
   assert.equal(waiting.pendingExecutionKey,'root/loop/batch1/wait');assert.ok(Date.parse(waiting.resumeAt)>Date.now());assert.deepEqual(effects.filter(effect=>effect.path==='/restart').map(effect=>effect.item.id),[1,2]);
   const savedRecords=structuredClone(waiting.runs);await server.stop();assert.deepEqual(effects.filter(effect=>effect.path==='/restart').map(effect=>effect.item.id),[1,2]);await server.start();
   const completed=await server.execution(id,e=>['success','error','canceled'].includes(e.status));assert.equal(completed.status,'success',completed.error);assert.equal(completed.id,id);assert.equal(completed.mode,'live');
   assert.deepEqual(effects.filter(effect=>effect.path==='/restart').map(effect=>effect.item.id),[1,2,3]);assert.deepEqual(completed.runs.slice(0,savedRecords.length),savedRecords);
   assert.deepEqual(completed.runs.filter(r=>r.nodeId==='post').map(r=>r.runIndex),[0,1,2]);assert.equal(completed.runs.filter(r=>r.nodeId==='wait').length,3);assert.equal(completed.runs.filter(r=>r.nodeId==='loop').length,4);
   const done=completed.runs.filter(r=>r.nodeId==='loop').at(-1)!;assert.deepEqual(done.output,[[...input.map(item=>({...item,handled:true}))],[]]);assert.equal(completed.loopContexts.loop.done,true);assert.equal(new Set(completed.runs.map(r=>r.executionKey)).size,completed.runs.length);
   const fileRun=completed.runs.findIndex(r=>r.nodeId==='result');assert.ok(fileRun>=0);const download=await fetch(`${server.base}/api/files/download?executionId=${encodeURIComponent(id)}&runIndex=${fileRun}&property=result`);assert.equal(download.status,200);assert.match(download.headers.get('content-type')||'',/text\/csv/);assert.match(download.headers.get('content-disposition')||'',/processed\.csv/);
   assert.equal(await download.text(),'id,name,handled\r\n1,민지,true\r\n2,준호,true\r\n3,수아,true');
  });
  await t.test('cancel during a durable loop wait prevents the next batch and remains canceled past its deadline',async()=>{
   const flow=loopWorkflow(target+'/cancel',0.2,false),input=[{id:10},{id:20},{id:30}];assert.equal((await server.request('/api/workflows','POST',flow)).status,200);
   const run=await server.request('/api/runs','POST',{workflowId:flow.id,mode:'live',input});assert.equal(run.status,202);const id=run.data.executionId as string;
   const waiting=await server.execution(id,e=>e.status==='waiting');assert.equal(waiting.pendingExecutionKey,'root/loop/batch0/wait');assert.deepEqual(effects.filter(effect=>effect.path==='/cancel').map(effect=>effect.item.id),[10]);
   assert.equal((await server.request('/api/executions/cancel','POST',{id})).status,200);const canceled=await server.execution(id,e=>e.status==='canceled');assert.equal(canceled.runs.filter(r=>r.nodeId==='post').length,1);
   await delay(Math.max(0,Date.parse(waiting.resumeAt)-Date.now())+1100);const retained=await server.execution(id,e=>e.status==='canceled');assert.deepEqual(retained.runs,canceled.runs);assert.deepEqual(effects.filter(effect=>effect.path==='/cancel').map(effect=>effect.item.id),[10]);assert.ok(!retained.runs.some(r=>r.nodeId==='result'));
  });
 }finally{try{await server.stop();}finally{if(fixture.listening)await closeFixture(fixture);await removeDirectory(directory);}}
});
