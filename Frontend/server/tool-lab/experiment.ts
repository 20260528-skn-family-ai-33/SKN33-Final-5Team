import {createHash,randomUUID} from 'node:crypto';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {isDeepStrictEqual} from 'node:util';
import {runCode} from '../sandbox';
import {PROTOCOL,parseDataset,parseGeneration,safeResponseIssue,rowsSchema,validateRows,type Task} from './contracts';
import {generationMessages,type Generator,type PromptVersion} from './generator';
import {CODE_CONTRACT,executableSource,SourceContractError} from './source-contract';

const sha=(value:string)=>createHash('sha256').update(value).digest('hex');
type CaseResult={id:string;visibility:'example'|'hidden';passed:boolean;milliseconds:number;
 actual?:unknown;error?:string;failure?:'runtime_error'|'output_contract'|'wrong_result'|'wrong_error'};
type ValidationIssue={stage:'model_transport'|'response_contract'|'source_contract';message:string};
export type Attempt={number:number;status:'passed_tests'|'failed_tests'|'generation_error'|'source_error'|'needs_clarification'|'out_of_scope';
 generation_ms:number;execution_ms:number;code_sha256?:string;response_model?:string;usage?:Record<string,number>;cases:CaseResult[];validation_issue?:ValidationIssue};
type TaskResult={task_id:string;status:string;first_pass:boolean;passed_tests:boolean;attempts:Attempt[]};

export async function evaluateCandidate(task:Task,code:string):Promise<CaseResult[]>{
 const executable=executableSource(code);
 const results:CaseResult[]=[];
 for(const c of task.cases){
  const start=performance.now();
  let actual:unknown;let runtimeError:string|undefined;
  try{
   // Generated source is only interpreted inside the existing QuickJS VM.
   // Each case gets a new worker/VM and receives only its input, never its oracle.
   actual=JSON.parse(JSON.stringify(await runCode(executable,c.input,'runOnceForAllItems',{},{startupTimeoutMs:10000})));
  }catch(error){runtimeError=error instanceof Error?error.message:'코드 실행 실패';}
  const result:CaseResult={id:c.id,visibility:c.visibility,passed:false,milliseconds:performance.now()-start};
  if(runtimeError!==undefined){
   result.error=runtimeError;
   result.passed=c.expectation.kind==='error'&&runtimeError===c.expectation.message;
   if(!result.passed)result.failure='runtime_error';
  }else if(c.expectation.kind==='error'){
   result.actual=actual;result.failure='wrong_error';
  }else{
   result.actual=actual;
   try{validateRows(rowsSchema.parse(actual),task.spec.output_fields);
    result.passed=isDeepStrictEqual(actual,c.expectation.rows);if(!result.passed)result.failure='wrong_result';
   }catch{result.failure='output_contract';}
  }
  results.push(result);
 }
 return results;
}

function repairFeedback(task:Task,previousCode:string|null,cases:CaseResult[],issue?:ValidationIssue,coarseCategories=false):string{
 const examples=cases.filter(c=>c.visibility==='example'&&!c.passed).map(c=>({
  id:c.id,failure:c.failure,error:c.error,actual:c.actual,
  expectation:task.cases.find(example=>example.id===c.id)!.expectation,
 }));
 return JSON.stringify({previous_code:previousCode,validator_issue:issue??null,public_example_failures:examples,
  ...(coarseCategories?{hidden_failure_categories:[...new Set(cases.filter(c=>c.visibility==='hidden'&&!c.passed).map(c=>c.failure))].sort()}:{}),
  hidden_feedback:cases.some(c=>c.visibility==='hidden'&&!c.passed)?'숨긴 평가에서 명세 불일치가 있습니다. 숨긴 입력·정답은 제공하지 않습니다.':null,
  instruction:'원래 spec이 확정된 요구입니다. previous_code는 잘못된 수정 대상이며 새 요구사항이 아닙니다. 명시된 기준을 다시 묻지 말고 function transform(rows) 선언 하나를 고쳐 반환하세요. 숨긴 입력·정답은 추측하거나 요청하지 마세요.'});
}

export async function runExperiment(value:unknown,generator:Generator,options:{outputDirectory:string;maxRepairs?:number;promptVersion?:PromptVersion;onProgress?:(message:string)=>void}){
 const dataset=parseDataset(value),maxRepairs=options.maxRepairs??1;
 const promptVersion=options.promptVersion??'v2';
 if(!['v2','v3','v4','v5','v6','v7'].includes(promptVersion))throw new Error('코드 생성 프롬프트는 v2/v3/v4/v5/v6/v7이어야 합니다.');
 if(!Number.isInteger(maxRepairs)||maxRepairs<0||maxRepairs>2)throw new Error('수정 횟수는 0~2회입니다.');
 const runId=randomUUID(),directory=join(options.outputDirectory,runId);
 await mkdir(directory,{recursive:true,mode:0o700});
 const save=async(name:string,value:unknown)=>writeFile(join(directory,name),JSON.stringify(value,null,2),{flag:'wx',mode:0o600});
 const sources:Record<string,string>={};
 for(const [name,url] of Object.entries({contracts:new URL('./contracts.ts',import.meta.url),generator:new URL('./generator.ts',import.meta.url),
  experiment:new URL('./experiment.ts',import.meta.url),source_contract:new URL('./source-contract.ts',import.meta.url),sandbox:new URL('../sandbox.ts',import.meta.url),binary:new URL('../binary.ts',import.meta.url),
  provenance:new URL('../provenance.ts',import.meta.url),worker_exit:new URL('../worker-exit.ts',import.meta.url)})){
  const source=await readFile(url,'utf8');sources[name]=sha(source);await save('source-'+name+'.json',{sha256:sources[name],source});
 }
 const lockText=await readFile(new URL('../../package-lock.json',import.meta.url),'utf8');
 const lock=JSON.parse(lockText);
 const dependencies=Object.fromEntries(Object.entries(lock.packages||{}).filter(([name])=>name.includes('quickjs')||name==='node_modules/typescript').map(([name,pkg])=>[name,(pkg as {version:string}).version]));
 const started=new Date().toISOString(),datasetHash=sha(JSON.stringify(dataset));
 await save('manifest.json',{protocol:PROTOCOL,run_id:runId,started_at:started,dataset_sha256:datasetHash,
  human_reviewed:dataset.human_reviewed,generator:generator.identity,max_repairs:maxRepairs,prompt_version:promptVersion,source_sha256:sources,
  runtime:{node:process.version,language:'javascript',code_contract:CODE_CONTRACT,engine:'existing QuickJS WASM worker',timeout_ms:2000,startup_timeout_ms:10000,vm_memory_mb:32,
   host_callbacks:false,network:false,filesystem:false,dependencies,dependency_lock_sha256:sha(lockText)},automatic_promotion:false,cost_usd:null});
 await save('dataset.json',dataset);
 const tasks:TaskResult[]=[];
 for(const task of dataset.tasks){
  const result:TaskResult={task_id:task.spec.id,status:'blocked_conditions',first_pass:false,passed_tests:false,attempts:[]};
  if(task.spec.unresolved_conditions.length){tasks.push(result);continue;}
  let feedback:string|undefined;
  for(let index=0;index<=maxRepairs;index++){
   options.onProgress?.(`${task.spec.title} · ${index+1}차 생성·검증`);
   const messages=generationMessages(task,feedback,promptVersion),stem=task.spec.id+'-'+(index+1);
   await save(stem+'-request.json',{messages});
   const start=performance.now();let generation:ReturnType<typeof parseGeneration>|undefined,previousCode:string|null=null;
   const attempt:Attempt={number:index+1,status:'generation_error',generation_ms:0,execution_ms:0,cases:[]};
   try{
    const reply=await generator.invoke(messages);
    attempt.generation_ms=performance.now()-start;attempt.usage=reply.usage;attempt.response_model=reply.model;
    await save(stem+'-response.json',reply);
    try{const value=JSON.parse(reply.content);if(typeof value.code==='string'&&value.code.length<=20000)previousCode=value.code;}catch{/* Keep malformed JSON out of repair code. */}
    try{generation=parseGeneration(reply.content);}
    catch(error){attempt.validation_issue={stage:'response_contract',message:safeResponseIssue(error)};}
   }catch{
    attempt.generation_ms=performance.now()-start;
    // Provider exceptions may contain credentials or response bodies. Do not log them.
    attempt.validation_issue={stage:'model_transport',message:'모델 연결 또는 응답에 실패했습니다. 서버 상태와 제한 시간을 확인하세요.'};
   }
   if(generation){
    if(generation.status==='candidate'){
     const code=generation.code!;attempt.code_sha256=sha(code);
     await save(stem+'-candidate.json',{protocol:PROTOCOL,task_id:task.spec.id,language:'javascript',code,
      code_contract:CODE_CONTRACT,entrypoint:'transform',code_sha256:attempt.code_sha256,model:generator.identity,registration_status:'unregistered'});
     const executionStart=performance.now();
     try{
      attempt.cases=await evaluateCandidate(task,code);
      attempt.status=attempt.cases.every(c=>c.passed)?'passed_tests':'failed_tests';
     }catch(error){
      if(!(error instanceof SourceContractError))throw error;
      attempt.status='source_error';attempt.validation_issue={stage:'source_contract',message:error.message};
     }
     attempt.execution_ms=performance.now()-executionStart;
    }else attempt.status=generation.status;
   }
   result.attempts.push(attempt);await save(stem+'-evaluation.json',attempt);
   result.status=attempt.status;result.first_pass=index===0&&attempt.status==='passed_tests'||result.first_pass;
   result.passed_tests=attempt.status==='passed_tests';
   if(attempt.status==='passed_tests'||attempt.status==='needs_clarification'||attempt.status==='out_of_scope')break;
   feedback=repairFeedback(task,previousCode,attempt.cases,attempt.validation_issue,['v5','v6','v7'].includes(promptVersion));
  }
  tasks.push(result);
 }
 const attempts=tasks.flatMap(t=>t.attempts);
 const summary={protocol:PROTOCOL,run_id:runId,directory,generator:generator.identity,dataset_sha256:datasetHash,task_count:tasks.length,
  first_pass_tasks:tasks.filter(t=>t.first_pass).length,passed_within_budget_tasks:tasks.filter(t=>t.passed_tests).length,
  blocked_tasks:tasks.filter(t=>t.status==='blocked_conditions').length,
  generation_error_attempts:attempts.filter(a=>a.status==='generation_error').length,generation_calls:attempts.length,
  source_error_attempts:attempts.filter(a=>a.status==='source_error').length,
  model_calls:generator.identity.provider==='fixture'?0:attempts.length,fixture_run:generator.identity.provider==='fixture',
  generation_ms:attempts.reduce((n,a)=>n+a.generation_ms,0),execution_ms:attempts.reduce((n,a)=>n+a.execution_ms,0),
  cost_usd:null,human_reviewed:dataset.human_reviewed,automatic_promotion:false,finished_at:new Date().toISOString(),tasks};
 await save('summary.json',summary);return summary;
}
