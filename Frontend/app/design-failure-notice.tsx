export type DesignFailure={kind?:string;failure_kind?:string;stage?:string;retryable:boolean;reason?:string;action?:string;attempt?:number;attempt_notice?:string};

const stages:Record<string,string>={requirements:'요청을 작업 목록으로 정리하는 단계',capability_check:'작업 지원 여부를 확인하는 단계',condition_check:'업무 조건을 확인하는 단계'};

export function DesignFailureNotice({failure,hasDraft,disabled,onRetry}:{failure?:DesignFailure|null;hasDraft:boolean;disabled:boolean;onRetry:()=>void}){
 if(!failure)return null;
 const attempt=failure.attempt??1;
 const retry=failure.retryable&&attempt<2;
 const action=failure.action?.trim();
 const attemptNotice=failure.attempt_notice?.trim()||(attempt>1&&!action?`같은 조건의 ${attempt}번째 시도도 실패했습니다. 반복 전송으로 해결되지 않고 있어 오류 확인이 필요합니다.`:'');
 return <section className="as-requirement-retry" aria-label="처리 중단 안내" role="status">
  <p><strong>{stages[failure.stage||'']||'워크플로우를 구성하는 단계'}에서 중단됐습니다.</strong></p>
  <p><strong>중단 이유</strong><br/>{failure.reason?.trim()||'상세 원인이 기록되지 않아 무엇 때문에 중단됐는지 확인할 수 없습니다.'}</p>
  {attemptNotice&&<p>{attemptNotice}</p>}
  <p><strong>다음 확인</strong><br/>{action||'관리자에게 이 안내와 요청 시각을 전달해 오류 원인을 확인해주세요.'}</p>
  <p>{hasDraft?'이전 워크플로우 초안은 유지했습니다. 이번 변경은 아직 적용되지 않았습니다.':'입력한 업무 설명과 답변은 남아 있습니다. 아직 워크플로우를 저장하지 않았습니다.'}</p>
  {retry&&<button type="button" className="as-button" disabled={disabled} onClick={onRetry}>조건 그대로 다시 시도</button>}
 </section>;
}
