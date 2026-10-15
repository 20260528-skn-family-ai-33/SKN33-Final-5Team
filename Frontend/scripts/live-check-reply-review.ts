import {documentationPath} from '../server/paths';
/** Opt-in real provider check. No secrets or real customer data go into the report. */
import assert from 'node:assert/strict';
import {writeFile,readFile} from 'node:fs/promises';
const base=process.env.A4B_DEMO_URL||'http://127.0.0.1:3042';
if(process.argv[2])process.loadEnvFile(process.argv[2]);
const model=process.env.A4B_REVIEW_MODEL||'gpt-4.1-mini';
async function api(action:string,data?:any){const response=await fetch(base+'/api/'+action,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,signal:AbortSignal.timeout(900000)});const result=await response.json() as any;if(!response.ok)throw new Error(result.error||'API request failed');return result;}
let observed:any={realProvider:true,model,checkedAt:new Date().toISOString()};
try{
 let accounts=(await api('credentials')).credentials.filter((c:any)=>c.provider==='openai');
 if(!accounts.length){assert.ok(process.env.OPENAI_API_KEY,'Configure an OpenAI connection in the UI or provide an env file');await api('credentials',{provider:'openai',name:'OpenAI 검수 시연',data:{apiKey:process.env.OPENAI_API_KEY}});accounts=(await api('credentials')).credentials.filter((c:any)=>c.provider==='openai');}
 const credentials=accounts[0];
 const scheduleOnly=process.argv.includes('--schedule-only');
 if(scheduleOnly)observed=JSON.parse(await readFile(documentationPath('REPLY_REVIEW_LIVE_CHECK.json'),'utf8'));
 if(!scheduleOnly){
 let s=await api('agent-builder/sessions',{credentialId:credentials.id,model});
 s=await api('agent-builder/upload',{id:s.id,revision:s.revision,fileName:'시연_정책.txt',data:Buffer.from('환불 신청은 구매 후 7일 이내에 가능합니다. 환불 신청 후 처리 기간은 영업일 3일입니다. 실제 환불 처리 여부는 담당자가 거래 기록을 확인해야 합니다.').toString('base64')});
 const prompt='고객 응대 초안 검수 에이전트를 만들어주세요. 고객 문의와 기존 응대 초안은 실행 화면에서 입력합니다. 연결된 정책 문서를 읽고 reply_review로 검수 보고서만 생성하세요. 근거 없는 처리 완료, 정책 충돌, 고객 요청 누락을 검수하고 문제 구절과 수정안을 보여주세요. 검수는 마지막 노드이며 운영자가 결과 화면에서 수정·승인 또는 거절합니다. 예약은 없습니다. 필요한 정책은 이미 연결했고 검수 입력은 실행 화면에서 받을 것이므로 개별 문의를 지금 묻지 마세요. execution_plan은 비활성으로 빈 filters/sort, missing_policy=keep, duplicate_policy=keep, output_format=json입니다.';
 s=await api('agent-builder/message',{id:s.id,revision:s.revision,content:prompt});
 assert.equal(s.status,'ready',JSON.stringify({status:s.status,questions:s.draft?.questions,issues:s.issues}));assert.ok(s.draft.tool_ids.includes('reply_review'));
 s=await api('agent-builder/confirm',{id:s.id,revision:s.revision});const agentId=s.saved_agent_id;observed.agentId=agentId;observed.naturalLanguageCreated=true;
 const bad=await api('agent-builder/test',{id:agentId,content:'환불 신청 방법과 처리 기간을 알려주세요.',replyDraft:'환불을 완료했습니다. 오늘 바로 입금됩니다.'});
 assert.equal(bad.status,'succeeded',bad.error);assert.ok(bad.reviews[0].report.findings.length);assert.notEqual(bad.reviews[0].report.verdict,'pass');assert.equal(bad.approval_status,'pending');observed.badDraftVerdict=bad.reviews[0].report.verdict;observed.badDraftCategories=bad.reviews[0].report.findings.map((f:any)=>f.category);
 const edited='환불은 구매 후 7일 이내에 신청할 수 있고, 신청 후 처리 기간은 영업일 3일입니다. 실제 처리 여부는 담당자가 거래 기록을 확인해야 합니다.';
 const approvePayload={id:bad.id,reviewId:bad.reviews[0].id,decision:'approve',editedReply:edited};
 const approved=await api('agent-builder/review_decision',approvePayload);assert.equal(approved.reviews[0].decision.edited_reply,edited);
 const duplicate=await api('agent-builder/review_decision',approvePayload);assert.deepEqual(duplicate.reviews[0].decision,approved.reviews[0].decision);observed.duplicateApprovalIdempotent=true;
 const reopened=(await api('agent-builder/run?id='+bad.id)).run;assert.equal(reopened.reviews[0].decision.edited_reply,edited);observed.savedDecisionRestored=true;
 const good=await api('agent-builder/test',{id:agentId,content:'환불 신청이 가능한 기간과 신청 후 처리 기간을 알려주세요.',replyDraft:'구매 후 7일 이내에 환불을 신청할 수 있습니다. 신청 후 처리 기간은 영업일 3일입니다.'});
 assert.equal(good.status,'succeeded',good.error);assert.equal(good.reviews[0].report.verdict,'pass');observed.goodDraftVerdict='pass';
 const rejected=await api('agent-builder/review_decision',{id:good.id,reviewId:good.reviews[0].id,decision:'reject',editedReply:''});assert.equal(rejected.reviews[0].decision.decision,'reject');
 const exported=await fetch(base+'/api/agent-builder/approved_replies?id='+agentId);assert.equal(exported.status,200);const csv=await exported.text();assert.ok(csv.includes(edited));assert.ok(!csv.includes(good.id));observed.onlyApprovedExported=true;
 const report=await fetch(base+'/api/agent-builder/review_report?id='+bad.id);assert.equal(report.status,200);assert.equal((await report.json() as any).reviews[0].decision.edited_reply,edited);observed.reportDownloaded=true;
 observed.runIds=[bad.id,good.id];
 }
 if(process.argv.includes('--scheduled')||scheduleOnly){
  const resumeId=process.argv.find(arg=>arg.startsWith('--resume-scheduled='))?.split('=')[1];
  const correction='조건 분기와 추가 노드를 모두 제거하고 read_table → reply_review 두 노드만 연결하세요. read_table은 bindings=[],file_ids=[]이며 관리 표 소스를 사용합니다. reply_review는 table binding을 read_table table에 연결하고 fields=[customer_message,reply_draft]입니다. 정책은 연결된 문서를 검수 코드가 자동 조회합니다. 빈 문의·초안은 검수 코드가 오류로 처리하므로 별도 분기·확인 노드가 필요 없습니다. 매일 오전 9시 Asia/Seoul 예약과 reports 저장을 그대로 유지하세요. 검수 이후 운영자 승인입니다.';
  let scheduled:any;
  if(resumeId){scheduled=await api('agent-builder/session?id='+resumeId);scheduled=await api('agent-builder/message',{id:scheduled.id,revision:scheduled.revision,content:correction});}
  else{scheduled=await api('agent-builder/sessions',{credentialId:credentials.id,model});
  for(const [fileName,content] of [['예약_정책.txt','환불 신청은 구매 후 7일 이내에 가능합니다. 신청 후 처리 기간은 영업일 3일입니다.'],['예약_문의.csv','customer_message,reply_draft\n환불 신청 기간을 알려주세요.,환불은 언제든 신청할 수 있습니다.\n']])scheduled=await api('agent-builder/upload',{id:scheduled.id,revision:scheduled.revision,fileName,data:Buffer.from(content).toString('base64')});
  scheduled=await api('agent-builder/message',{id:scheduled.id,revision:scheduled.revision,content:'매일 오전 9시 Asia/Seoul 시간대에 연결한 문의 표의 customer_message 열과 reply_draft 열을 검수하는 자동화를 만들어줘. 현재 연결한 표를 관리 데이터 소스로 사용하며 중복 행은 유지합니다. read_table table을 reply_review table에 연결하고 fields=[customer_message,reply_draft]로 설정하세요. 정책은 연결한 문서를 사용합니다. 검수는 마지막 노드이며 보고서는 A4B 서버 reports 보관함에 저장하고 운영자가 실행 후에 수정·승인·거절합니다. ask_user나 write_result/export_result는 필요 없습니다. 검수 최대 10행 범위이며 현재 자료는 1행입니다. execution_plan은 filters/sort=[],missing_policy=keep,duplicate_policy=keep,duplicate_field=null,match=all,output_format=json입니다.'});
  }
  if(scheduled.status!=='ready'){scheduled=await api('agent-builder/message',{id:scheduled.id,revision:scheduled.revision,content:correction});observed.scheduledDesignCorrected=true;}
  assert.equal(scheduled.status,'ready',JSON.stringify({status:scheduled.status,issues:scheduled.issues,questions:scheduled.draft?.questions}));
  scheduled=await api('agent-builder/confirm',{id:scheduled.id,revision:scheduled.revision});const scheduledAgentId=scheduled.saved_agent_id;
  const trial=(await api('agent-builder/schedule_test',{id:scheduledAgentId})).job;
  const deadline=Date.now()+180000;let trialRun:any;
  while(Date.now()<deadline){await new Promise(done=>setTimeout(done,3000));const info=await api('agent-builder/agent?id='+scheduledAgentId);trialRun=info.tests.find((r:any)=>r.id===trial.id);if(trialRun&&['succeeded','failed'].includes(trialRun.status))break;}
  assert.equal(trialRun?.status,'succeeded',trialRun?.error||'Trial did not finish');assert.equal(trialRun.approval_status,'pending');assert.equal(trialRun.reviews.length,1);assert.ok(trialRun.reviews[0].sources.length);
  const approvedTrial=await api('agent-builder/review_decision',{id:trialRun.id,reviewId:trialRun.reviews[0].id,decision:'approve',editedReply:'환불은 구매 후 7일 이내에 신청할 수 있습니다.'});assert.equal(approvedTrial.approval_status,'decided');
  observed.scheduled={agentId:scheduledAgentId,runId:trialRun.id,trigger:trialRun.trigger,sourceVersion:trialRun.source_version,verdict:trialRun.reviews[0].report.verdict,approvedAfterRun:true,policySnapshot:true};
 }

 await writeFile(documentationPath('REPLY_REVIEW_LIVE_CHECK.json'),JSON.stringify(observed,null,2));console.log(JSON.stringify(observed,null,2));
}catch(error){console.error(JSON.stringify({ok:false,step:'real-provider-check',message:error instanceof Error?error.message:'Check failed'}));process.exitCode=1;}
