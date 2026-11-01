import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {isDeepStrictEqual} from 'node:util';
import {PROTOCOL} from './contracts';

/** Refuse incomparable experiments rather than silently mixing task sets. */
export async function compareExperiments(directories:string[]){
 if(directories.length<2||directories.length>10)throw new Error('비교할 실험 폴더를 2~10개 지정하세요.');
 const runs=[];
 for(const directory of directories){
  const manifest=JSON.parse(await readFile(join(directory,'manifest.json'),'utf8'));
  const summary=JSON.parse(await readFile(join(directory,'summary.json'),'utf8'));
  if(manifest.protocol!==PROTOCOL||summary.protocol!==PROTOCOL||manifest.run_id!==summary.run_id||
     manifest.dataset_sha256!==summary.dataset_sha256)throw new Error('실험 기록의 규격·ID·평가 자료가 맞지 않습니다.');
  runs.push({directory,manifest,summary});
 }
 const first=runs[0];
 for(const run of runs.slice(1)){
  if(run.manifest.dataset_sha256!==first.manifest.dataset_sha256||run.manifest.max_repairs!==first.manifest.max_repairs||
     (run.manifest.prompt_version??'v2')!==(first.manifest.prompt_version??'v2')||
     !isDeepStrictEqual(run.manifest.source_sha256,first.manifest.source_sha256)||
     !isDeepStrictEqual(run.manifest.runtime,first.manifest.runtime))throw new Error('평가 자료·수정 한도·실행 코드·환경이 다른 실험은 직접 비교할 수 없습니다.');
  if(run.summary.fixture_run!==first.summary.fixture_run)throw new Error('수동 응답 시연과 실제 모델 시험을 섞어 비교할 수 없습니다.');
 }
 return {protocol:PROTOCOL,dataset_sha256:first.manifest.dataset_sha256,fixture_only:first.summary.fixture_run,
  human_reviewed:first.manifest.human_reviewed,max_repairs:first.manifest.max_repairs,
  note:'같은 업무·평가·실행 환경의 관측 결과입니다. 모델별 추론 설정은 각 manifest에서 확인하세요. 시험 통과가 정식 등록이나 모든 입력의 정확성을 뜻하지 않습니다.',
  runs:runs.map(({directory,summary:s})=>({directory,provider:s.generator.provider,model:s.generator.model,
   task_count:s.task_count,first_pass_tasks:s.first_pass_tasks,passed_within_budget_tasks:s.passed_within_budget_tasks,
   blocked_tasks:s.blocked_tasks,generation_error_attempts:s.generation_error_attempts,generation_calls:s.generation_calls,
   source_error_attempts:s.source_error_attempts??0,
   model_calls:s.model_calls,generation_ms:s.generation_ms,execution_ms:s.execution_ms,cost_usd:s.cost_usd}))};
}
