# A4B 공개 Qwen3.5 9B 실패 분석

2026-11-11 KST. 동일한 고정 52개를 로컬 공개 모델로 전후 측정했다. 전체 요청 성공은 **23/52 → 28/52**, 실제 모델 호출 사례만은 **21/50 → 26/50**이다. 개선 8개·악화 3개이며, 모든 문제를 해결한 상태는 아니다. 실제 모델·합성 회귀·미실행 범위와 전체 지표는 `qwen-live-evaluation-report.md`를 참조한다.

## 원인별 건수

한 실패 사례를 주원인 단계 한 곳에만 배정했다. 최초로 확인된 중요한 의미·검증 오류와 최종 차단 단계는 별도로 기록한다. X06은 반환 응답을 얻지 못한 timeout을 주 실패로 세고, 그 이전 의미 검토 오류는 부가 근거로 남겼다. 이는 trace 기반 단계 분류이며 프롬프트·모델·Python 변경을 각각 분리한 인과 실험 결과가 아니다.

| 주원인 단계 | baseline 실패 29개 | after 실패 24개 |
|---|---:|---:|
| 의미 추출·모델 검토 | 7 | 7 |
| 지원 판단 | 5 | 2 |
| 조건 검증·질문 | 9 | 2 |
| 요구사항 추출·계약·충돌 | 7 | 12 |
| 워크플로우 생성 | 1 | 0 |
| 모델 응답 timeout | 0 | 1 |

전후 오류가 같은 단계에 머무르지 않는다. 조건 문제가 줄어 요구사항 단계까지 진행한 뒤 새로 막히는 사례가 있다. 생성 단계 실패가 0이라는 숫자도 생성 품질이 완벽하다는 뜻이 아니다. 예를 들어 R09는 더 앞선 요구사항 단계에서 중단된다. 상세 ID·근거·사례 hash는 `qwen-local-public9b-failure-causes.json`에 있다.

별도 추천 ML 정확도는 측정하지 않았다. SVM 후보 경로는 활성 상태였지만 이 자료만으로 실패를 SVM 학습 품질 탓으로 확정할 수 없다. 실제 저장된 두 턴의 세션·이력 보존은 전후 10/10이고, 잘못된 의미·요구사항과 세션 유실을 구분했다. Python executor에서 확인된 합성 표 실행 실패는 0/4이며 HTTP/브라우저 실동작은 미실행이다.

## 이번에 개선된 사례

| 사례 | 실제 after 근거 |
|---|---|
| R07 | rename을 compute 수식으로 만들던 문제를 보완하여 amount→revenue 및 JSON workflow가 READY |
| R10 | 기존 scalar/evaluate에 대응하는 요구사항 계약·근거·coverage를 추가하여 명시 수식의 READY 성공. 실제 계산 실행은 미실행 |
| C05 | 빈 값 오류를 중복 오류 정책으로 잘못 수용하지 않고, 중복 기준 열·보존 정책을 질문 |
| C07 | 시·분을 한 시각 질문으로 묶어 요일도 함께 질문. 내부 두 슬롯은 유지 |
| C08 | 근거 없는 08:00을 거절하고 시각을 질문. 무관한 정책 질문 제거 |
| T02 | 원문에 없는 unread 없이 정확한 뉴욕 날짜 epoch query 생성 |
| T06 | holdout의 DST 종료일 25시간 범위 query가 올바른 READY로 개선 |
| M06 | holdout의 월15일 09:00 예약 답변을 반영해 READY. 추가 filter_sort는 조건·정렬이 없는 항등 전달 노드로 업무 의미를 바꾸지 않으나 지원 판단의 과잉 후보 문제는 남음 |

T06/M06은 holdout 판정 결과이며 해당 문장을 근거로 수정 규칙을 만든 것이 아니다. 개선 사례 중 실제 도구 실행이 예정되지 않은 사례를 실행 성공으로 세지 않았다.

## 악화된 사례와 보존된 실패

- **T01/T04:** 최초 의미 모델이 ‘어제’를 date_range로 바꾸고 원문을 other_requirements에 중복 기입했으며 검토 모델도 승인했다. 표준 날짜 compiler를 벗어난 뒤 요구사항에 요청하지 않은 human_review가 추가되어 SYSTEM_ERROR가 됐다. 기준 날짜 문자열이 일부 남아도 시간대에 맞는 epoch query 성공으로 세지 않았다. T02/T06의 개선과 T01/T04의 악화가 상쇄되어 시간·수량 전체 성공은 4/8로 같다.
- **C09 holdout:** baseline의 적절한 질문과 달리 after가 qty*unit_price와 total_amount를 추측했다. 두 요구사항 후보는 수식 형식 검증에서 거절됐지만 필요한 수식·결과 열 질문도 사용자에게 도달하지 않아 SYSTEM_ERROR다. 거절된 후보는 수용 슬롯 FP로 계산하지 않았으며, 형식 거절을 의미 추측 검증의 해결로 해석하지 않는다.

다음 사례는 전체 실패가 유지됐다.

| 사례 | 남은 원인과 부분 개선 |
|---|---|
| R03 / M03 | 정렬 또는 중복 제거를 추가 필터로 오인해 불필요한 필터 질문. holdout 전용 규칙 미추가 |
| R04 / M02 | 조건과 첫/마지막 보존 정책은 정확해졌으나 요구사항이 이미 정한 정책을 다시 질문 |
| R06 | 빈 값 오류 정책을 ERROR 문자열 채우기로 바꾸고 재질문 |
| R09 | Base64 연산을 요구사항 계약이 정확히 표현하지 못해 compute/scalar로 오분류·거절. scalar 추가만으로 등록 operation 전체를 해결하지 못함 |
| C01 / C03 | 매일 09:00·시간대 또는 지원 분류는 개선됐지만 수신자·출처·분석 기준 중 필수 질문이 빠짐 |
| C06 | ‘중요한 메일’을 근거 없이 important 필터로 확정하고 잘못된 표 workflow 생성. validator가 거절 |
| C10 / M10 | 발송 제목·본문의 올바른 후보를 의미 검토 모델이 모순된 이유로 거절. 지원 단계의 불필요 질문 또는 수신자 질문 누락이 남음 |
| M04 | 시각 답변과 정확한 예약 초안까지 만들었지만 capability가 무관한 filter_sort를 필수 도구로 요구하여 거절 |
| M05 | 월요일을 조건 ledger에 weekday0으로 보존했지만 requirement가 weekday1을 두 번 내어 검증 실패 |
| M09 | amount 답변을 별도의 compute 업무로 만들어 정확한 집계 초안을 거절 |
| M12 | 첫 턴부터 날짜 의미 추출 경로가 틀려 SYSTEM_ERROR. 다음날 5개 변경은 미실행 |
| X03 | 정확한 집계·필터 후보에 대해 JSON export 요구사항을 중복 생성하여 복수 파일 검증에서 거절 |
| X05 | condition·workflow는 first 정책으로 정확하지만 accepted requirement가 keep을 명시해 충돌. 실제 native 구현은 중복 제거 후 필터 순서라 후보 업무 coverage는 4/4이나 전체는 실패 |
| X06 | 계정 연결을 미지원으로 오인하던 판단은 개선. 의미 후보 검토 실패 뒤 requirement 응답이 181.2883초에 TimeoutError. 재실행하여 성공 결과로 교체하지 않음 |
| X07 | 기준값·연산자 질문 대신 amount 자기 비교(gt, compare_field=amount)를 만들고 거절됨. 첫 raw의 기준 질문도 실제 응답에는 노출되지 않음 |

X01은 정확한 Notion 미지원 처리로 전체 분류 성공이지만, accepted meaning이 근거 없는 sender:customer 선택자를 만든 결함은 슬롯 FP로 별도 감점했다. 미지원 종료로 실제 query·발송·저장 실행은 없었다.

## 수정 파일과 한계

이번 앱 변경은 다음 8개 파일에 제한했다. 이번 변경만의 patch 및 전후 byte hash는 `qwen-local-public9b-application-changes.patch`, `qwen-local-public9b-after-change-manifest.json`에 있다. 기존 사용자 변경은 유지했다.

| 파일 | 변경 목적 |
|---|---|
| Backend/agent_builder/capabilities/capability_check.py | 문장형 자료 ID 질문을 연결 UI 안내로 바꾸고 실제 업무 ID 열은 보존 |
| Backend/agent_builder/capabilities/capability_judgement.py | 발송 문자값과 실행 동작, 연결 전제와 기능 부재를 프롬프트에서 구분 |
| Backend/agent_builder/conditions/condition_readiness.py | 정책·시각 근거와 이전 checkpoint 재검증, 시·분 질문 묶기, 순수 중복 제거의 제한적 분류 교정 |
| Backend/agent_builder/design/request_meaning.py | 근거 없는 unread와 잘못된 이전 의미 cache 검증, 발송 literal의 추출·검토 프롬프트 보완 |
| Backend/agent_builder/design/requirement_contracts.py | 기존 scalar_calculate 도구의 명시적 요구사항 계약 추가 |
| Backend/agent_builder/design/requirement_ledger.py | 검증된 사용자 시간대 결합, rename·scalar 추출, 불필요 확인 질문 억제 안내, 계약 fingerprint 갱신 |
| Backend/agent_builder/design/requirement_coverage.py | scalar 수식·변수의 정확한 coverage 검증 |
| Backend/agent_builder/design/scalar_requirement.py | 원문에 쓰인 수식·숫자 변수만 좁게 검증하는 helper 추가 |

수정 전 합성 재현은 51 failed / 41 passed였다. 이후 지정 Python 회귀 2,069개, TypeScript 계약·상태 테스트 41개와 두 typecheck가 통과했다. 실제 모델의 잔존 실패는 이 합성 통과와 별개다. 중간 실패 파일도 보존했다.

## 평가 자체의 제한과 다음 우선순위

첫 로컬 실행은 계측 저장 실패가 앱 fallback으로 섞일 수 있어 원본을 보존하고 품질 비교에서 제외했다. 수정된 계측기로 v2 전체 baseline을 다시 측정했다. v2와 after의 추론 harness는 동일했다. SDK httpx2 경로의 dispatch/redirect hook 누락은 남아 반환 응답 근거와 HTTP 계수를 분리했다. 모든 후보 JSON의 schema 실패율이나 총 서버 추론·비용을 안다고 주장하지 않는다.

after 첫 shard는 X06 timeout 뒤 circuit breaker가 X07을 미실행으로 남겼다. 서버 로그의 취소·idle 상태를 확인한 뒤 X07만 같은 환경에서 실행해 별도 shard로 보존했다. 중복 사례 없이 병합하고 두 probe의 출처를 모두 남긴다. 과거 RunPod 및 첫 로컬 중단 결과와는 합치지 않는다.

다중 턴의 미관측 후속 답변을 성공으로 채우지 않았다. baseline M02는 유효한 질문 표현을 고정 답변 matcher가 못 맞춘 한계가 있으며, after M10/M12도 후속 미관측이다. M05 실패 응답의 stale draft 질문은 기존 집계 규칙상 포함되지만 실제 새 pending 질문·브라우저 노출과 구분했다.

우선순위는 (1) 요구사항이 명시 조건·기존 조건 ledger와 충돌하거나 중복 업무를 만들지 않도록 검증하고 미정 값을 질문으로 표현하기, (2) 날짜·시간과 검색 필터의 근거 및 원래 기준시각을 유지하기, (3) 지원 도구 후보와 반드시 실행해야 할 업무를 구분하기, (4) 의미 검토의 잘못된 거절·승인을 검증 가능한 규칙으로 보완하기다. 프롬프트 변경만의 효과를 분리하는 다음 비교가 필요하다.

**별도 sLLM이나 파인튜닝의 필요성은 아직 입증되지 않았다.** 현재 Qwen으로도 조건 검증과 계약 보완의 효과가 있었지만 holdout 성공은 3/15에 그쳤다. 먼저 위 잔존 원인을 보완하고 독립 사례로 재평가해야 하며, 52개 결과를 학습 문장처럼 맞추거나 모델을 추가하는 것으로 대체하지 않는다. 기존 커스텀 모델의 가중치가 없어 공개 모델보다 좋거나 나쁘다는 비교도 할 수 없다.
