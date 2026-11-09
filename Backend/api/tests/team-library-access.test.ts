import assert from 'node:assert/strict';
import test from 'node:test';
import childProcess from 'node:child_process';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {syncBuiltinESMExports} from 'node:module';
import {builderWorker,resetWorkerSlots} from '../src/agent-builder';
import {withTeamLibraryAccess} from '../src/team-library-access';

test('a queued team worker rechecks membership after slot handoff and releases a rejected slot',async t=>{
 const previous={...process.env};
 Object.assign(process.env,{A4B_STORAGE:'postgres',A4B_DATABASE_URL:'postgres://fixture:fixture@localhost/fixture',A4B_AGENT_PYTHON:process.execPath,
  A4B_WORKER_MAX_CONCURRENCY:'1',A4B_WORKER_LONG_CONCURRENCY:'1',A4B_WORKER_CONTROL_CONCURRENCY:'1',A4B_WORKER_QUEUE_TIMEOUT_MS:'1000'});
 delete process.env.A4B_BUSINESS_RECORDS;resetWorkerSlots();
 const children:any[]=[];
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;child.kill=()=>true;
  child.stdin=new Writable({write(_chunk,_encoding,done){done();}});children.push(child);return child;
 });syncBuiltinESMExports();
 function finish(child:any){child.stdout.end(JSON.stringify({ok:true,result:{ok:true}}));child.exitCode=0;child.emit('close');}
 try{
  const occupying=builderWorker({action:'asset',id:'fixture'});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(children.length,1);
  let allowed=true,checks=0;
  const waiting=withTeamLibraryAccess(async()=>{checks++;if(!allowed)throw new Error('팀에서 제외되었습니다.');},()=>builderWorker({action:'library'}));
  // Attach rejection handling before freeing the slot to avoid an unhandled rejection.
  const denied=assert.rejects(waiting,/제외/);
  await new Promise(resolve=>setImmediate(resolve));assert.equal(checks,0,'membership must be checked after queuing');
  allowed=false;finish(children[0]);await occupying;await denied;
  assert.equal(children.length,1,'the revoked request must never spawn a worker');
  const next=builderWorker({action:'library'});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(children.length,2,'a rejected authorization releases the acquired slot');finish(children[1]);await next;
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();resetWorkerSlots();
  for(const key of Object.keys(process.env))if(!(key in previous))delete process.env[key];Object.assign(process.env,previous);
 }
});
