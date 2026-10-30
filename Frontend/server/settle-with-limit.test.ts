import assert from 'node:assert/strict';
import test from 'node:test';
import {settleWithLimit} from '../lib/settle-with-limit';
test('bounded loading preserves result order and individual failures while limiting requests',async()=>{
 let active=0,max=0;const result=await settleWithLimit([0,1,2,3,4,5,6,7],3,async i=>{active++;max=Math.max(active,max);try{await new Promise(r=>setTimeout(r,(i%3)+1));if(i===2)throw Error('부분 실패');return i*10;}finally{active--;}});assert.equal(max,3);assert.equal(result[2].status,'rejected');assert.deepEqual(result.map(r=>r.status==='fulfilled'?r.value:null),[0,10,null,30,40,50,60,70]);
});
test('navigation cancellation prevents queued requests from starting',async()=>{
 let current=true;const started:number[]=[];const result=await settleWithLimit([0,1,2,3],2,async i=>{started.push(i);current=false;return i;},()=>current);assert.deepEqual(started,[0]);assert.equal(result[0].status,'fulfilled');assert.ok(result.slice(1).every(r=>r.status==='rejected'));
});
test('empty loads and invalid concurrency are handled without invoking a task',async()=>{assert.deepEqual(await settleWithLimit([],4,async()=>{throw Error('호출되면 안 됩니다.');}),[]);await assert.rejects(()=>settleWithLimit([1],0,async i=>i),RangeError);});
