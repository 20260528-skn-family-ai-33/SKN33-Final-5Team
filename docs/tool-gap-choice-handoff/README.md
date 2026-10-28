# 팀원 A: 도구 부족 시 사용자 선택과 코드 제작 연결

현재 상태: 선택·동의·보류·제작 작업/결과 보존과 명시적 Node 서버 실행 경로를 구현했다. UI 버튼 3개와 재설계 대안 생성은 받는 파트가 연결해야 한다. 팀 합의·실제 파서/DB 환경 통합은 대기다.

## 프론트가 받는 선택지

`GET /api/agent-builder/session?id=<session>` 응답의 `tool_gap_choice`가 null이 아니면 표시한다.

> 현재 도구로는 요청한 기능을 처리하기 어려울 수 있습니다. 새 도구 제작과 검증에 시간이 더 걸릴 수 있습니다. 새 도구 제작을 진행할까요, 아니면 기존 도구로 워크플로우를 다시 설계할까요?

| choice | 버튼 | 다음 처리 |
|---|---|---|
| create_tool | 새 도구 제작 | 제작 동의 보존 → 등록 도구/명세 검토 → 생성·격리 검증 |
| redesign_workflow | 워크플로우 재설계 | 원래 조건 보존 → 기존 도구의 대안과 변경점 제시 → 변경 동의 |
| defer | 나중에 결정 | 조건·선택 보존. 나중에 새 선택 가능 |

`capability_status=unverified`다. 실제 등록 도구와 조합으로 해결 가능한지 검토하기 전에는 “없는 도구가 확정”이라고 표시하지 않는다. 현재 코드가 실제 도구 부족 분류 정확도를 보장하지 않는다.

## 선택 요청

`POST /api/agent-builder/tool_gap_choice`, JSON:

```json
{
  "id": "현재 session UUID",
  "revision": 1,
  "requestId": "새 선택 요청 UUID",
  "gapId": "현재 tool_gap_choice.gap_id",
  "stateFingerprint": "현재 tool_gap_choice.state_fingerprint",
  "choice": "create_tool"
}
```

실행 가능한 실제 예시는 examples/*.request.json, *.response.json을 사용한다. 위 코드는 필드 설명이며 UUID/해시 자체는 예시 값이 아니다.

- 구현된 요청 검증/타입: `Frontend/server/tool-gap-contract.ts`.
- 선택 버튼의 새 클릭은 새 requestId. 네트워크 재전송은 동일 requestId와 동일 원래 body. 최신 선택의 동일 요청만 멱등 반환한다.
- 선택 성공 후 응답의 새 revision을 반드시 보관한다. 오래된 revision으로 다른 클릭을 보내면 거부한다.
- 파일 업로드·대화·도구 버전이 바뀌면 기존 제작 동의는 무효다. 현재 선택지를 다시 가져온다.
- 프론트가 creation_intent나 execution_allowed를 직접 만들거나 덮어쓸 수 없다. 별도 실행 권한은 false다.
- 선택 요청은 모델을 부르지 않는다. 구현한 POST는 선택 저장만 담당하며 긴 모델 작업을 동기 실행하지 않는다.

## 제작 경로: 서버 담당에게

`Frontend/server/tool-lab/gap-service.ts`의 `executeReviewedGap(review, dataset, generator, options)`를 내부 서버에서 호출한다. options.worker는 실제 builderWorker다. 브라우저에서 임의 gap/동의 데이터를 넘겨 함수를 호출하지 않는다.

```typescript
const generator = remoteGenerator({provider:'ollama', model:'qwen3.5:4b', conditionalStatus:true});
const result = await executeReviewedGap(review, dataset, generator, {
  worker: builderWorker, outputDirectory: serverOwnedOutputDirectory,
  maxRepairs: 1, promptVersion: 'v7'
});
```

review/dataset은 [기존 도구 검토 규격](../tool-gap.md)을 따른다. 선택했다는 사실만으로 부족한 업무 조건이나 독립 정답 자료가 생기지 않는다. 입출력·오류 처리·공개/숨긴 사례가 필요하다. human_reviewed 표기를 자동으로 true로 바꾸지 않는다.

1. 현재 선택을 실제 worker에서 조회.
2. 도구 전체 대조·명세/정답 형식·동의 일치 검증.
3. SQLite에서 decision_id당 작업 하나 선점. 동일 동의/설정의 중복 호출은 기존 작업 반환, 모델 재호출 없음. 다른 설정이면 새 검토와 선택 필요.
4. 기존 생성·문법 검사·QuickJS·정답 비교 수행. 기본 수정 상한 유지.
5. 동의/대화가 바뀌었으면 stale. 정상 결과도 미등록이며 실행 허가 없음.
6. 현재 session의 tool_gap_job에서 결과 재조회 가능.

내부 worker action tool_gap_job_claim/tool_gap_job_finish는 Node 서버 전용이다. HTTP 목록에 공개하지 않았으며 브라우저의 자체 성공 보고를 저장하지 않는다. 내부 서버는 신뢰 경계이며 worker finish가 코드의 정답을 독자적으로 증명하는 것은 아니다.

상태: running / passed_lab_tests / lab_not_passed / generation_error / stale.
`passed_lab_tests`는 제공된 개발 사례 통과이지 실제 업무 실행 완료가 아니다. 결과의 model_calls=null은 인프라 실패로 호출 수를 확정 못했다는 뜻이며 0회로 해석하지 않는다.

프로세스 강제 종료 시 running 상태가 남을 수 있다. 자동 재시작/백그라운드 큐/중단 강제 종료는 아직 없다. 현재 선택을 다시 확인한 뒤 새로운 제작 선택으로 재시도할 수 있다. 선택 철회는 이미 수행 중인 HTTP 모델 호출을 강제 취소하지 않으며 결과 사용을 막는다.

## 재설계 경로: 생성 담당에게

`tool_gap_choice.next_action=propose_existing_tool_alternative`이고 `redesign_context`를 받는다.

- original_user_messages, condition_ledger, draft, missing_actions를 원문대로 전달한다.
- 기존 도구 목록을 기준으로 가능한 대안·달라지는 요구·빠지는 요구를 사용자에게 먼저 보여준다.
- `requirement_changes_approved=false`이므로 재설계 선택만으로 필수 요구 삭제에 동의한 것으로 보지 않는다.
- 대안 생성 자체와 대안 동의 입력을 실제 generator에 연결하는 부분은 담당 파트와 합의 후 추가한다. 현재 응답이 새 워크플로우 생성을 이미 완료한 뜻은 아니다.

## 실제 검증과 범위

작업공간 research/a4b_gap_choice_20261027/integration.json:
실제 worker·SQLite·Node coordinator·QuickJS로 보류→재설계→제작→결과 보존→중복 요청→재시작 조회를 검사했다. 생성은 수동 fixture 1회, 모델 호출 0회. 미지원 판단/사용자 선택도 주입/스크립트다. Qwen 품질을 새로 측정한 것이 아니다.

기존 v7의 실제 Qwen 29/29 두 실행은 보존하지만 새 선택·작업 저장 경로의 실제 Qwen 전체 통합 점수와 구분한다. 해당 generator 코드는 바꾸지 않았다.

## 팀원에게 받을 것

- 프론트: 선택지 표시·전송·버전 갱신·결과 polling payload.
- 생성 담당: 재설계 대안/변경점·사용자 확인 입력 형식, 도구 명세 작성/검토 담당.
- 파서: 정상/부분실패 실제 반환 1건씩, 본문/쪽/버전 매핑.
- DB: 현재 세션/revision 조회·실패/재시작 응답, 저장 위치와 접근 경계.

현재 [되묻기 전달 규격](../clarification-handoff/README.md)은 v1-draft다. 그 UNSUPPORTED=SHOW_UNSUPPORTED 반환을 이 신규 선택 프로토콜과 연결할지는 팀 합의가 필요하다. 기존 예제를 합의 없이 바꾸지 않았다.
