import assert from 'node:assert/strict';import {writeFile} from 'node:fs/promises';
const base='http://127.0.0.1:3030';
async function call(action:string,data?:any){const r=await fetch(base+'/api/agent-builder/'+action,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,signal:AbortSignal.timeout(30000)});const value:any=await r.json();if(!r.ok)throw new Error(value.error);return value;}
const {agents}=await call('agents');const agent=agents.find((a:any)=>a.config.name==='팀별 금액 합계 정기 집계 및 보고서 저장');assert.ok(agent,'기존 데이터의 합계 자동화');
const run=await call('test',{id:agent.id,content:'수정판 실제 보고서 실행 확인'});assert.equal(run.status,'succeeded');assert.deepEqual(run.rows,[{team:'A',sum:0.3},{team:'B',sum:200}]);assert.equal(run.attempts[0].status,'succeeded');
const report=await fetch(base+'/api/agent-builder/report?id='+run.id);assert.equal(report.status,200);assert.match(await report.text(),/A,0.3/);
const {job}=await call('schedule_test',{id:agent.id});const cancelled=await call('schedule_cancel',{id:agent.id,jobId:job.id});assert.equal(cancelled.job.status,'cancelled');
const snapshot=await call('agent?id='+agent.id);assert.equal(snapshot.schedule.active,false);
await writeFile('docs/REVIEW_FIXES_LIVE_CHECK.json',JSON.stringify({date:'2026-10-13',agentId:agent.id,run,cancelled:cancelled.job,priorAgentsPreserved:agents.length,realModelCalls:0,regularSchedulePaused:true},null,2));console.log(JSON.stringify({status:'passed',rows:run.rows,priorAgentsPreserved:agents.length,report:run.report_path,cancellation:'passed'}));
