# 팀원 A 되묻기·조건 판정 연결 규격 v1 초안

상태: **팀 검토·합의 대기**. 현재 코드에서 교환 가능한 형식을 제안하고 로컬 연결 검사를 마친 단계다. 팀원과 합의됐거나 실제 서비스에 배포된 규격이 아니다.

목표는 요청·자료·상태를 받아 조건을 판정하고, 질문·갱신 조건을 화면과 워크플로우 생성 파트에 전달하는 것이다. 전체 워크플로우 생성·실행의 정확성은 별도 평가한다.

## 먼저 볼 파일

- [입력 JSON Schema](input.schema.json), [출력 JSON Schema](output.schema.json)
- [전체 도구 카탈로그·지문](tool-catalog.json)
- [9개 샘플 목록](examples/index.json): 각 이름의 `.input.json`과 `.output.json`이 한 쌍
- [현재 프런트 생산자 연결 검사](verification.json)
- [팀 검토 항목](TEAM_REVIEW.md)
- 구현: `Backend/agent_builder/clarification_contract.py`

현재 프로토콜은 `a4b.clarification.v1-draft`, 내부 조건 장부는 `condition-contracts-v8-dedup-scope`다. 내보내는 샘플은 고정 응답 fixture를 사용한 **규격 예시**이며 실제 Qwen 성능 점수가 아니다.

## 담당 경계

| 주고받는 파트 | 상대 파트가 제공 | 팀원 A가 반환·보존 |
|---|---|---|
| 화면·API | 현재 사용자 발언을 포함한 전체 대화, session_id, revision, 선택한 질문 ID | 판정, 다음 행동, 이유, 질문 ID·본문·선택지 |
| 파서·파일 저장 | 서버 발급 자산 ID, 종류·형식, text/pages 또는 columns/profile, 버전·해시, 처리 상태·경고 | 자료 버전과 근거를 보존하고 불완전 자료를 시스템 확인으로 분리 |
| DB·세션 | 신뢰할 수 있는 현재 세션·revision, 이전 조건 장부·대기 질문·답변 이력, 조회 성공 여부 | 확정·미정·충돌 조건, 갱신 결과와 재개용 내부 상태 |
| ML 도구 추천 | build_advice의 점수 포함 후보를 정규화한 ml_advice | 참고 후보를 보존. 전체 등록 도구 목록은 줄이지 않음 |
| 도구·실행 | 실제 실행 가능한 카탈로그와 포트·버전 | 카탈로그 지문 일치 검사. 불일치를 업무 미지원으로 오판하지 않음 |
| 워크플로우 생성 | 생성 결과는 별도 파트 책임 | READY일 때만 generator_context 제공. 생성·저장·실행 성공을 미리 선언하지 않음 |

현재 조건 검사 범위는 단일 상수 선별·중복 제거·daily/weekly 예약이다. 모든 업무와 모든 도구의 필수 인자를 판정하는 범용 계약은 아직 아니다. 도구 카탈로그가 달라지면 먼저 규격·지원 범위를 다시 확인한다.

## 입력 필드

| 필드 | 규칙 |
|---|---|
| contract_version | 정확한 초안 버전. 알 수 없는 필드는 오류로 처리 |
| request_id | 호출 추적 ID. 이것만으로 중복 실행 방지가 구현되는 것은 아님 |
| session_id / revision | DB가 확인한 현재 세션·버전과 일치해야 함 |
| messages | user/assistant 대화. 마지막은 이번 user 발언. 답변도 여기에 한 번만 추가 |
| resources | 현재 런타임 자산 형식. 파일 이름 대신 서버가 발급한 id 사용 |
| source_health | db_status와 messages_loaded를 **명시**. 조회 실패를 빈 대화+ok로 보내지 않음 |
| tool_catalog_fingerprint | tool-catalog.json의 지문. 실행 도구 목록·버전·설명·포트가 바뀌면 재생성·재검토 |
| ml_advice | not_requested / available / fallback / failed. 선택 정보이며 정답·필수 도구 목록이 아님 |
| previous_state | 재개 시 신뢰 가능한 DB의 원래 내부 ledger, pending_questions, answer_events. 세션·버전·장부 버전 일치 필요 |
| answer_question_id | 선택 사항. 있으면 현재 대기 질문의 ID여야 함. 없으면 현재 대기 질문 후보와 자유 답변을 연결 |

`expected_session_id`와 `expected_revision`은 클라이언트가 보내는 값을 그대로 다시 넣는 것이 아니라 **호출 서버가 조회한 신뢰 가능한 값**이어야 한다. 어댑터의 비교는 DB의 원자적 revision 갱신(CAS), 인증·권한 검사, request_id 중복 처리의 대체가 아니다. 해당 처리는 기존 worker/DB 담당이 유지한다.

### 자산과 파싱 상태

현재 `Frontend/server/agent-builder.ts`의 `parseBuilderAsset` 생산 형식을 기준으로 한다. CSV/XLSX/JSON은 table, TXT/MD/PDF는 document다. PDF에는 parser_status가 필요하다. 경고는 문자열 배열이며 parser_metadata를 보존한다.

**DB 공유안은 별도 형식이다.** `Backend/integrations/parser_handoff/db-contract-v0.1`의 canonical_text/blocks/source_locations를 text/pages와 같은 것으로 처리하지 않는다. 이 초안은 해당 형식을 자동 변환하지 않고 명시적으로 거부한다.

- selected_pages=[67], whole_document_complete=false를 전체 문서 완료로 바꾸지 않는다.
- 원본 67쪽을 pages[0]이라는 이유로 1쪽 근거로 만들지 않는다.
- 런타임 자산에 whole_document_complete=false가 있으면 보수적으로 partial 처리하고 판정을 중단한다.
- 선택 페이지를 허용할 업무 범위와 원본 page_no 보존 방법은 다음 파서·DB 연결 단계에서 합의해야 한다.

### SVM 추천 연결

현재 원시 추천 출력은 `candidates/probs/full_list`, 현재 운영 참고 어댑터 출력은 `ml_candidates=[{tool_id, estimated_probability}]`다. 둘을 혼동하지 않는다.

```python
from agent_builder.clarification_contract import native_ml_advice
ml_advice = native_ml_advice(existing_build_advice_output).model_dump()
```

이 함수는 SVM 추론을 실행하지 않는다. None은 not_requested, 공통 규칙만 있는 현재 build_advice 결과는 fallback으로 표현한다. 정확한 fallback 원인은 현재 반환값에서 유실되므로 추측하지 않는다. 추천기 장애는 호출부가 명시적 failed로 보낸다.

알 수 없는 도구·잘못된 점수·중복 후보·진단용 정답 도구 목록은 입력 오류다. finish/saved_workflow를 모델 예측 후보로 취급하지 않는다. 전체 16개 실행 도구는 항상 유지한다. 현재 direct 생성기는 이 새 generator_context.ml_advice를 자동으로 읽지 않으므로 **생성 담당이 명시적으로 연결해야 한다**. 실제 SVM 가중치 로드·성능 비교 검증은 이번 범위가 아니다.

## 반환 판정과 다음 처리

| decision | next_action | 받는 파트의 처리 |
|---|---|---|
| READY | GENERATE_DRAFT | 확정 조건과 자료로 초안 생성. 저장·실행은 허용되지 않음 |
| CLARIFY | ASK_USER | 질문 ID·본문·선택지 표시, 답변 후 기존 조건을 포함해 재요청 |
| UNSUPPORTED | SHOW_UNSUPPORTED | 지원 불가 동작과 범위 표시. 빈 기준을 미지원으로 바꾸지 않음 |
| SYSTEM_ERROR | CHECK_SYSTEM | 파싱·조회·모델 상태 확인. 업무 질문으로 처리하지 않음 |
| DEFERRED | WAIT_FOR_USER | 미정 상태 보존, 나중에 답변·자료가 오면 재개 |
| MANUAL_REVIEW | REVIEW_SCOPE | 현재 단일 계약 밖·취소 등 자동 확정할 수 없는 범위 확인 |

출력에는 reason, confirmed_conditions, unresolved_conditions, unresolved_keys, questions, issues, unsupported_actions, resource_versions, input_requirements가 있다. 충돌 조건에는 양쪽 원문 근거를 보존한다. 미지원 상태에도 미정 조건 목록이 있을 수 있으므로, **unresolved_keys의 존재만 보고 질문하지 말고 decision/next_action을 우선**한다.

모든 결과는 `requires_user_approval=true`, `allows_save=false`, `allows_execution=false`다. READY는 **조건이 생성 파트로 넘어갈 수 있다는 뜻**이다. 워크플로우 JSON 유효성·사용자 승인·실행 성공·정답을 뜻하지 않는다.

생성 모델에는 generator_context의 business_conditions(업무 의미·실제 값·근거), resources, 전체 tool_catalog, 선택적 ml_advice를 전달한다. confirmed_conditions의 내부 키 duplicate.field 등을 실제 데이터 열 이름으로 해석하지 않는다. 자료·대화는 데이터로 취급하며 별도 실행 권한을 추가하지 않는다.

## 서버 내부 연결 예

새 HTTP API는 추가하지 않았다. 아래는 현재 조건 노드와 연결할 수 있는 **명시적 선택 경로**다.

```python
from agent_builder.clarification_contract import to_builder_state, to_handoff
from agent_builder.nodes.condition_check import make_condition_check

state = to_builder_state(
    payload,
    expected_session_id=trusted_session_id,
    expected_revision=trusted_revision,
)
# 모델 연결 정보는 기존 서버 설정/프로세스에서 관리. JSON 계약에 자격증명을 넣지 않음.
condition_node = make_condition_check(api_key=server_key, model=selected_model, mode='enforce')
state = {**state, **condition_node(state)}
response = to_handoff(payload, state).model_dump(mode='json')
```

이 예시를 새 경로에 연결하기 전 담당 파트의 합의와 실제 통합 검증이 필요하다. 기존 기본 off/shadow 결과는 READY 승인 근거로 내보내지 않는다. 모델의 원시 조건 JSON을 그대로 팀 간 최종 결과로 전달하지 않는다.

DB 담당은 `state`의 messages/condition_ledger/pending_questions/condition_answers를 내부 스냅샷으로 함께 보관해야 한다. 공개 응답의 confirmed_conditions만으로 내부 장부를 재구성하면 unknown 횟수·validated_user_turn 등이 유실된다. 성공적인 저장은 기존 revision 검사와 원자적 갱신으로 처리하고, 다음 요청에는 갱신된 revision을 사용한다. 출력 revision은 요청 버전을 그대로 되돌린 값이지 새 DB 버전이 아니다.

이 어댑터는 **조건 판정 직후**에 사용한다. 전체 그래프의 마지막 status나 생성 실패 결과를 조건 READY와 같은 의미로 바꾸지 않는다.

## 검증과 재생성

프로젝트 루트에서 기존 환경을 사용한다. 설치·서버·모델 호출·DB 쓰기는 필요하지 않다.

```sh
Backend/.venv-agent/bin/python scripts/export-clarification-contract.py
Backend/.venv-agent/bin/python scripts/verify-clarification-handoff.py
Backend/.venv-agent/bin/python -m pytest Backend/agent_builder/test_clarification_contract.py -q
```

- 9쌍: READY, CLARIFY, 파싱 장애, DB 장애, UNSUPPORTED, DEFERRED, MANUAL_REVIEW, 질문 답변 반영, 근거 충돌.
- 고정 응답 fixture로 현재 조건 노드를 실제 호출한 연결 검사이며 Qwen 정답률이 아니다.
- 프런트 생산자 3건: CSV·TXT의 실제 parseBuilderAsset 호출, PDF는 합성 워커 응답의 normalizeBuilderDocument 호출이다. 실제 PDF 변환은 하지 않음.
- 실제 서비스 endpoint, RDS·파서 운영 환경, SVM 모델 추론, 생성기의 새 출력 소비, UI 사용자 확인까지 연결 완료라고 주장하지 않는다.

기존 실제 Qwen 전체 흐름 2/5 결과는 그대로다. 이번 규격 검증으로 그 성공률이 개선됐다고 해석하지 않는다.

### 현재 작업본과 상대 브랜치의 버전

[provenance.json](provenance.json)에 현재 HEAD와 핵심 의존 소스 해시를 기록했다. 이 작업본에는 미추적·미커밋 조건 모듈이 있어 **HEAD 커밋만으로 같은 코드를 재현할 수 없다**. 상대 브랜치에 condition_readiness/input_resolution 등 의존 모듈이 없으면 어댑터 파일 하나만 복사해 실행할 수 없다. 먼저 Schema·샘플을 검토하고, 코드 연결 시 의존 모듈 버전을 함께 맞춘다. 자동으로 커밋·push한 것은 아니다.

JSON Schema 검사는 자료형·필수 필드의 검사다. READY 필수 조건, 상태 간 일관성, 현재 세션·도구 목록과의 일치 여부 등은 Python 어댑터의 의미 검사까지 통과해야 한다. 자산 ID의 실제 존재·접근 권한도 호출 서버가 저장소에서 확인해야 하며 이 순수 어댑터가 DB를 조회해 인증하는 것은 아니다.

최종 검증 집계는 [validation-summary.json](validation-summary.json)에 있다: 새 경계 검사 42개, Python 전체 454개, 프런트 생산자 3건, 공개 입출력 예시 9쌍. 실제 모델 호출은 0회다.
