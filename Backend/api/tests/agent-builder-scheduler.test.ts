import test from 'node:test';
import assert from 'node:assert/strict';
import {createBuilderScheduleRunner,createBuilderScheduleState,builderScheduleHealth} from '../src/agent-builder-scheduler';
import {WorkerBusyError} from '../src/worker-slots';

test('builder schedule runner keeps polling out of a still-running execution',async()=>{
 let release:()=>void=()=>{};const barrier=new Promise<void>(r=>release=r);const actions:string[]=[];
 const worker=async(p:Record<string,unknown>)=>{actions.push(String(p.action));
  if(p.action==='claim_due')return {jobs:[{id:'job',lease_token:'lease',agent_snapshot:{config:{workflow:{nodes:[]}},workflow_dependencies:{}}}]};
  if(p.action==='scheduled_run')await barrier;return {};
 };
 const runner=createBuilderScheduleRunner({} as any,worker);const running=runner.tick();
 while(!actions.includes('scheduled_run'))await new Promise(r=>setTimeout(r,1));
 await runner.tick();assert.equal(actions.filter(a=>a==='claim_due').length,1);
 release();await running;assert.equal(builderScheduleHealth().running,false);
});

test('schedule failure is recorded rather than reported as success',async()=>{
 const actions:string[]=[];
 const worker=async(p:Record<string,unknown>)=>{actions.push(String(p.action));
  if(p.action==='claim_due')return {jobs:[{id:'job',lease_token:'lease',agent_snapshot:{config:{workflow:{nodes:[]}},workflow_dependencies:{}}}]};
  if(p.action==='scheduled_run')throw new Error('controlled failure');return {};
 };
 await createBuilderScheduleRunner({} as any,worker).tick();
 assert.ok(actions.includes('fail_job'));assert.ok(builderScheduleHealth().lastError);
});

test('a run that never got a worker slot is left for the lease to offer again',async()=>{
 const actions:string[]=[];const scheduleState=createBuilderScheduleState();
 const worker=async(p:Record<string,unknown>)=>{actions.push(String(p.action));
  if(p.action==='claim_due')return {jobs:[{id:'job',lease_token:'lease',agent_snapshot:{config:{workflow:{nodes:[]}},workflow_dependencies:{}}}]};
  if(p.action==='scheduled_run')throw new WorkerBusyError('busy');return {};
 };
 await createBuilderScheduleRunner({} as any,worker,scheduleState).tick();
 assert.ok(actions.includes('scheduled_run'));assert.ok(!actions.includes('fail_job'));assert.match(scheduleState.lastError||'',/다시 시도/);
});
