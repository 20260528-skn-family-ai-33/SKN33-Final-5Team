import assert from 'node:assert/strict';import {writeFile} from 'node:fs/promises';
const base='http://127.0.0.1:3030';
async function call(action:string,data?:any){const r=await fetch(base+'/api/'+action,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,signal:AbortSignal.timeout(180000)});const v:any=await r.json();if(!r.ok)throw Error(v.error);return v;}
const credentials=await call('credentials');const account=credentials.credentials.find((c:any)=>c.provider==='openai');assert.ok(account);
let s=await call('agent-builder/sessions',{credentialId:account.id,model:'gpt-4.1'});
const csv='team,amount\nA,0.1\nA,0.2\nB,150\nB,50\n';
s=await call('agent-builder/upload',{id:s.id,revision:s.revision,fileName:'team-sales.csv',data:Buffer.from(csv).toString('base64')});
s=await call('agent-builder/message',{id:s.id,revision:s.revision,content:'매주 월요일 오전 10시 한국 시간(Asia/Seoul)에 연결한 team-sales.csv의 amount 금액을 team별로 합계 집계해줘. 팀별 금액 합계만 필요하고 건수·정렬·추가 요약은 필요 없어. 이 파일을 갱신 가능한 관리 데이터 소스로 쓰고 A4B 서버 보고서 보관함에 CSV로 저장해줘. 빈 금액이나 숫자가 아닌 금액은 실행 실패로 처리하고 중복 행은 모두 합계에 포함해줘.'});
await writeFile('docs/INTEGRATION_LIVE_CHECK.json',JSON.stringify({creation:s},null,2));
assert.equal(s.status,'ready',JSON.stringify({status:s.status,issues:s.issues,questions:s.draft?.questions}));
const sum=s.draft.workflow.nodes.find((n:any)=>n.tool==='aggregate_table');assert.equal(sum.settings.aggregation,'sum');assert.equal(sum.settings.sum_field,'amount');assert.deepEqual(sum.settings.fields,['team']);
s=await call('agent-builder/confirm',{id:s.id,revision:s.revision});const agentId=s.saved_agent_id;
const run=await call('agent-builder/test',{id:agentId,content:'합계 보고서 실행'});assert.equal(run.status,'succeeded',run.error);assert.deepEqual(run.rows,[{team:'A',sum:0.3},{team:'B',sum:200}]);assert.ok(run.steps.every((step:any)=>step.output_summary&&typeof step.elapsed_ms==='number'));
const download=await fetch(base+'/api/agent-builder/report?id='+run.id);assert.equal(download.status,200);
const {job}=await call('agent-builder/schedule_test',{id:agentId});const cancelled=await call('agent-builder/schedule_cancel',{id:agentId,jobId:job.id});assert.equal(cancelled.job.status,'cancelled');
const a=await call('agent-builder/agent?id='+agentId);assert.equal(a.schedule.active,false);assert.equal(a.jobs.find((j:any)=>j.id===job.id).status,'cancelled');
await writeFile('docs/INTEGRATION_LIVE_CHECK.json',JSON.stringify({date:'2026-10-12',agentId,creationWorkflow:s.draft.workflow,run,cancelled:cancelled.job,regularSchedulePaused:!a.schedule.active,realModelCreation:true,deterministicExecution:true},null,2));
console.log(JSON.stringify({status:'passed',agentId,rows:run.rows,cancellation:'passed',report:run.report_path}));
