import {documentationPath,examplePath} from '../server/paths';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
const base=process.env.A4B_DEMO_URL||'http://127.0.0.1:3030';
async function call(action:string,data?:any){const r=await fetch(base+'/api/'+action,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,signal:AbortSignal.timeout(180000)});const v:any=await r.json();if(!r.ok)throw Error(v.error);return v;}
const accounts=(await call('credentials')).credentials.filter((c:any)=>c.provider==='openai');assert.ok(accounts.length);
let s:any;
if(process.env.A4B_AGENT_ID){const base=(await call('agent-builder/agent?id='+process.env.A4B_AGENT_ID)).agent;s=await call('agent-builder/sessions',{agentId:base.id,credentialId:base.credential_id,model:base.model});s=await call('agent-builder/session?id='+s.id);}else if(process.env.A4B_SESSION_ID){s=await call('agent-builder/session?id='+process.env.A4B_SESSION_ID);}else{
s=await call('agent-builder/sessions',{credentialId:accounts[0].id,model:'gpt-4.1'});
s=await call('agent-builder/upload',{id:s.id,revision:s.revision,fileName:'inventory.csv',data:(await readFile(examplePath('scheduled-workflow/inventory.csv'))).toString('base64')});
s=await call('agent-builder/message',{id:s.id,revision:s.revision,content:'매일 오전 9시에 재고가 기준 수량보다 적은 상품을 찾아 보고서로 저장해줘.'});
console.log(JSON.stringify({stage:'clarification',status:s.status,questions:s.draft.questions,issues:s.issues,schedule:s.draft.schedule}));
}
const firstQuestions=s.draft.questions;
s=await call('agent-builder/message',{id:s.id,revision:s.revision,content:'한국 시간 Asia/Seoul 기준 매일 오전 9시입니다. 연결한 inventory.csv를 갱신 가능한 관리 데이터 소스로 사용합니다. current_stock이 threshold보다 작은 행만 찾으면 됩니다. 빈 값은 제외하고 숫자로 변환할 수 없는 값은 실행 실패로 기록해주세요. 중복 행은 유지하고 정렬은 필요 없습니다. 원래 열을 유지한 CSV를 A4B 서버 보고서 보관함에 저장해주세요. 요약 에이전트나 외부 전송은 필요 없습니다.'});
await writeFile(documentationPath('SCHEDULED_WORKFLOW_DRAFT_CHECK.json'),JSON.stringify(s,null,2));
console.log(JSON.stringify({stage:'configured',status:s.status,questions:s.draft.questions,issues:s.issues,workflow:s.draft.workflow,schedule:s.draft.schedule}));assert.equal(s.status,'ready');assert.equal(s.draft.schedule.timezone,'Asia/Seoul');assert.equal(s.draft.schedule.hour,9);assert.equal(s.draft.report_destination,'reports');assert.equal(s.draft.execution_plan.missing_policy,'exclude');assert.ok(!s.draft.tool_ids.includes('ask_user'));assert.ok(s.draft.tool_ids.includes('filter_sort'));
s=await call('agent-builder/confirm',{id:s.id,revision:s.revision});const id=s.saved_agent_id;
let run=await call('agent-builder/test',{id,content:'지금 실행'});assert.equal(run.status,'succeeded');assert.deepEqual(run.rows.map((r:any)=>r.product_name),['키보드','마우스']);assert.ok(run.report_path);const initialRun=run;
await call('agent-builder/schedule',{id,active:true});const schedule=(await call('agent-builder/agent?id='+id)).schedule;assert.equal(schedule.active,true);
let updated=await call('agent-builder/source_upload',{id,fileName:'inventory-updated.csv',data:(await readFile(examplePath('scheduled-workflow/inventory-updated.csv'))).toString('base64')});assert.equal(updated.source.version,2);
const {job}=await call('agent-builder/schedule_test',{id});console.log(JSON.stringify({stage:'timer-queued',jobId:job.id,dueAt:job.due_at}));
let info:any;
for(let i=0;i<18;i++){await new Promise(r=>setTimeout(r,3000));info=await call('agent-builder/agent?id='+id);const j=info.jobs.find((j:any)=>j.id===job.id);if(j&&['succeeded','failed'].includes(j.status))break;}
const finished=info.jobs.find((j:any)=>j.id===job.id);assert.equal(finished.status,'succeeded');const scheduledRun=info.tests.find((r:any)=>r.id===job.id);assert.ok(scheduledRun);assert.equal(scheduledRun.source_version,2);assert.deepEqual(scheduledRun.rows.map((r:any)=>r.product_name),['케이블']);assert.ok(scheduledRun.report_path);assert.equal(info.jobs.filter((j:any)=>j.id===job.id).length,1);
await call('agent-builder/source_upload',{id,fileName:'inventory-invalid.csv',data:(await readFile(examplePath('scheduled-workflow/inventory-invalid.csv'))).toString('base64')});const failed=await call('agent-builder/test',{id,content:'오류 데이터 실행'});assert.equal(failed.status,'failed');assert.ok(failed.steps.some((s:any)=>s.status==='failed'));assert.ok(!failed.report_path);
await call('agent-builder/source_upload',{id,fileName:'inventory-none.csv',data:(await readFile(examplePath('scheduled-workflow/inventory-none.csv'))).toString('base64')});const empty=await call('agent-builder/test',{id,content:'부족 상품 없음 실행'});assert.equal(empty.status,'succeeded');assert.equal(empty.rows.length,0);assert.ok(empty.artifact.content.includes('product_name'));
await call('agent-builder/source_upload',{id,fileName:'inventory-updated.csv',data:(await readFile(examplePath('scheduled-workflow/inventory-updated.csv'))).toString('base64')});await call('agent-builder/schedule',{id,active:false});const finalRun=await call('agent-builder/test',{id,content:'갱신된 소스로 최종 지금 실행'});assert.equal(finalRun.status,'succeeded');
const report={date:'2026-10-12',model:'gpt-4.1',agentId:id,firstQuestions,schedule,workflow:s.draft.workflow,initialRun,scheduledRun,failedRun:failed,emptyRun:empty,finalRun,realServerTimer:true,sourceUpdateReflected:true,noDuplicateJob:true,schedulePausedAfterVerification:true};await writeFile(documentationPath('SCHEDULED_WORKFLOW_LIVE_CHECK.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({stage:'complete',agentId:id,realServerTimer:true,sourceUpdateReflected:true,scheduledReport:scheduledRun.report_path,numericFailureRecorded:true,emptyReport:true,schedulePaused:true}));
