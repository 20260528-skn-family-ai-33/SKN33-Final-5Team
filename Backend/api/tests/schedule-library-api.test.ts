import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {EventEmitter} from 'node:events';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {syncBuiltinESMExports} from 'node:module';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PassThrough,Writable} from 'node:stream';
import test from 'node:test';
import {handleAgentBuilder,resetWorkerSlots} from '../src/agent-builder';
import {withDataDirectory} from '../src/paths';
import type {Store} from '../src/store';
import {withTeamLibraryAccess} from '../src/team-library-access';

test('schedule listing is a noncached GET scoped to the authorized workspace',async t=>{
 const previousPython=process.env.A4B_AGENT_PYTHON;
 process.env.A4B_AGENT_PYTHON=process.execPath;
 resetWorkerSlots();
 const alice=join(tmpdir(),'schedule-listing','users',randomUUID()),bob=join(tmpdir(),'schedule-listing','users',randomUUID());
 const snapshots=new Map([[alice,{workflows:[{agent:{id:'alice-scheduled'},schedule:{active:true,next_run_at:'2026-11-11T00:00:00+00:00'}},{agent:{id:'missing-state'},schedule:null}]}],[bob,{workflows:[]}]]);
 const payloads:any[]=[];
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;
  child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;child.kill=()=>true;
  let input='';
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);payloads.push(payload);
   assert.deepEqual(Object.keys(payload).sort(),['action','directory']);
   assert.equal(payload.action,'schedules');assert.ok(snapshots.has(payload.directory));
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result:snapshots.get(payload.directory)}));child.exitCode=0;child.emit('close');});
   done();
  }});
  return child;
 });
 syncBuiltinESMExports();
 async function invoke(directory:string,method='GET',query=''){
  const headers:Record<string,unknown>={};let status=0,value:any;
  const response={setHeader(name:string,header:unknown){headers[name]=header;}} as unknown as ServerResponse;
  const handled=await withDataDirectory(directory,()=>handleAgentBuilder(
   {method} as IncomingMessage,response,new URL('http://localhost/api/agent-builder/schedules'+query),
   {} as Store,async()=>{throw new Error('Schedule listing must not read a request body');},
   (_response,code,result)=>{status=code;value=result;}));
  assert.equal(handled,true);return {status,value,headers};
 }
 try{
  const [first,second]=await Promise.all([invoke(alice,'GET','?directory='+encodeURIComponent(bob)+'&workspace_id=someone-else'),invoke(bob)]);
  assert.equal(first.status,200);assert.deepEqual(first.value,snapshots.get(alice));
  assert.equal(second.status,200);assert.deepEqual(second.value,snapshots.get(bob));
  assert.equal(first.headers['Cache-Control'],'no-store');
  assert.equal(payloads.length,2);
  for(const method of ['POST','PATCH','DELETE']){
   const result=await invoke(alice,method);assert.equal(result.status,405);assert.equal(result.headers.Allow,'GET');
  }
  await assert.rejects(withTeamLibraryAccess(async()=>{throw new Error('팀 접근 권한이 없습니다.');},()=>invoke(alice)),/접근 권한/);
  assert.equal(payloads.length,2,'unsupported methods and revoked membership never start workers');
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();resetWorkerSlots();
  if(previousPython===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=previousPython;
 }
});

test('Gmail reservation controls reject disconnected accounts before enqueueing but still allow stopping',async t=>{
 const previousPython=process.env.A4B_AGENT_PYTHON;process.env.A4B_AGENT_PYTHON=process.execPath;resetWorkerSlots();
 const actions:string[]=[],id=randomUUID();
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;child.kill=()=>true;
  let input='';
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);actions.push(payload.action);
   const result=payload.action==='agent'?{agent:{config:{workflow:{nodes:[{tool:'email.list'}]}}}}:{schedule:{active:payload.active}};
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result}));child.exitCode=0;child.emit('close');});done();
  }});return child;
 });syncBuiltinESMExports();
 async function invoke(action:string,active?:boolean,token?:()=>Promise<string|undefined>){
  let value:any;
  await handleAgentBuilder({method:'POST'} as IncomingMessage,{} as ServerResponse,
   new URL('http://localhost/api/agent-builder/'+action),{} as Store,async()=>({id,active}),
   (_response,status,result)=>{assert.equal(status,200);value=result;},token);
  return value;
 }
 try{
  await assert.rejects(invoke('schedule',true),/Gmail 계정/);
  await assert.rejects(invoke('schedule_test'),/Gmail 계정/);
  assert.deepEqual(actions,['agent','agent']);
  await invoke('schedule',false);assert.equal(actions.at(-1),'schedule');
  await invoke('schedule',true,async()=>'fixture-scoped-access');
  assert.deepEqual(actions.slice(-2),['agent','schedule']);
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();resetWorkerSlots();
  if(previousPython===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=previousPython;
 }
});

test('reservation configure and remove route separate settings without model credentials',async t=>{
 const previousPython=process.env.A4B_AGENT_PYTHON;process.env.A4B_AGENT_PYTHON=process.execPath;resetWorkerSlots();
 const payloads:any[]=[],id=randomUUID();
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;child.kill=()=>true;
  let input='';child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);payloads.push(payload);
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result:{schedule:payload.action==='schedule_remove'?null:{spec:payload.spec,active:false}}}));child.exitCode=0;child.emit('close');});done();
  }});return child;
 });syncBuiltinESMExports();
 const spec={frequency:'weekly',timezone:'Asia/Seoul',hour:9,minute:30,weekday:0};
 async function invoke(action:string,body:unknown,method='POST'){
  let status=0,value:any;
  await handleAgentBuilder({method} as IncomingMessage,{} as ServerResponse,new URL('http://localhost/api/agent-builder/'+action),{} as Store,async()=>body,
   (_response,code,result)=>{status=code;value=result;});return {status,value};
 }
 try{
  assert.deepEqual(await invoke('schedule_configure',{id,spec}),{status:200,value:{schedule:{spec,active:false}}});
  assert.deepEqual(await invoke('schedule_remove',{id}),{status:200,value:{schedule:null}});
  assert.deepEqual(payloads.map(p=>p.action),['schedule_configure','schedule_remove']);
  assert.deepEqual(payloads[0].spec,spec);assert.equal(payloads[0].id,id);
  assert.ok(payloads.every(p=>!('api_key' in p)&&!('model' in p)&&!('credential_id' in p)));
  for(const invalid of [null,[],true,'daily'])await assert.rejects(invoke('schedule_configure',{id,spec:invalid}),/예약 일정/);
  assert.equal((await invoke('schedule_remove',{},'GET')).status,405);
  assert.equal(payloads.length,2);
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();resetWorkerSlots();
  if(previousPython===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=previousPython;
 }
});
