# 재설계 확인·제작 중단 복구 후속 규격

## 재설계 대안 제안 (생성 담당의 서버 내부 호출)

worker `action=tool_gap_replan_propose`, id, revision, alternative를 전달한다. 사용자에게 재설계 선택이 있어야 한다.

alternative:
- summary: 대안 설명
- proposed_request: 사용자가 동의할 완전한 새 요청
- tool_ids: 현재 실제 등록된 도구만
- changes: [{original_quote, proposed_change}] 최소 1건. 원래 사용자 발언의 정확한 인용 필요
- limitations: 수행하지 못하는 부분과 제약 최소 1건

자동 의미 검증으로 모든 변경 누락을 증명하지는 않는다. 생성 담당은 원래 요청과 대안을 대조해 변경·누락을 전부 명시해야 한다. 현재는 다른 업무로 바꾸어 확정하지 않도록 명시적 사용자 동의를 요구한다.

응답 `redesign_proposal`의 status는 awaiting_confirmation / accepted / rejected / deferred.
프론트는 대안·변경점·제약을 보여주고 사용자 결정 후 다음을 보낸다.

POST /api/agent-builder/tool_gap_replan_decision
{id, revision, requestId, proposalId, decision: "accept" | "reject" | "defer"}

- 실제 타입/검증: Frontend/server/tool-gap-contract.ts
- 선택 성공 시 새 revision을 보관. 같은 최신 요청 재전송만 멱등 처리.
- 보류 후 현재 대안에 다시 확인 가능. 새 대안/파일/요청/선택이 되면 이전 확인 무효.
- 원래 messages/draft/조건 장부는 자동 수정하지 않고 별도로 보존.
- accepted일 때만 redesign_generation 반환. effective_request·approved_changes·original_messages·original_conditions를 생성 담당에 전달.
- requires_condition_recheck / requires_draft_validation / requires_user_approval 모두 true, allows_save/execution 모두 false.
- 실제 generator가 이 새 요청으로 Workflow JSON을 생성하고 기존 저장·실행 검증을 통과시키는 연결은 생성 담당 작업이다. 반환 객체가 실제 새 워크플로우 완료를 뜻하지 않는다.

## 도구 지원 판단 (선택 경로)

Backend/agent_builder/capability_judgement.py의 assess_capability(state, transport)는 전체 실제 등록 목록과 대화를 검토한다. 파일 미첨부와 도구 부재를 구분하고 도구 부족 판단은 최대 한 번 더 재검토한다. 다른 모델을 따로 학습한 것은 아니다. 질문의 필요성·지원 판단은 보조적인 개발 기능이며 실행/생성 권한을 주지 않는다.

선택형 그래프 경로 A4B_CAPABILITY_MODE=enforce 또는 build_graph(..., capability_mode='enforce', capability_transport=...)로 사용한다. 기본값 off 유지. 현재 transport 기본 경로는 로컬 Ollama이며 다른 provider의 새 연결은 별도다.

- missing_capability면 tool_gap에 명세를 기록하고 사용자 선택으로 연결.
- existing_tools이고 현재 사용자 요청·자원·입력 상태·카탈로그의 fingerprint가 맞을 때만 조건 확인/생성으로 전달.
- clarify는 await_capability에서 실제 질문과 24자리 hex 질문ID를 전달. 답변 전 생성·저장 차단. 모르겠어/보류는 미해결 상태 유지, 명확한 답변 후 지원 재판정.
- manual_review는 validation_failed로 중단. 일반 저장 확인으로 풀리지 않으며 새 설명·수정 후 재판정 필요.
- 답변/수정 재개 및 생성 실패 후 변경 요청은 지원 판단을 다시 거친다. 직접 복원된 생성 노드와 저장/DB 저장에도 강제 검사.
- 한번 enforce로 적용된 세션은 재연결의 기본 off/shadow로 자동 약화되지 않는다. 새 세션의 기본 off는 유지. 기존 ready 체크포인트에 유효성 해시가 없으면 재판정 필요.
- 자료·DB·모델 오류는 저장/선택 진행을 차단하며 도구 부족으로 바꾸지 않음.
- 이미 지원되는 작업을 잘못 없는 것으로 판단할 수 있으므로 신규 도구/업무 추가 때 반드시 재평가.
- 연구의 새 사례는 자동 작성 개발 사례이며 사람 검수 완료나 일반화 정확도가 아님.

## 제작 작업 소유권·복구

claim 응답의 lease_token은 내부 소유자에게만 전달한다. public job과 session 결과에는 포함하지 않는다. finish에 같은 token 필요. 기본 제한 900초, 서버 환경 A4B_TOOL_JOB_LEASE_S로 30~1800초만 설정 가능. 사용자 요청 본문에서 제한을 변경하지 않는다.

- 만료된 작업은 tool_gap_job_recover(id)에서 interrupted로 보존. executeReviewedGap은 시작 전 만료 작업을 회수.
- 오래된 소유자/다른 token의 결과 등록 차단. 완료된 결과는 임의 변경 불가.
- 재시도는 새 사용자 제작 선택 필요. 복구가 모델을 자동으로 다시 부르지 않음.
- 제한 시간은 실제 프로세스/HTTP를 강제 종료하는 장치가 아니라 결과 등록을 통제하는 기한이다.
- 상시 복구 스케줄러/heartbeat 연장/HTTP 강제 취소는 아직 없음. 운영 서버가 시작·재연결 시 recover를 호출할지 합의 필요.
- 검토 명세/실험 시간이 제한을 초과하면 성공 등록하지 않고 보류·복구/재선택해야 함.

실제 프로세스 종료+시험 30초 만료+복구+늦은 결과 차단을 research/a4b_completion_20261027/recovery.json에 기록한다. 모델 호출 0회인 복구 시험이다.
