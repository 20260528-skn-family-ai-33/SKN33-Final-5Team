import {documentationPath} from '../server/paths';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
const base=process.env.A4B_DEMO_URL||'http://127.0.0.1:3030';
const manualPdf=process.env.A4B_MANUAL_PDF;
if(!process.env.A4B_SESSION_ID&&!manualPdf)throw new Error('새 매뉴얼 시연에는 A4B_MANUAL_PDF에 로컬 PDF 경로를 지정하세요. 원본 PDF는 Git에 등록하지 마세요.');
async function call(action:string,data?:any){const r=await fetch(base+'/api/'+action,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,signal:AbortSignal.timeout(240000)});const v:any=await r.json();if(!r.ok)throw new Error(v.error);return v;}
const accounts=(await call('credentials')).credentials.filter((c:any)=>c.provider==='openai');
let s:any;
if(process.env.A4B_SESSION_ID){s=await call('agent-builder/session?id='+process.env.A4B_SESSION_ID);}else{
s=await call('agent-builder/sessions',{model:process.env.A4B_TEST_MODEL||'gpt-4.1-mini',credentialId:accounts[0].id});
s=await call('agent-builder/upload',{id:s.id,revision:s.revision,fileName:'manual.pdf',data:(await readFile(manualPdf!)).toString('base64')});
console.log(JSON.stringify({stage:'upload',engine:s.resources[0].engine,pages:s.resources[0].page_count,originalStored:!!s.resources[0].original_path}));
s=await call('agent-builder/message',{id:s.id,revision:s.revision,content:'이 매뉴얼을 활용해서 직원이 프린터 오류를 입력하면 해결 방법을 안내하고, 해결되지 않으면 담당자에게 문의할 내용을 정리하도록 자동화하고 싶어요.'});
console.log(JSON.stringify({stage:'first-draft',status:s.status,questions:s.draft.questions,issues:s.issues,tools:s.draft.tool_ids}));
}
s=await call('agent-builder/message',{id:s.id,revision:s.revision,content:'모델명과 오류 내용이 부족하면 직원에게 물어봐 주세요. 매뉴얼에 근거가 없으면 추측하지 마세요. 안내한 뒤 직원에게 해결됐는지 반드시 질문하고 기다리세요. 답변이 불명확하면 해결/미해결인지 다시 물어보세요. 해결됐다고 답하면 완료 안내만 해주세요. 미해결이면 모델명, 오류 내용, 직원이 실제로 말한 시도한 조치와 결과로 문의 초안을 화면에 표시해주세요. 실제 전송이나 접수, 결과 파일 저장은 하지 않습니다.'});
if(s.status!=='ready')s=await call('agent-builder/message',{id:s.id,revision:s.revision,content:'현재 초안의 연결 검증 오류를 수정해주세요. 생성 단계에서는 실제 직원의 모델명이나 오류 내용을 요구하지 않습니다. 이미 충분한 직원 입력이면 추가 질문 없이 안내하고, 부족한 경우에만 물어봅니다. 해결 여부 분기는 직원이 해결됐다고 명확히 말했는지가 true 조건이며, 미해결이면 false입니다. 불명확한 응답 재확인은 ask_user 내부에서 처리하고 역방향 연결은 만들지 않습니다. 문의 초안에 필요한 문서 입력은 검색한 관련 구간을 사용합니다. 기존 전송 없음·파일 저장 없음 조건을 유지해주세요.'});
const report:any={date:'2026-10-12',sessionId:s.id,document:{engine:s.resources[0].engine,pages:s.resources[0].page_count},status:s.status,issues:s.issues,questions:s.draft.questions,workflow:s.draft.workflow};
await writeFile(documentationPath('MANUAL_WORKFLOW_CHECK.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify({stage:'configured',status:s.status,issues:s.issues,questions:s.draft.questions,nodes:s.draft.workflow.nodes.map((n:any)=>({id:n.id,tool:n.tool})),edges:s.draft.workflow.edges}));assert.equal(s.status,'ready');
s=await call('agent-builder/confirm',{id:s.id,revision:s.revision});report.agentId=s.saved_agent_id;
let run=await call('agent-builder/test',{id:s.saved_agent_id,content:'AL-C9500DN에서 IP 주소와 서브넷 마스크 조합이 잘못됐다는 오류가 나요. 어떻게 해야 하나요?'});
report.initialRun=run;await writeFile(documentationPath('MANUAL_WORKFLOW_CHECK.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify({stage:'guidance',status:run.status,answer:run.answer,error:run.error,question:run.question,evidence:run.evidence?.map((e:any)=>({page:e.page,quote:e.quote})),steps:run.steps.map((s:any)=>s.node)}));assert.equal(run.status,'needs_input');assert.ok(run.evidence.some((e:any)=>e.page===125));
const runId=run.id;run=await call('agent-builder/resume_run',{id:run.id,content:'IP 주소와 기본 게이트웨이 설정을 확인했는데도 같은 오류가 나요.'});report.followupRun=run;await writeFile(documentationPath('MANUAL_WORKFLOW_CHECK.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify({stage:'unresolved',status:run.status,id:run.id,answer:run.answer,error:run.error,steps:run.steps.map((s:any)=>s.node)}));assert.equal(run.id,runId);assert.equal(run.status,'succeeded');assert.match(run.answer,/문의/);assert.doesNotMatch(run.answer,/설정 변경 완료|설정을 변경했습니다/);
