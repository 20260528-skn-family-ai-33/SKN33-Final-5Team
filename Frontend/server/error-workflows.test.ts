import test from 'node:test';
import assert from 'node:assert/strict';
import {dispatchErrorWorkflow} from './error-workflows';
import {clone,type Execution,type Flow,type Json} from '../lib/workflow';
const flow=(id:string,type='errorTrigger'):Flow=>({id,name:id,nodes:[{id:'start',name:'오류 시작',type:`n8n-nodes-base.${type}`,typeVersion:1,position:[0,0],parameters:{}}],connections:{},settings:{}});
const parent=():Flow=>({...flow('parent','scheduleTrigger'),settings:{errorWorkflow:'handler'}});
const failure=():Execution=>({id:'failed-run',workflowId:'parent',workflowName:'parent',status:'error',mode:'live',startedAt:'2026-10-07T00:00:00Z',duration:30,runs:[{nodeId:'http',name:'API 조회',input:[{}],output:[[]],status:'error',duration:30,error:'요청 실패'}],error:'요청 실패',trigger:'schedule'});

test('production scheduled/webhook failure creates an independent error execution with truthful input',async()=>{
 for(const trigger of ['schedule','webhook']){const e={...failure(),trigger,url:'http://localhost:3010/?execution=failed-run',retryOf:'earlier-run'},original=clone(e);let captured:Json={};
  const result=await dispatchErrorWorkflow(e,parent(),id=>id==='handler'?flow('handler'):undefined,(child,input,mode,extra)=>{captured={child,input,mode,extra};return {...failure(),id:'error-run',workflowId:child.id,status:'queued',...extra};});
  assert.deepEqual(result,{dispatched:true,executionId:'error-run'});assert.equal(captured.mode,'live');assert.equal(captured.child.id,'handler');assert.deepEqual(captured.extra,{trigger:'error',parentExecutionId:'failed-run',errorWorkflowOf:'parent'});assert.deepEqual(captured.input,[{execution:{id:'failed-run',mode:'live',error:{message:'요청 실패'},lastNodeExecuted:'API 조회',url:e.url,retryOf:'earlier-run'},workflow:{id:'parent',name:'parent'}}]);assert.deepEqual(e,original);
 }
});
test('manual, learning, canceled/success and recursive error executions never dispatch',async()=>{
 let lookups=0,enqueues=0;for(const change of [{trigger:'manual'},{trigger:'retry'},{trigger:'error'},{mode:'learning'},{status:'success'},{status:'canceled'},{errorWorkflowOf:'source'}]){
  const result=await dispatchErrorWorkflow({...failure(),...change} as Execution,parent(),()=>{lookups++;return flow('handler');},()=>{enqueues++;return failure();});assert.deepEqual(result,{dispatched:false});
 }assert.equal(lookups,0);assert.equal(enqueues,0);assert.deepEqual(await dispatchErrorWorkflow(failure(),flow('parent'),()=>{throw new Error('must not look up');},()=>{throw new Error('must not enqueue');}),{dispatched:false});
});
test('missing, disabled, multiple/wrong starts and self error targets preserve parent failure',async()=>{
 const disabled=flow('handler');disabled.nodes[0].disabled=true;const multiple=flow('handler');multiple.nodes.push({...multiple.nodes[0],id:'second',name:'두 번째 시작'});
 for(const target of [undefined,disabled,multiple,flow('handler','manualTrigger')]){const e=failure(),original=clone(e);let calls=0;const result=await dispatchErrorWorkflow(e,parent(),()=>target,()=>{calls++;return failure();});assert.equal(result.dispatched,false);assert.ok(result.notificationError);assert.equal(calls,0);assert.deepEqual(e,original);}
 const self=parent();self.settings.errorWorkflow='parent';const result=await dispatchErrorWorkflow(failure(),self,()=>self,()=>failure());assert.equal(result.dispatched,false);assert.match(result.notificationError||'',/다른/);
});
test('error handler enqueue failures do not overwrite or expose the original error',async()=>{
 const e=failure(),original=clone(e);const rejected=await dispatchErrorWorkflow(e,parent(),()=>flow('handler'),()=>{throw new Error('private-connector-token');});assert.equal(rejected.dispatched,false);assert.ok(rejected.notificationError);assert.ok(!JSON.stringify(rejected).includes('private-connector-token'));assert.deepEqual(e,original);
 const empty=await dispatchErrorWorkflow(e,parent(),()=>flow('handler'),()=>null);assert.equal(empty.dispatched,false);assert.ok(empty.notificationError);assert.deepEqual(e,original);
});
