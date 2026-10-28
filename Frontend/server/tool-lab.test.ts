import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,readdir,rm,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {demoDataset,fixtureGenerator} from './tool-lab/examples';
import {parseDataset,parseGeneration,type Task} from './tool-lab/contracts';
import {generationMessages,remoteGenerator,type Generator} from './tool-lab/generator';
import {evaluateCandidate,runExperiment} from './tool-lab/experiment';
import {compareExperiments} from './tool-lab/compare';
import {executableSource,SourceContractError} from './tool-lab/source-contract';
import {runCode} from './sandbox';

const temporary=async(fn:(directory:string)=>Promise<void>)=>{
 const directory=await mkdtemp(join(tmpdir(),'a4b-tool-lab-'));
 try{await fn(directory);}finally{await rm(directory,{recursive:true,force:true});}
};
const functionCode=(body:string)=>'function transform(rows) {\n'+body.replaceAll('$input.all()','rows.map(json=>({json}))')+'\n}';
const candidate=(body:string)=>({content:JSON.stringify({status:'candidate',code:functionCode(body),explanation:'시험',questions:[]})});

test('tool lab validates row contracts and requires hidden normal-result tests',()=>{
 const data=demoDataset();assert.equal(data.tasks.length,3);
 const noHidden=structuredClone(data);noHidden.tasks[0].cases=noHidden.tasks[0].cases.filter(c=>c.visibility==='example');
 assert.throws(()=>parseDataset(noHidden));
 const wrongSchema=structuredClone(data);wrongSchema.tasks[0].cases[0].expectation={kind:'rows',rows:[{wrong:'value'}]};
 assert.throws(()=>parseDataset(wrongSchema),/열/);
 const duplicate=structuredClone(data);duplicate.tasks[0].spec.input_fields.push(duplicate.tasks[0].spec.input_fields[0]);
 assert.throws(()=>parseDataset(duplicate),/중복/);
});

test('tool lab sends only public examples and specification to model',()=>{
 const task=demoDataset().tasks[0];
 const text=JSON.stringify(generationMessages(task));
 assert.ok(text.includes('20261026'));
 for(const hidden of ['20240229','20260229','invalid_day','leap'])assert.ok(!text.includes(hidden));
});

test('tool lab refuses executable code on clarification and scope responses',()=>{
 assert.throws(()=>parseGeneration(JSON.stringify({status:'needs_clarification',code:'return [];',explanation:'확인',questions:['기준은?']})));
 assert.throws(()=>parseGeneration(JSON.stringify({status:'candidate',code:'return [];',explanation:'확인',questions:['기준은?']})));
 assert.throws(()=>parseGeneration(JSON.stringify({status:'needs_clarification',code:null,explanation:'확인',questions:[]})));
 assert.equal(parseGeneration(JSON.stringify({status:'out_of_scope',code:null,explanation:'외부 전송 미지원',questions:[]})).status,'out_of_scope');
});

test('tool lab handwritten reference passes and boundary bug fails independently',()=>temporary(async outputDirectory=>{
 const good=await runExperiment(demoDataset(),fixtureGenerator(),{outputDirectory,maxRepairs:0});
 const bad=await runExperiment(demoDataset(),fixtureGenerator(true),{outputDirectory,maxRepairs:0});
 assert.equal(good.passed_within_budget_tasks,3);assert.equal(bad.passed_within_budget_tasks,2);
 assert.equal(good.model_calls,0);assert.equal(good.generation_calls,3);assert.equal(good.fixture_run,true);
 const failed=bad.tasks.find(t=>t.task_id==='tax_rounding')!;
 assert.equal(failed.passed_tests,false);assert.equal(failed.attempts[0].cases.find(c=>c.id==='half_rounding')!.failure,'wrong_result');
 assert.equal(good.automatic_promotion,false);
 const record=JSON.parse(await readFile(join(good.directory,'date_normalize-1-candidate.json'),'utf8'));
 assert.equal(record.registration_status,'unregistered');
 assert.equal(createHash('sha256').update(record.code).digest('hex'),record.code_sha256);
 assert.ok((await readdir(good.directory)).includes('source-sandbox.json'));
}));

test('tool lab never calls a model when business conditions are unresolved',()=>temporary(async outputDirectory=>{
 const data=demoDataset();data.tasks=[data.tasks[0]];data.tasks[0].spec.unresolved_conditions=['지원할 날짜 형식'];
 let calls=0;const generator:Generator={identity:{provider:'fixture',model:'never',settings:{}},async invoke(){calls++;throw new Error('호출되면 안 됨');}};
 const result=await runExperiment(data,generator,{outputDirectory});
 assert.equal(calls,0);assert.equal(result.task_count,1);assert.equal(result.blocked_tasks,1);assert.equal(result.passed_within_budget_tasks,0);
}));

test('tool lab keeps model errors in denominator and limits generation attempts',()=>temporary(async outputDirectory=>{
 const data=demoDataset();data.tasks=[data.tasks[0]];
 let calls=0;const generator:Generator={identity:{provider:'fixture',model:'error',settings:{}},async invoke(){calls++;throw new Error('do-not-log-secret');}};
 const result=await runExperiment(data,generator,{outputDirectory,maxRepairs:1});
 assert.equal(calls,2);assert.equal(result.task_count,1);assert.equal(result.generation_error_attempts,2);
 assert.equal(result.passed_within_budget_tasks,0);
 const log=await readFile(join(result.directory,'summary.json'),'utf8');assert.ok(!log.includes('do-not-log-secret'));
}));

test('tool lab repair feedback does not reveal hidden input, answer or actual value',()=>temporary(async outputDirectory=>{
 const data=demoDataset();data.tasks=[data.tasks[2]];
 data.tasks[0].cases[1].id='private-case';data.tasks[0].cases[1].input=[{amount:1234565}];
 data.tasks[0].cases[1].expectation={kind:'rows',rows:[{amount:1234565,tax:123457,total:1358022}]};
 const calls:string[]=[];
 const generator:Generator={identity:{provider:'fixture',model:'repair',settings:{}},async invoke(messages){
  calls.push(JSON.stringify(messages));return candidate('return $input.all().map(({json:r})=>({amount:r.amount,tax:Math.floor(r.amount/10),total:r.amount+Math.floor(r.amount/10)}));');
 }};
 const result=await runExperiment(data,generator,{outputDirectory,maxRepairs:1});
 assert.equal(calls.length,2);assert.equal(result.passed_within_budget_tasks,0);
 for(const request of calls)for(const secret of ['private-case','1234565','123457','1358022'])assert.ok(!request.includes(secret));
}));

test('tool lab errors must match expected error instead of counting any crash as success',async()=>{
 const task=demoDataset().tasks[2];task.cases=[task.cases.find(c=>c.id==='negative')!];
 assert.equal((await evaluateCandidate(task,functionCode("throw new Error('다른 오류');")))[0].passed,false);
 assert.equal((await evaluateCandidate(task,functionCode("throw new Error('금액 형식 오류');")))[0].passed,true);
});

test('tool lab output contract rejects plausible values in wrong fields',async()=>{
 const task=demoDataset().tasks[0];task.cases=[task.cases[0]];
 const result=await evaluateCandidate(task,functionCode("return [{other:'2026-10-26'}];"));
 assert.equal(result[0].passed,false);assert.equal(result[0].failure,'output_contract');
});

test('tool lab QuickJS has no host process, module loader, network or Buffer',async()=>{
 const task:Task={spec:{...demoDataset().tasks[0].spec,output_fields:[{name:'access',type:'string',nullable:false}]},
  cases:[{id:'access',visibility:'hidden',input:[{date:'x'}],expectation:{kind:'rows',rows:[{access:'undefined,undefined,undefined,undefined'}]}}]};
 const code="return [{access:[typeof process,typeof require,typeof fetch,typeof Buffer].join(',')}];";
 assert.equal((await evaluateCandidate(task,functionCode(code)))[0].passed,true);
 const escaped=await evaluateCandidate(task,functionCode("return [{access:globalThis.constructor.constructor('return process')().env}];"));
 assert.equal(escaped[0].passed,false);assert.equal(escaped[0].failure,'runtime_error');
});

test('tool lab stops non-terminating candidate rather than hanging the evaluator',async()=>{
 const task=demoDataset().tasks[0];task.cases=[task.cases[0]];
 const start=performance.now();const result=await evaluateCandidate(task,functionCode('while(true){}'));
 assert.equal(result[0].passed,false);assert.equal(result[0].failure,'runtime_error');
 assert.ok(performance.now()-start<8000);
});

test('tool lab local transport uses schema and never embeds credentials in messages',async()=>{
 let sent:RequestInit|undefined;let url='';
 const fake=async(input:unknown,init?:RequestInit)=>{url=String(input);sent=init;return new Response(JSON.stringify({model:'qwen-test',message:{content:'{}'},prompt_eval_count:12,eval_count:7}));};
 const generator=remoteGenerator({provider:'ollama',model:'qwen-test',endpoint:'http://127.0.0.1:11434/v1'},fake as typeof fetch);
 const reply=await generator.invoke(generationMessages(demoDataset().tasks[0]));
 assert.equal(url,'http://127.0.0.1:11434/api/chat');assert.equal(reply.usage!.input_tokens,12);
 const body=JSON.parse(String(sent!.body));assert.equal(body.format.additionalProperties,false);assert.equal(body.options.seed,42);
 assert.equal(sent!.redirect,'error');assert.ok(!JSON.stringify(generator.identity).includes('apiKey'));
});

test('tool lab OpenAI adapter explicitly needs key and masks provider error body',async()=>{
 assert.throws(()=>remoteGenerator({provider:'openai',model:'chosen-model'}),/API_KEY/);
 assert.throws(()=>remoteGenerator({provider:'openai',model:'chosen-model',apiKey:'test-secret',endpoint:'https://other.example/v1'}),/공식/);
 const fake=async()=>new Response('provider-body-with-secret',{status:401});
 const generator=remoteGenerator({provider:'openai',model:'chosen-model',apiKey:'test-secret'},fake as typeof fetch);
 await assert.rejects(generator.invoke([]),/^Error: 모델 요청 실패: HTTP 401$/);
 assert.ok(!JSON.stringify(generator.identity).includes('test-secret'));
});

test('tool lab clarification does not run or register candidate',()=>temporary(async outputDirectory=>{
 const data=demoDataset();data.tasks=[data.tasks[0]];
 const generator:Generator={identity:{provider:'fixture',model:'clarify',settings:{}},async invoke(){return {content:JSON.stringify({status:'needs_clarification',code:null,explanation:'형식 확인',questions:['날짜 형식은 무엇인가요?']})};}};
 const result=await runExperiment(data,generator,{outputDirectory});
 assert.equal(result.tasks[0].status,'needs_clarification');assert.equal(result.tasks[0].attempts[0].cases.length,0);
 assert.ok(!(await readdir(result.directory)).some(name=>name.endsWith('-candidate.json')));
}));

test('tool lab OpenAI and vLLM adapters provide code schema and separate authorization',async()=>{
 for(const provider of ['openai','vllm'] as const){
  let sent:RequestInit|undefined;
  const fake=async(_url:unknown,init?:RequestInit)=>{sent=init;return new Response(JSON.stringify({model:'chosen-model',choices:[{message:{content:candidate('return [];').content}}],usage:{prompt_tokens:10,completion_tokens:20}}));};
  const generator=remoteGenerator({provider,model:'chosen-model',apiKey:'test-secret',...(provider==='vllm'?{endpoint:'http://127.0.0.1:8000/v1'}:{})},fake as typeof fetch);
  const response=await generator.invoke(generationMessages(demoDataset().tasks[0]));
  const body=JSON.parse(String(sent!.body));
  assert.equal(body.response_format.json_schema.strict,true);assert.equal(body.max_completion_tokens,6000);
  assert.equal((sent!.headers as Record<string,string>).Authorization,'Bearer test-secret');
  assert.ok(!String(sent!.body).includes('test-secret'));assert.equal(parseGeneration(response.content).status,'candidate');
 }
});

async function comparisonFixtures(root:string){
 const paths=[join(root,'one'),join(root,'two')];
 for(const [index,directory] of paths.entries()){
  await mkdir(directory);
  const manifest={protocol:'a4b-tool-lab-v1',run_id:String(index),dataset_sha256:'same',max_repairs:1,source_sha256:{runner:'same'},runtime:{same:true},human_reviewed:false};
  const summary={protocol:manifest.protocol,run_id:String(index),dataset_sha256:'same',fixture_run:true,generator:{provider:'fixture',model:String(index)},task_count:3};
  await writeFile(join(directory,'manifest.json'),JSON.stringify(manifest));await writeFile(join(directory,'summary.json'),JSON.stringify(summary));
 }
 return paths;
}
test('tool lab comparison rejects different datasets or repair budgets',()=>temporary(async directory=>{
 const paths=await comparisonFixtures(directory);
 assert.equal((await compareExperiments(paths)).runs.length,2);
 const file=join(paths[1],'manifest.json'),manifest=JSON.parse(await readFile(file,'utf8'));
 manifest.max_repairs=2;await writeFile(file,JSON.stringify(manifest));
 await assert.rejects(compareExperiments(paths),/수정 한도/);
 manifest.max_repairs=1;manifest.dataset_sha256='different';await writeFile(file,JSON.stringify(manifest));
 await assert.rejects(compareExperiments(paths),/평가 자료/);
}));
test('tool lab comparison refuses to mix fixture results with live model results',()=>temporary(async directory=>{
 const paths=await comparisonFixtures(directory),file=join(paths[1],'summary.json');
 const summary=JSON.parse(await readFile(file,'utf8'));summary.fixture_run=false;await writeFile(file,JSON.stringify(summary));
 await assert.rejects(compareExperiments(paths),/섞어/);
}));

test('tool lab v2 fixes invocation while rejecting top-level side effects and wrong source forms',async()=>{
 const task=demoDataset().tasks[0];task.cases=[task.cases[0]];
 const code="function transform(input) { return input.map(row=>({date:row.date.slice(0,4)+'-'+row.date.slice(4,6)+'-'+row.date.slice(6,8)})); }";
 assert.equal((await evaluateCandidate(task,code))[0].passed,true);
 for(const source of ['return [];','function process(rows) {return rows;}','function $input.all() {return [];}',
  'function transform(rows) {return rows;}\ntransform([]);','const n=1; function transform(rows) {return rows;}',
  'async function transform(rows) {return rows;}','function* transform(rows) {yield rows;}',
  'function transform(rows=[]) {return rows;}','function transform(rows) {return $input.all();}',
  'function transform(rows: any) {return rows;}','function transform(rows) {\\n return rows;\\n}']){
  assert.throws(()=>executableSource(source),SourceContractError);
 }
});

test('tool lab v2 gives explicit source-contract feedback and retains original spec',()=>temporary(async outputDirectory=>{
 const data=demoDataset();data.tasks=[data.tasks[2]];let calls=0;
 const reference=fixtureGenerator();
 const generator:Generator={identity:{provider:'fixture',model:'source-repair',settings:{}},async invoke(messages){
  calls++;const request=JSON.parse(messages[1].content);
  assert.deepEqual(request.task.spec,data.tasks[0].spec);
  if(calls===1)return {content:JSON.stringify({status:'candidate',code:'function wrong(rows) {return rows;}',explanation:'시험',questions:[]})};
  const feedback=JSON.parse(request.repair_feedback);
  assert.equal(feedback.validator_issue.stage,'source_contract');assert.match(feedback.validator_issue.message,/transform/);
  assert.ok(feedback.previous_code.includes('function wrong'));
  assert.ok(!JSON.stringify(feedback).includes('half_rounding'));
  return reference.invoke(messages);
 }};
 const result=await runExperiment(data,generator,{outputDirectory,maxRepairs:1});
 assert.equal(result.first_pass_tasks,0);assert.equal(result.passed_within_budget_tasks,1);assert.equal(result.source_error_attempts,1);
 assert.equal(result.tasks[0].attempts[0].cases.length,0);
}));

test('tool lab v2 tells the model why candidate plus questions violates the response contract',()=>temporary(async outputDirectory=>{
 const data=demoDataset();data.tasks=[data.tasks[2]];let calls=0;
 const reference=fixtureGenerator();
 const generator:Generator={identity:{provider:'fixture',model:'schema-repair',settings:{}},async invoke(messages){
  calls++;
  if(calls===1)return {content:JSON.stringify({status:'candidate',code:'function transform(rows) {return rows;}',explanation:'시험',questions:['이미 정한 기준 재확인']})};
  const request=JSON.parse(messages[1].content),feedback=JSON.parse(request.repair_feedback);
  assert.equal(feedback.validator_issue.stage,'response_contract');assert.match(feedback.validator_issue.message,/미정 질문/);
  assert.equal(feedback.previous_code,'function transform(rows) {return rows;}');
  return reference.invoke(messages);
 }};
 const result=await runExperiment(data,generator,{outputDirectory,maxRepairs:1});
 assert.equal(result.first_pass_tasks,0);assert.equal(result.passed_within_budget_tasks,1);
}));

test('tool lab startup allowance remains bounded and cannot remove the execution limit',async()=>{
 await assert.rejects(runCode('return [];',[],'runOnceForAllItems',{},{startupTimeoutMs:10001}),/2~10초/);
 await assert.rejects(runCode('return [];',[],'runOnceForAllItems',{},{startupTimeoutMs:0}),/2~10초/);
});

test('tool lab v2 records excessively nested source as a source-contract rejection',()=>{
 assert.throws(()=>executableSource('function transform(rows) {return '+ '('.repeat(1000)+'rows'+')'.repeat(1000)+';}'),SourceContractError);
 assert.throws(()=>executableSource(' '.repeat(20001)),SourceContractError);
});
