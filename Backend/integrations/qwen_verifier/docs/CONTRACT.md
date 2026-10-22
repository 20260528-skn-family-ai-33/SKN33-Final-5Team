# 입출력 계약 `a4b.verifier.v1`

기계 판독 기준은 `schemas/input.schema.json`, `output.schema.json`, `result.schema.json`입니다. 알 수 없는 필드는 거부합니다. 외부 계약 변경은 새 버전으로 합의하세요.

## 입력: VerificationInput

| 필드 | 내용 |
| --- | --- |
| contract_version | 고정값 `a4b.verifier.v1` |
| request_id | 호출 측의 유일한 실행 ID. DB 결과 저장 시 중복 덮어쓰기 금지 |
| user_request | 최초 사용자 요청 원문 |
| messages | 순서가 있는 `{id, role: user/assistant, content}` 대화 |
| latest_user_answer_id | 최신 사용자 답변 ID, 없으면 null. user 메시지만 참조 |
| documents | 문서 ID·버전·파싱 상태·원문 chunk·표 스키마/샘플 |
| tools | 전체 등록 도구와 실제 활성 상태·입출력 계약 |
| previous_draft | 이전 초안 또는 null |
| candidate_draft | 메인 모델의 현재 초안 또는 null |

문서는 `{id, version, parse_status, chunks, tables}`입니다. chunk는 `{id,text,locator}`, table은 `{id,columns,sample_rows}`입니다. `failed`는 빈 chunks/tables, 일부 결과만 신뢰 가능하면 `partial`을 사용하세요. 샘플 행은 정확성 평가 전체 행을 대신하지 않습니다. 표 노드는 현재 문서 한 개의 표 한 개를 읽는 예제로 제한됩니다. 여러 표는 연동 측에서 분리하거나 계약을 확장해야 합니다.

초안은 `{id,version,document_ids,workflow}`이고 workflow는 `{entry,nodes,edges}`입니다. 노드는 `{id,tool,bindings,settings}`, 연결은 `{source,target,branch}`입니다. 실제 Backend AgentDraft 변환은 `draft_from_backend()`를 참고하세요. 원본 전체 초안은 호출 측에서 보관합니다. 다른 메인 모델이나 `workflow_preprocessing` 실험 계약과 자동 호환한다고 가정하지 않습니다.

## 모델 출력: VerificationOutput

| 필드 | 값·의미 |
| --- | --- |
| request_status | `ready / clarify / unsupported / null` |
| evidence_status | `sufficient / insufficient` |
| missing_conditions | `{code,detail,evidence}`. 이미 답한 조건은 제외 |
| unsupported_actions | 필수 업무 중 실제로 지원하지 않는 부분 |
| draft_verdict | `pass/fail/undetermined/not_applicable` |
| reflection_verdict | 같은 네 값. 최신 답변의 실제 초안 반영 여부 |
| evidence | 공통 판정 근거 `{evidence_id,quote}` 목록 |
| issues | `{code,detail,path,expected,actual,evidence}` 불일치·보류 진단 |
| summary | 짧은 진단. 생각 과정·새 워크플로우·질문 문장 아님 |

evidence_id는 `user_request`, `message/{메시지ID}`, `document/{문서ID}/{버전}/chunk/{chunkID}` 또는 `.../table/{tableID}`입니다. 각 ID 성분은 URL percent encoding을 적용합니다. 정확한 은행은 `evidence_bank()` 또는 실제 호출 payload의 `evidence_bank`에서 확인합니다. assistant 발화는 단독 사용자 근거로 등록하지 않습니다.

불일치 `path`는 **candidate_draft를 루트로 한 RFC6901 JSON Pointer**입니다. 예: `/workflow/nodes/1/settings/execution_plan/filters/0/operator`. expected/actual은 문자열로 쓰며 actual은 해당 위치 값과 일치해야 합니다. 숫자 표기 `2000000`과 `2000000.0`은 정규화하지만 gt와 gte는 다릅니다.

null과 not_applicable은 구분합니다. request_status의 null은 증거 부족으로 업무 상태 판정 보류입니다. 후속 답변이 없으면 reflection은 not_applicable, 답변이 있으나 초안 쌍이 없으면 undetermined입니다. 초안이 없으면 draft는 not_applicable입니다.

## 최종 반환: VerificationResult

`model_output`, `findings`, `route`, `execution_allowed`, `mode`, `model`, `latency_ms`, `error_code`를 반환합니다.

| route | 호출 측 처리 |
| --- | --- |
| ask_user | 사용자 조건 누락. 메인 모델이 질문을 작성 |
| repair_plan | 조건은 충분하지만 초안·설정·연결이 잘못됨. 메인 모델이 수정 |
| unsupported | 필수 작업의 도구가 없음. 지원 범위를 안내 |
| manual_review | 근거/계약 부족·모순·동적 노드·API 오류. 입력을 보완하거나 검토 |
| continue_review | 이번 검사에서 다음 단계로 이동 가능. 최종 실행 승인 아님 |

우선순위는 **검증 불능/모순 → 지원 불가 → 사용자 조건 확인 → 초안 수정 → 다음 검토**입니다. 단순히 실패 판정을 사용자에게 다시 질문하는 것으로 처리하지 않습니다. `execution_allowed=false`는 어떤 결과에서도 바뀌지 않습니다.

## 코드 검증 범위

JSON 계약/중복 ID, 문서 참조, 등록·활성 도구, 필수 설정의 존재, DAG 순환·도달성, 포트 자료형·의존성, 대표 표 필터의 실제 열·연산자·기준값, 원문 인용·응답 모순을 검사합니다. 모든 도구 설정·분기 경로의 의미·업무 의도·최종 결과 계산을 완전 검증하지는 않습니다. 같은 인용을 잘못 해석하는 모델 오류도 남을 수 있습니다.
