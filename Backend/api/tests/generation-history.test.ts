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
import {builderWorker,handleAgentBuilder} from '../src/agent-builder';
import {withDataDirectory} from '../src/paths';
import type {IncomingMessage,ServerResponse} from 'node:http';
import type {Store} from '../src/store';

test('a killed design worker records its request id before returning the timeout',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'a4b-generation-timeout-'));
 const before={python:process.env.A4B_AGENT_PYTHON,provider:process.env.A4B_MAIN_PROVIDER,timeout:process.env.A4B_DESIGN_TIMEOUT_MS};
 process.env.A4B_AGENT_PYTHON=process.execPath;process.env.A4B_MAIN_PROVIDER='ollama';process.env.A4B_DESIGN_TIMEOUT_MS='4000';
 const calls:any[]=[];let started!:()=>void;
 const ready=new Promise<void>(resolve=>{started=resolve;});
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;
  child.kill=()=>{child.signalCode='SIGKILL';child.emit('close');return true;};let input='';
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);calls.push(payload);
   if(payload.action==='message'){started();done();return;}
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result:{generation_request_id:payload.generation_request_id}}));child.exitCode=0;child.emit('close');});done();
  }});return child;
 });syncBuiltinESMExports();
 try{
  t.mock.timers.enable({apis:['setTimeout']});
  const request=withDataDirectory(dir,()=>builderWorker({action:'message',id:randomUUID(),revision:0,content:'업무 생성'}));
  await ready;
  assert.deepEqual(calls.map(c=>c.action),['begin_generation','message']);
  t.mock.timers.tick(4000);
  await assert.rejects(request,/설계 응답 시간이 초과/);
  assert.deepEqual(calls.map(c=>c.action),['begin_generation','message','interrupt_generation']);
  assert.equal(new Set(calls.map(c=>c.generation_request_id)).size,1);
  assert.equal(calls[2].reason_code,'worker_timeout');
 }finally{
  t.mock.timers.reset();t.mock.restoreAll();syncBuiltinESMExports();
  for(const [key,value] of Object.entries({A4B_AGENT_PYTHON:before.python,A4B_MAIN_PROVIDER:before.provider,A4B_DESIGN_TIMEOUT_MS:before.timeout})){
   if(value===undefined)delete process.env[key];else process.env[key]=value;
  }
  await rm(dir,{recursive:true,force:true});
 }
});

test('credential preparation failures are recorded before any design worker starts',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'a4b-generation-auth-'));
 const previous=process.env.A4B_AGENT_PYTHON;process.env.A4B_AGENT_PYTHON=process.execPath;
 const calls:any[]=[];
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;
  child.kill=()=>true;let input='';
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);calls.push(payload);
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result:{credential_id:'missing-credential'}}));child.exitCode=0;child.emit('close');});done();
  }});return child;
 });syncBuiltinESMExports();
 const id=randomUUID();
 try{
  await assert.rejects(withDataDirectory(dir,()=>handleAgentBuilder(
   {method:'POST'} as IncomingMessage,{} as ServerResponse,new URL('http://localhost/api/agent-builder/message'),
   {getCredential(){throw new Error('계정을 찾을 수 없습니다.');}} as unknown as Store,
   async()=>({id,revision:0,content:'워크플로우 생성'}),()=>{})),/계정을 찾을 수 없습니다/);
  assert.deepEqual(calls.map(c=>c.action),['begin_generation','session','interrupt_generation']);
  assert.equal(calls[0].generation_request_id,calls[2].generation_request_id);
  assert.equal(calls[2].reason_code,'request_rejected');
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();
  if(previous===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=previous;
  await rm(dir,{recursive:true,force:true});
 }
});
