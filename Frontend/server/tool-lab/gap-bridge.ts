/** Bridge trusted current builder state to isolated experiments; no registry/runtime writes. */
import {z} from 'zod';
import {isDeepStrictEqual} from 'node:util';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {parseDataset} from './contracts';
import {runExperiment} from './experiment';
import type {Generator,PromptVersion} from './generator';

const hash=z.string().regex(/^[a-f0-9]{64}$/);
const referenceSchema=z.object({session_id:z.string().uuid(),revision:z.number().int().nonnegative(),
 gap_id:z.string().regex(/^gap_[a-f0-9]{24}$/),state_fingerprint:hash,catalog_sha256:hash});
export const gapSchema=referenceSchema.extend({protocol:z.literal('a4b.tool-gap.v1'),
 creation_intent:z.object({protocol:z.literal('a4b.tool-gap-choice.v1'),decision_id:z.string().uuid(),
  choice:z.literal('create_tool'),gap_id:z.string(),state_fingerprint:hash,revision:z.number().int(),
  scope:z.literal('code_generation_and_lab_tests_only'),workflow_execution_allowed:z.literal(false)}).strict().nullable().optional(),
 status:z.literal('needs_spec_review'),capability_status:z.literal('unverified'),
 reported_missing_actions:z.array(z.string().min(1)).min(1),
 catalog:z.array(z.object({id:z.string(),version:z.number().int(),description:z.string()}).passthrough()).min(1),
 automatic_generation:z.literal(false),automatic_registration:z.literal(false),workflow_execution_allowed:z.literal(false),
 lab_scope:z.literal('pure_json_transform'),
}).passthrough();
export const reviewSchema=referenceSchema.extend({protocol:z.literal('a4b.tool-gap-review.v1'),
 classification:z.enum(['actual_gap','existing_tools','unclear','external_integration','input_problem']),
 action_index:z.number().int().nonnegative(),reviewer:z.string().min(1).max(100),
 review_basis:z.enum(['human','automated_development']),reason:z.string().min(1).max(2000),
 compared_tool_ids:z.array(z.string()).min(1),existing_tool_ids:z.array(z.string()),
 effects:z.array(z.enum(['json_transform','network','filesystem','database','external_delivery'])).min(1),
 // Bind the reviewed specification, including every condition, to the submitted task.
 reviewed_spec:z.unknown(),
}).strict();
export type Gap=z.infer<typeof gapSchema>;
export type GapReview=z.infer<typeof reviewSchema>;
export type LoadCurrentGap=()=>Promise<unknown>;
const sameReference=(a:Record<string,unknown>,b:Record<string,unknown>)=>['session_id','revision','gap_id','state_fingerprint','catalog_sha256'].every(k=>a[k]===b[k]);
function validCreationIntent(gap:Gap){
 const intent=gap.creation_intent;
 return !!intent&&intent.choice==='create_tool'&&intent.gap_id===gap.gap_id&&
  intent.state_fingerprint===gap.state_fingerprint&&intent.revision===gap.revision;
}

export async function runGapExperiment(reviewValue:unknown,datasetValue:unknown,generator:Generator,
 options:{loadCurrentGap:LoadCurrentGap;outputDirectory:string;maxRepairs?:number;promptVersion?:PromptVersion;onProgress?:(message:string)=>void;beforeGenerate?:()=>Promise<void>}){
 const started=performance.now();
 const review=reviewSchema.parse(reviewValue);
 // The HTTP/CLI caller must load from worker storage, never accept a client-authored capability packet.
 const gap=gapSchema.parse(await options.loadCurrentGap());
 if(!sameReference(gap,review))throw new Error('대화·자료·도구 목록이 변경됐습니다. 부족 기능 명세를 다시 검토하세요.');
 if(!validCreationIntent(gap))throw new Error('사용자가 현재 요청의 새 도구 제작을 선택한 후 진행하세요.');
 if(review.action_index>=gap.reported_missing_actions.length)throw new Error('검토한 부족 기능의 번호를 확인하세요.');
 const actualIds=gap.catalog.map(t=>t.id).sort();
 if(!isDeepStrictEqual([...new Set(review.compared_tool_ids)].sort(),actualIds))throw new Error('현재 등록 도구 전체와 대조한 검토가 필요합니다.');
 if(review.existing_tool_ids.some(id=>!actualIds.includes(id)))throw new Error('검토에 등록되지 않은 도구 ID가 있습니다.');
 if(review.classification!=='actual_gap'||review.existing_tool_ids.length)throw new Error('기존 도구·입력 문제·불명확한 요청은 코드 생성 대신 재설계·추가 확인이 필요합니다.');
 if(review.effects.length!==1||review.effects[0]!=='json_transform')throw new Error('외부 전송·파일·DB·네트워크는 임시 변환 도구의 범위가 아닙니다.');
 const dataset=parseDataset(datasetValue);
 if(dataset.tasks.length!==1)throw new Error('부족 기능 검토 하나에는 업무 명세 하나만 연결하세요.');
 if(!isDeepStrictEqual(review.reviewed_spec,dataset.tasks[0].spec))throw new Error('검토한 명세와 생성할 업무가 다릅니다.');
 if(dataset.human_reviewed&&review.review_basis!=='human')throw new Error('자동 개발 검토를 사람 검수 자료로 표시할 수 없습니다.');
 await options.beforeGenerate?.();
 const preflightFinished=performance.now();
 const summary=await runExperiment(dataset,generator,options);
 const labFinished=performance.now();
 let current=false;
 try{const latest=gapSchema.parse(await options.loadCurrentGap());current=sameReference(gap,latest)&&validCreationIntent(latest)&&latest.creation_intent!.decision_id===gap.creation_intent!.decision_id;}catch{/* Keep stale results as research records only. */}
 const revalidationFinished=performance.now();
 const result={protocol:'a4b.tool-gap-evaluation.v1',gap_id:gap.gap_id,session_id:gap.session_id,revision:gap.revision,
  state_fingerprint:gap.state_fingerprint,catalog_sha256:gap.catalog_sha256,
  review_basis:review.review_basis,task_id:dataset.tasks[0].spec.id,decision_id:gap.creation_intent!.decision_id,
  status:!current?'stale':summary.passed_within_budget_tasks===1?'passed_lab_tests':'lab_not_passed',
  current_state_revalidated:current,lab_run_id:summary.run_id,lab_directory:summary.directory,
  model_calls:summary.model_calls,registration_status:'unregistered',workflow_execution_allowed:false,
  automatic_promotion:false,human_quality_approval:false,
  timings:{preflight_ms:preflightFinished-started,lab_ms:labFinished-preflightFinished,
   model_generation_ms:summary.generation_ms,isolated_evaluation_ms:summary.execution_ms,
   final_state_revalidation_ms:revalidationFinished-labFinished,
   scope:'단계별 서버 처리 시간. 최종 파일 기록·CLI 시작·반환 시간은 외부 계측의 왕복 시간으로 별도 확인. 첫 토큰 시간은 비스트리밍 방식이므로 미측정.'}};
 // Full spec/oracle stays in local experiment files; only public examples crossed the model boundary.
 for(const [name,value] of Object.entries({'gap.json':gap,'gap-review.json':review,'gap-evaluation.json':result}))
  await writeFile(join(summary.directory,name),JSON.stringify(value,null,2),{flag:'wx',mode:0o600});
 const source=await readFile(new URL('./gap-bridge.ts',import.meta.url),'utf8');
 await writeFile(join(summary.directory,'source-gap-bridge.json'),JSON.stringify({sha256:createHash('sha256').update(source).digest('hex'),source}),{flag:'wx',mode:0o600});
 return {...result,experiment:summary};
}
