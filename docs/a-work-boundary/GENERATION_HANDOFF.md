# 생성·검증 담당 전달 — 2026-10-31 (A 작성)

A 범위(요청 검증·되묻기·조건/지원 판정·질문 상태)를 넘는 생성·검증 공유 파일 관련 내용이다. **아래 1절 변경은 미커밋 상태로 작업본에 있으며, 생성 담당의 검토·수정·되돌리기 판단에 맡긴다.** 2절은 A가 구현하지 않은 제안이다. 근거 자료는 `research/a4b_scopefix_20261029/`(보고서.md, 원시 요청·응답, 반복 평가, 실행 노트북)에 있다.

## 1. 이미 반영된 공유 파일 변경 (검토 요청)
| 파일 | 변경 | 원인(실제 관측) | 검증 |
|---|---|---|---|
| `linear_plan.py` | 단계 생성 규격 `steps` 개수 하한=기존 컴파일·감사가 요구하는 도구 수(read_table+filter_sort+확정 형식 시 write_result), 상한=4. 도구 선택·순서는 모델 몫, 코드 삽입 없음 | 단계 방식 `filter_sort` 누락 3/9: `minItems:1`이라 read_table 1단계 후 종료 허용 | 단계 방식 6/9→9/9 |
| `planner_constraints.py` | 노드 ID `pattern`(검증기 규칙의 ASCII형), 범위 제한 시 노드≤도구 수·연결≤도구 수−1, 연결 후보를 범위 안·비종료 도구 출력에서 계산(`TERMINAL_TOOLS=('finish',)`), 포트 미등록 도구·필수 입력 공급 불가 범위는 `ValueError` | ID `"1"`~`"4"`, `finish` 12개 반복, `finish ← write_result.text` | 그래프 7/9→8/9 |
| `workflow.py` `normalize_connections` | 연결 끝점 `노드:포트`는 실제 노드의 실제 포트일 때만 노드 ID로 정리. 없는 노드·없는 포트는 그대로 두어 검증기가 차단 | Deep Agent 초안 edges `read_table_1:table` | 실제 실패 사례 테스트 |
| `nodes/conversation.py` | 첫 단계에서 `list_tools` 호출 없이 끝나면 중단 대신, 입력 문맥에 이미 있는 도구 목록으로 초안 단계 1회 진행(`tool_selection_fallback` 기록). 정상 경로 변경 없음 | Ollama `/v1`이 강제 `tool_choice`를 지키지 않아 qwen3.5:4b 기본 경로 4/4 중단 | 이후 16회 중 0회 중단 |

테스트: `test_linear_plan.py`, `test_planner_constraints.py`, `test_workflow.py`, `test_deep_agent_conversation.py`에 추가·수정. `agent_builder` 전체 771개 통과(ML 의존성 설치 환경, 판정 모드 환경변수 없이).

## 2. 제안 (A 미구현): READY 단일 표 업무의 생성 경로
반복 평가(qwen3.5:4b 16k, 판정 enforce, 같은 명확 요청 5회)에서 Deep Agent 초안은 **2/5**만 올바른 ready였다. 실패 3건 중 2건은 **노드를 정의하지 않고 연결만 적은 노드 누락**(`filter_sort`/`write_result` 없음), 1건은 응답 길이 제한 중단이다. 조건 감사·검증이 저장 전에 모두 차단해 틀린 결과는 나가지 않았지만, 사용자는 재시도해야 한다.

같은 범위(판정 READY + 단일 표 선별·중복 제거)에서 기존 선택 경로 `direct v8`(모델이 단계 순서·설정 선택, 코드가 연결)은 9/9였다.

**제안:** 판정이 READY이고 `linear_plan.eligible(state)`가 참일 때만 단계 생성 경로를 쓰고, 그 외에는 Deep Agent를 유지한다.
- 장점: 노드 누락·연결 형식 오류가 구조적으로 생기지 않는다. 확정 조건·판정 지문·저장 전 검사는 그대로 적용된다.
- 고려: 생성 경로가 상태에 따라 갈리는 설계 변경(`graph.py`/`nodes/conversation.py`). 현재 direct transport는 Ollama 전용. 다중 표·문서·예약 업무는 범위 밖.
- 대안: 현 상태 유지(판정 차단 + 재시도). 재시도 복구율은 아직 측정하지 않았다.

## 3. 남은 생성 쪽 관측
- direct 그래프 방식: 도구 중복·누락(`filter_sort` 3회 반복, `write_result` 누락) 9회 중 1회. 문법으로 막으려면 도구 순서를 고정해야 해 반영하지 않았다. 검증기가 차단.
- 판정 off(현재 코드 기본)에서는 `match: any`로 묶인 틀린 초안이 ready로 통과한 사례 1/5. 조건 감사가 꺼져 있기 때문이며, A는 판정 enforce로 진행한다(`DEFAULT_MODE_PROPOSAL.md`).
- Ollama `/v1` 경로는 `num_ctx`를 지정할 수 없어 기본 4096에서 첫 요청의 자료·도구 문맥 메시지가 오류 없이 빠진다. 16k 모델 사용 필요(`Backend/scripts/setup-ollama-model.sh`).
