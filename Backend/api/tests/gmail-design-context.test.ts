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

test('design gets current server Gmail metadata and ignores client claims or tokens',async t=>{
 const previousPython=process.env.A4B_AGENT_PYTHON,previousLocal=process.env.A4B_REQUIRE_LOCAL_MODEL;
 process.env.A4B_AGENT_PYTHON=process.execPath;delete process.env.A4B_REQUIRE_LOCAL_MODEL;resetWorkerSlots();
 const payloads:any[]=[];
 t.mock.method(childProcess,'spawn',()=>{
  const child=new EventEmitter() as any;
  child.stdout=new PassThrough();child.stderr=new PassThrough();child.exitCode=null;child.signalCode=null;child.kill=()=>true;
  let input='';
  child.stdin=new Writable({write(chunk,_encoding,done){input+=String(chunk);done();},final(done){
   const payload=JSON.parse(input);payloads.push(payload);
   const result=payload.action==='session'?{credential_id:'fixture-model'}:{id:payload.id,status:'ready'};
   queueMicrotask(()=>{child.stdout.end(JSON.stringify({ok:true,result}));child.exitCode=0;child.emit('close');});done();
  }});
  return child;
 });syncBuiltinESMExports();
 let connected=true,slackConnected=true;
 const store={gmailConnected:()=>connected,listCredentials:()=>slackConnected?[{id:'slack-member',provider:'slack',service:'slack'}]:[],getCredential:()=>({provider:'openai',data:{apiKey:'fixture-model-key'}})} as unknown as Store;
 async function invoke(action:'message'|'confirm',extra:Record<string,unknown>={}){
  const id=randomUUID();let status=0;
  await withDataDirectory(join(tmpdir(),'gmail-design-context','users',randomUUID()),()=>handleAgentBuilder(
   {method:'POST'} as IncomingMessage,{} as ServerResponse,new URL('http://localhost/api/agent-builder/'+action),store,
   async()=>({id,revision:0,content:'매 정시마다 Gmail의 안 읽은 메일을 요약해줘',
    userTimezone:'America/New_York',gmail_connected:!connected,gmail_access_token:'untrusted-client-token',slack_connected:!slackConnected,slack_access_token:'untrusted-slack-token',...extra}),
   (_response,code)=>{status=code;},async()=>{throw new Error('Design must not fetch or refresh Gmail tokens');}));
  assert.equal(status,200);
  return payloads.findLast(payload=>payload.action===action);
 }
 try{
  const first=await invoke('message');
  assert.equal(first.gmail_connected,true);
  assert.equal(first.slack_connected,true);
  assert.equal(first.user_timezone,'America/New_York');
  connected=false;slackConnected=false;
  assert.equal((await invoke('message')).gmail_connected,false);
  assert.equal((await invoke('message')).slack_connected,false);
  assert.equal((await invoke('confirm')).gmail_connected,false);
  assert.equal((await invoke('confirm',{includeSchedule:true})).include_schedule,true);
  assert.equal((await invoke('confirm',{includeSchedule:false})).include_schedule,false);
  await assert.rejects(invoke('confirm',{includeSchedule:'yes'}),/예약을 함께 저장/);
  const batch=await invoke('message',{answers:[{questionId:'1'.repeat(24),value:0},
   {questionId:'2'.repeat(24),value:['CSV','직접 정한 형식'],custom:true},{questionId:'3'.repeat(24),value:null,skip:true}]});
  assert.deepEqual(batch.question_answers,[{question_id:'1'.repeat(24),value:0},
   {question_id:'2'.repeat(24),value:['CSV','직접 정한 형식'],custom:true},{question_id:'3'.repeat(24),value:null,skip:true}]);
  assert.ok(payloads.filter(payload=>['message','confirm'].includes(payload.action)).every(payload=>!('gmail_access_token' in payload)&&!('slack_access_token' in payload)));
 }finally{
  t.mock.restoreAll();syncBuiltinESMExports();resetWorkerSlots();
  if(previousPython===undefined)delete process.env.A4B_AGENT_PYTHON;else process.env.A4B_AGENT_PYTHON=previousPython;
  if(previousLocal===undefined)delete process.env.A4B_REQUIRE_LOCAL_MODEL;else process.env.A4B_REQUIRE_LOCAL_MODEL=previousLocal;
 }
});
