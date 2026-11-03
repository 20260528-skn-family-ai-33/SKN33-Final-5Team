import test from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import type {IncomingMessage,ServerResponse} from 'node:http';
import type {Store} from '../src/store';
import {handleAgentBuilder} from '../src/agent-builder';
import {withDataDirectory} from '../src/paths';

test('design mutations share the session lock inside a workspace and stay independent across members',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-design-locks-')),alice=join(directory,'alice'),bob=join(directory,'bob');
 const previous={python:process.env.A4B_AGENT_PYTHON,routing:process.env.A4B_DOCUMENT_ROUTING};
 process.env.A4B_AGENT_PYTHON=process.execPath;process.env.A4B_DOCUMENT_ROUTING='0';
 let heldAction='',release!:()=>void,started!:()=>void,gate=Promise.resolve();
 const calls:{action:string;directory:string}[]=[];
 // Exercise the real worker bridge without requiring an installed Python/LLM environment.
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;
  child.kill=()=>{child.signalCode='SIGKILL';child.emit('close');return true;};let input='';
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);calls.push(payload);
   const blocked=payload.action===(heldAction==='tool_gap_replan_decision'?'tool_gap_replan_decide':heldAction)&&payload.directory===alice;
   if(blocked)started();
   void (blocked?gate:Promise.resolve()).then(()=>{child.stdout.end(JSON.stringify({ok:true,result:{id:payload.id,revision:1}}));child.exitCode=0;child.emit('close');});done();
  }});return child;
 });syncBuiltinESMExports();
 const id=randomUUID(),choice={id,revision:0,requestId:randomUUID(),gapId:'gap_'+'a'.repeat(24),stateFingerprint:'a'.repeat(64),choice:'defer'};
 const bodies:Record<string,unknown>={upload:{id,revision:0,fileName:'sample.csv',data:Buffer.from('name\nAlice\n').toString('base64')},tool_gap_choice:choice,tool_gap_replan_decision:{id,revision:0,requestId:randomUUID(),proposalId:randomUUID(),decision:'defer'}};
 async function call(directory:string,action:string){
  let status=0;
  await withDataDirectory(directory,()=>handleAgentBuilder({method:'POST'} as IncomingMessage,{setHeader(){}} as unknown as ServerResponse,new URL('http://localhost/api/agent-builder/'+action),{} as Store,async()=>bodies[action],(_res,value)=>{status=value;}));
  return status;
 }
 try{
  for(const [held,concurrent] of [['upload','tool_gap_choice'],['upload','tool_gap_replan_decision'],['tool_gap_choice','upload'],['tool_gap_replan_decision','upload']]){
   await t.test(held+' blocks '+concurrent+' only for its own workspace',async()=>{
    heldAction=held;gate=new Promise<void>(resolve=>{release=resolve;});const ready=new Promise<void>(resolve=>{started=resolve;});
    const pending=call(alice,held);
    try{
     await ready;const count=calls.length;
     assert.equal(await call(alice,concurrent),409);assert.equal(calls.length,count,'blocked mutations never reach Python');
     assert.equal(await call(bob,concurrent),200,'another member is not blocked by the same ID');
    }finally{release();assert.equal(await pending,200);}
    assert.equal(await call(alice,concurrent),200,'the lock is released after completion');
   });
  }
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();
  if(previous.python===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=previous.python;
  if(previous.routing===undefined)delete process.env.A4B_DOCUMENT_ROUTING;else process.env.A4B_DOCUMENT_ROUTING=previous.routing;
  await rm(directory,{recursive:true,force:true});
 }
});
