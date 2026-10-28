import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {runGapExperiment,type GapReview} from './tool-lab/gap-bridge';
import {demoDataset,fixtureGenerator} from './tool-lab/examples';
import type {Generator} from './tool-lab/generator';

const gap=()=>({protocol:'a4b.tool-gap.v1',session_id:'12345678-1234-4234-8234-123456789abc',revision:2,
 gap_id:'gap_'+'a'.repeat(24),state_fingerprint:'a'.repeat(64),catalog_sha256:'b'.repeat(64),
 reported_missing_actions:['달력 날짜 정규화'],catalog:[{id:'read_table',version:1,description:'표 읽기'},{id:'filter_sort',version:1,description:'조건'}],
 status:'needs_spec_review',capability_status:'unverified',lab_scope:'pure_json_transform',automatic_generation:false,
 automatic_registration:false,workflow_execution_allowed:false,creation_intent:{protocol:'a4b.tool-gap-choice.v1',decision_id:'12345678-1234-4234-8234-123456789def',choice:'create_tool',gap_id:'gap_'+'a'.repeat(24),state_fingerprint:'a'.repeat(64),revision:2,scope:'code_generation_and_lab_tests_only',workflow_execution_allowed:false}});
function setup(){
 const dataset=demoDataset();dataset.tasks=[dataset.tasks[0]];const current=gap();
 const review:GapReview={protocol:'a4b.tool-gap-review.v1',session_id:current.session_id,revision:current.revision,gap_id:current.gap_id,
 state_fingerprint:current.state_fingerprint,catalog_sha256:current.catalog_sha256,classification:'actual_gap',action_index:0,
 reviewer:'연결 시험',review_basis:'automated_development',reason:'자동 개발용 예제이며 실제 기능 누락의 사람 검수 아님',
 compared_tool_ids:current.catalog.map(t=>t.id),existing_tool_ids:[],effects:['json_transform'],reviewed_spec:structuredClone(dataset.tasks[0].spec)};
 return {dataset,current,review};
}
async function temp(fn:(path:string)=>Promise<void>){const p=await mkdtemp(join(tmpdir(),'a4b-gap-'));try{await fn(p);}finally{await rm(p,{recursive:true,force:true});}}
const never:Generator={identity:{provider:'fixture',model:'never',settings:{}},async invoke(){assert.fail('모델을 호출하면 안 됨');}};

test('gap bridge runs isolated tests, records source/session lineage, keeps candidate unregistered',()=>temp(async outputDirectory=>{
 const {dataset,current,review}=setup();const requests:string[]=[];const fixture=fixtureGenerator();
 const generator:Generator={...fixture,async invoke(messages){requests.push(JSON.stringify(messages));return fixture.invoke(messages);}};
 const result=await runGapExperiment(review,dataset,generator,{loadCurrentGap:async()=>current,outputDirectory,maxRepairs:0});
 assert.equal(result.status,'passed_lab_tests');assert.equal(result.registration_status,'unregistered');
 assert.equal(result.workflow_execution_allowed,false);assert.equal(result.automatic_promotion,false);assert.equal(result.model_calls,0);
 const record=JSON.parse(await readFile(join(result.lab_directory,'gap-review.json'),'utf8'));
 assert.equal(record.state_fingerprint,current.state_fingerprint);
 for(const request of requests){assert.ok(!request.includes('20240229'));assert.ok(!request.includes('20260229'));}
 assert.ok(await readFile(join(result.lab_directory,'source-gap-bridge.json'),'utf8'));
}));
for(const classification of ['existing_tools','unclear','external_integration','input_problem'] as const){
 test('no code generation for '+classification,()=>temp(async outputDirectory=>{
  const {dataset,current,review}=setup();review.classification=classification;
  await assert.rejects(runGapExperiment(review,dataset,never,{loadCurrentGap:async()=>current,outputDirectory}),/재설계/);
 }));
}
for(const key of ['revision','gap_id','state_fingerprint','catalog_sha256'] as const){
 test('stale '+key+' rejected before model call',()=>temp(async outputDirectory=>{
  const {dataset,current,review}=setup();const newer={...current,[key]:key==='revision'?3:key==='gap_id'?'gap_'+'c'.repeat(24):'c'.repeat(64)};
  await assert.rejects(runGapExperiment(review,dataset,never,{loadCurrentGap:async()=>newer,outputDirectory}),/변경/);
 }));
}
for(const effect of ['network','filesystem','database','external_delivery'] as const){
 test('external effect '+effect+' cannot enter transform lab',()=>temp(async outputDirectory=>{
  const {dataset,current,review}=setup();review.effects=['json_transform',effect];
  await assert.rejects(runGapExperiment(review,dataset,never,{loadCurrentGap:async()=>current,outputDirectory}),/범위/);
 }));
}
test('full catalog comparison and matching reviewed specification required',()=>temp(async outputDirectory=>{
 const {dataset,current,review}=setup();review.compared_tool_ids=['read_table'];
 await assert.rejects(runGapExperiment(review,dataset,never,{loadCurrentGap:async()=>current,outputDirectory}),/전체/);
 review.compared_tool_ids=['read_table','filter_sort'];review.reviewed_spec={};
 await assert.rejects(runGapExperiment(review,dataset,never,{loadCurrentGap:async()=>current,outputDirectory}),/명세/);
}));
test('unresolved conditions stop before model invocation',()=>temp(async outputDirectory=>{
 const {dataset,current,review}=setup();dataset.tasks[0].spec.unresolved_conditions=['날짜 범위'];review.reviewed_spec=structuredClone(dataset.tasks[0].spec);
 const result=await runGapExperiment(review,dataset,never,{loadCurrentGap:async()=>current,outputDirectory});
 assert.equal(result.status,'lab_not_passed');assert.equal(result.experiment.blocked_tasks,1);assert.equal(result.model_calls,0);
}));
test('state changed during model call leaves only stale research record',()=>temp(async outputDirectory=>{
 const {dataset,current,review}=setup();let loads=0;
 const result=await runGapExperiment(review,dataset,fixtureGenerator(),{loadCurrentGap:async()=>++loads===1?current:{...current,revision:3},outputDirectory,maxRepairs:0});
 assert.equal(result.experiment.passed_within_budget_tasks,1);assert.equal(result.status,'stale');
 assert.equal(result.current_state_revalidated,false);assert.equal(result.workflow_execution_allowed,false);
}));
test('automatic development review cannot assert human-reviewed gold',()=>temp(async outputDirectory=>{
 const {dataset,current,review}=setup();dataset.human_reviewed=true;
 await assert.rejects(runGapExperiment(review,dataset,never,{loadCurrentGap:async()=>current,outputDirectory}),/사람 검수/);
}));

test('v3 quality prompt preserves public specification and keeps hidden cases private',async()=>{
 const {generationMessages,SYSTEM}=await import('./tool-lab/generator');
 const task=demoDataset().tasks[0];
 const baseline=generationMessages(task);const candidate=generationMessages(task,undefined,'v3');
 assert.equal(baseline[0].content.startsWith(SYSTEM),true);
 assert.equal(candidate[1].content,baseline[1].content);
 assert.notEqual(candidate[0].content,baseline[0].content);
 for(const request of candidate)for(const value of ['20240229','20260229','invalid_day','century_invalid'])assert.ok(!request.content.includes(value));
});
test('v3 manifest records explicit prompt and unsupported versions stop before generation',()=>temp(async outputDirectory=>{
 const {dataset}=setup();
 const result=await (await import('./tool-lab/experiment')).runExperiment(dataset,fixtureGenerator(),{outputDirectory,maxRepairs:0,promptVersion:'v3'});
 const manifest=JSON.parse(await readFile(join(result.directory,'manifest.json'),'utf8'));
 assert.equal(manifest.prompt_version,'v3');
 await assert.rejects((await import('./tool-lab/experiment')).runExperiment(dataset,never,{outputDirectory,promptVersion:'v9' as any}),/v2\/v3/);
}));
test('model comparison refuses mixing different prompt interventions',()=>temp(async outputDirectory=>{
 const {dataset}=setup();const {runExperiment}=await import('./tool-lab/experiment');
 const a=await runExperiment(dataset,fixtureGenerator(),{outputDirectory,maxRepairs:0,promptVersion:'v2'});
 const b=await runExperiment(dataset,fixtureGenerator(),{outputDirectory,maxRepairs:0,promptVersion:'v3'});
 await assert.rejects((await import('./tool-lab/compare')).compareExperiments([a.directory,b.directory]),/다른 실험/);
}));
test('v4 repair keeps original spec last without exposing hidden oracle',async()=>{
 const {generationMessages}=await import('./tool-lab/generator');const task=demoDataset().tasks[2];
 const messages=generationMessages(task,'실패한 이전 코드','v4');
 const body=JSON.parse(messages[1].content);
 assert.deepEqual(Object.keys(body),['repair_feedback','authoritative_task']);
 assert.deepEqual(body.authoritative_task.spec,task.spec);
 assert.deepEqual(body.authoritative_task.examples,task.cases.filter(c=>c.visibility==='example'));
 for(const hidden of task.cases.filter(c=>c.visibility==='hidden'))assert.ok(!messages[1].content.includes('"'+hidden.id+'"'));
});
test('v5 literal hints cite public instruction and leave unmatched policies unfilled',async()=>{
 const {literalPolicyHints,generationMessages}=await import('./tool-lab/generator');
 const task=demoDataset().tasks[2];const hints=literalPolicyHints(task.spec.instruction);
 assert.ok(hints.some(h=>h.kind==='rounding'&&h.javascript_primitive==='Math.round'));
 for(const h of hints)assert.ok(task.spec.instruction.includes(h.source_quote as string));
 assert.deepEqual(literalPolicyHints('반올림 정책과 범위를 아직 정하지 않았음'),[]);
 assert.ok(!literalPolicyHints('원 단위 반올림(0.5 올림)은 금지하며 반올림하지 말 것').some(h=>h.kind==='rounding'));
 const body=JSON.parse(generationMessages(task,undefined,'v5')[1].content);
 assert.deepEqual(body.authoritative_task.spec,task.spec);
 assert.deepEqual(body.authoritative_task.literal_policy_hints,hints);
});
test('v5 repair shares only hidden failure categories, never private cases or output values',()=>temp(async outputDirectory=>{
 const {dataset}=setup();dataset.tasks=[demoDataset().tasks[2]];
 dataset.tasks[0].cases[1]={id:'private_sample_175',visibility:'hidden',input:[{amount:1234565}],expectation:{kind:'rows',rows:[{amount:1234565,tax:123457,total:1358022}]}};
 const calls:string[]=[];
 const generator:Generator={identity:{provider:'fixture',model:'wrong-rounding',settings:{}},async invoke(messages){
  calls.push(JSON.stringify(messages));return {content:JSON.stringify({status:'candidate',code:'function transform(rows) { return rows.map(r=>({amount:r.amount,tax:Math.floor(r.amount/10),total:r.amount+Math.floor(r.amount/10)})); }',explanation:'검사',questions:[]})};
 }};
 const result=await (await import('./tool-lab/experiment')).runExperiment(dataset,generator,{outputDirectory,promptVersion:'v5',maxRepairs:1});
 assert.equal(result.generation_calls,2);
 assert.ok(calls[1].includes('hidden_failure_categories'));assert.ok(calls[1].includes('wrong_result'));
 for(const c of calls)for(const secret of ['private_sample_175','1234565','123457','1358022'])assert.ok(!c.includes(secret));
}));
test('v6 repair removes failed code anchor and keeps only public source rules',async()=>{
 const {generationMessages}=await import('./tool-lab/generator');const task=demoDataset().tasks[2];
 const feedback=JSON.stringify({previous_code:'do_not_copy_this_bad_function',hidden_failure_categories:['wrong_result'],hidden_feedback:'명세 불일치'});
 const messages=generationMessages(task,feedback,'v6');
 assert.ok(!JSON.stringify(messages).includes('do_not_copy_this_bad_function'));
 assert.deepEqual(JSON.parse(messages[1].content).authoritative_task.spec,task.spec);
});
test('literal hints reject impossible numeric bounds and negated domains',async()=>{
 const {literalPolicyHints}=await import('./tool-lab/generator');
 assert.deepEqual(literalPolicyHints('0~999999999999999999999 정수 원'),[]);
 assert.deepEqual(literalPolicyHints('선택적 + 뒤 숫자 15~8개'),[]);
 assert.deepEqual(literalPolicyHints('0~100 정수 원이 아니다'),[]);
});
test('v7 literal full-string rule keeps conditional 아니면 and rejects disallowed characters',async()=>{
 const {literalPolicyHints}=await import('./tool-lab/generator');
 const task=demoDataset().tasks[1];const rule=literalPolicyHints(task.spec.instruction,true).find(h=>h.kind==='whole_string_validation');
 assert.ok(rule);assert.ok(task.spec.instruction.includes(rule.source_quote as string));
 const regex=new RegExp(rule.javascript_regexp_source as string);
 assert.ok(regex.test('+821012345678'));assert.ok(regex.test('01012345678'));
 for(const value of ['a01012345678','010+12345678','(010)12345678'])assert.equal(regex.test(value),false);
});
test('conditional transport keeps candidate code and questions mutually consistent',async()=>{
 const {remoteGenerator}=await import('./tool-lab/generator');let sent:any;
 const fetcher:typeof fetch=async(_url,options)=>{sent=JSON.parse(String(options?.body));return new Response(JSON.stringify({message:{content:'{}'}}));};
 await remoteGenerator({provider:'ollama',model:'test',conditionalStatus:true},fetcher).invoke([]);
 assert.equal(sent.format.anyOf.length,3);
 const candidate=sent.format.anyOf.find((x:any)=>x.properties.status.enum[0]==='candidate');
 assert.equal(candidate.properties.questions.maxItems,0);assert.equal(candidate.properties.code.type,'string');
 const clarification=sent.format.anyOf.find((x:any)=>x.properties.status.enum[0]==='needs_clarification');
 assert.equal(clarification.properties.code.type,'null');assert.equal(clarification.properties.questions.minItems,1);
});
for(const intent of [undefined,null]){
 test('gap creation requires explicit current user selection '+String(intent),()=>temp(async outputDirectory=>{
  const {dataset,current,review}=setup();(current as any).creation_intent=intent;
  await assert.rejects(runGapExperiment(review,dataset,never,{loadCurrentGap:async()=>current,outputDirectory}),/제작을 선택/);
 }));
}
test('revoked creation intent during generation marks results stale',()=>temp(async outputDirectory=>{
 const {dataset,current,review}=setup();let loads=0;
 const result=await runGapExperiment(review,dataset,fixtureGenerator(),{loadCurrentGap:async()=>++loads===1?current:{...current,creation_intent:null},outputDirectory,maxRepairs:0});
 assert.equal(result.status,'stale');assert.equal(result.workflow_execution_allowed,false);
}));
test('public choice request is strict and never accepts execution permission',async()=>{
 const {parseToolGapChoiceRequest}=await import('./tool-gap-contract');
 const good={id:'12345678-1234-4234-8234-123456789abc',revision:2,requestId:'12345678-1234-4234-8234-123456789def',gapId:'gap_'+'a'.repeat(24),stateFingerprint:'a'.repeat(64),choice:'create_tool'};
 assert.deepEqual(parseToolGapChoiceRequest(good),good);
 for(const change of [{revision:true},{revision:-1},{revision:Infinity},{choice:'yes'},{workflow_execution_allowed:true},{requestId:'bad'}])assert.throws(()=>parseToolGapChoiceRequest({...good,...change}));
});
test('published choice requests match the actual frontend contract',async()=>{
 const {parseToolGapChoiceRequest}=await import('./tool-gap-contract');
 for(const name of ['01-defer','02-redesign_workflow','03-create_tool']){
  const request=JSON.parse(await readFile(new URL('../../docs/tool-gap-choice-handoff/examples/'+name+'.request.json',import.meta.url),'utf8'));
  assert.deepEqual(parseToolGapChoiceRequest(request),request);
  const response=JSON.parse(await readFile(new URL('../../docs/tool-gap-choice-handoff/examples/'+name+'.response.json',import.meta.url),'utf8'));
  assert.equal(response.revision,request.revision+1);
  assert.equal(response.tool_gap_choice.selection.choice,request.choice);
 }
});
test('redesign decision request never includes implicit execution approval',async()=>{
 const {parseRedesignDecisionRequest}=await import('./tool-gap-contract');
 const request={id:'12345678-1234-4234-8234-123456789abc',revision:3,requestId:'12345678-1234-4234-8234-123456789def',proposalId:'12345678-1234-4234-8234-123456789aaa',decision:'accept'};
 assert.deepEqual(parseRedesignDecisionRequest(request),request);
 for(const bad of [{decision:'generate'},{revision:true},{allows_execution:true}])assert.throws(()=>parseRedesignDecisionRequest({...request,...bad}));
});
test('published redesign confirmation matches current request contract',async()=>{
 const {parseRedesignDecisionRequest}=await import('./tool-gap-contract');
 const request=JSON.parse(await readFile(new URL('../../docs/tool-gap-choice-handoff/redesign-examples/02-accept.request.json',import.meta.url),'utf8'));
 assert.deepEqual(parseRedesignDecisionRequest(request),request);
 const response=JSON.parse(await readFile(new URL('../../docs/tool-gap-choice-handoff/redesign-examples/02-accept.response.json',import.meta.url),'utf8'));
 assert.equal(response.revision,request.revision+1);assert.equal(response.redesign_proposal.status,'accepted');
 assert.equal(response.redesign_generation.requires_condition_recheck,true);assert.equal(response.redesign_generation.allows_execution,false);
});
