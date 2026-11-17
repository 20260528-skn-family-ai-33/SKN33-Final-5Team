import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkerLanes,WorkerBusyError} from '../src/worker-slots';

test('queued executions leave a long slot available for design',async()=>{
 const lanes=createWorkerLanes({general:4,long:3,control:1,queueMs:30});
 const first=await lanes.acquire('test'),second=await lanes.acquire('scheduled_run');
 const blocked=assert.rejects(lanes.acquire('retry_run'),WorkerBusyError);
 const design=await lanes.acquire('message');
 assert.equal(lanes.general.size.active,3);
 const short=await lanes.acquire('session');short();
 const control=await lanes.acquire('interrupt_generation');control();
 await blocked;
 for(const release of [first,second,design]){release();release();}
 assert.deepEqual(lanes.execution.size,{active:0,waiting:0});
 assert.deepEqual(lanes.long.size,{active:0,waiting:0});
 assert.deepEqual(lanes.general.size,{active:0,waiting:0});
});

test('execution admission is released when the shared lane times out',async()=>{
 const lanes=createWorkerLanes({general:3,long:2,control:1,queueMs:20});
 const a=await lanes.acquire('message'),b=await lanes.acquire('confirm');
 await assert.rejects(lanes.acquire('test'),WorkerBusyError);
 assert.deepEqual(lanes.execution.size,{active:0,waiting:0});
 a();b();
 const retry=await lanes.acquire('resume_run');retry();
 assert.equal(lanes.general.size.active,0);
});
