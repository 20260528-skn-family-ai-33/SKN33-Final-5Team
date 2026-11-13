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

test('direct Gmail to Slack run injects member tokens only when executing and blocks disconnected schedules',async t=>{
 const previousPython=process.env.A4B_AGENT_PYTHON,previousLocal=process.env.A4B_REQUIRE_LOCAL_MODEL;
 process.env.A4B_AGENT_PYTHON=process.execPath;delete process.env.A4B_REQUIRE_LOCAL_MODEL;resetWorkerSlots();
 const payloads:Record<string,any>[]=[];
 const id=randomUUID();
 const config={workflow:{nodes:[
  {id:'mail',tool:'email.list',settings:{operation:'read_messages'}},
  {id:'send',tool:'slack.post_message',settings:{operation:'execute',arguments_json:'{"channel_id":"C123"}'}},
 ]}};
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;
  child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;child.kill=()=>true;
  let input='';
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);payloads.push(payload);
   const result=payload.action==='agent'?{agent:{id,config,workflow_dependencies:{},credential_id:''},request_required:false}:payload.action==='test'?{status:'completed'}:{ok:true};
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result}));child.exitCode=0;child.emit('close');});done();
  }});
  return child;
 });syncBuiltinESMExports();
 let slackToken:string|undefined='private-slack-bot';
 const store={} as Store;
 async function invoke(action:'test'|'schedule'|'schedule_test',data:Record<string,unknown>={}){
  let response:any;
  await withDataDirectory(join(tmpdir(),'slack-builder-bridge','users',randomUUID()),()=>handleAgentBuilder(
   {method:'POST'} as IncomingMessage,{} as ServerResponse,new URL('http://localhost/api/agent-builder/'+action),store,
   async()=>({id,...data}),(_res,status,value)=>{response={status,value};},async()=> 'private-gmail-access',async()=>slackToken));
  return response;
 }
 try{
  const response=await invoke('test',{content:''});
  assert.equal(response.status,200);
  assert.doesNotMatch(JSON.stringify(response.value),/private-slack-bot|private-gmail-access/);
  const run=payloads.find(payload=>payload.action==='test');
  assert.ok(run);
  assert.equal(run.slack_access_token,'private-slack-bot');
  assert.equal(run.gmail_access_token,'private-gmail-access');
  assert.equal(payloads.find(payload=>payload.action==='agent')?.slack_access_token,undefined);
  slackToken=undefined;
  await assert.rejects(invoke('test',{content:''}),/Slack 계정을 먼저 연결/);
  await assert.rejects(invoke('schedule',{active:true}),/예약 실행에 사용할 Slack 계정을 먼저 연결/);
  await assert.rejects(invoke('schedule_test'),/예약 실행에 사용할 Slack 계정을 먼저 연결/);
  assert.equal(payloads.filter(payload=>['test','schedule','schedule_test'].includes(payload.action)).length,1);
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();resetWorkerSlots();
  if(previousPython===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=previousPython;
  if(previousLocal===undefined)delete process.env.A4B_REQUIRE_LOCAL_MODEL;else process.env.A4B_REQUIRE_LOCAL_MODEL=previousLocal;
 }
});
