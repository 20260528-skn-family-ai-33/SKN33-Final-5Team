# A4B 공개 Qwen3.5 9B 실제 평가 보고서

2026-11-11 KST. 공개 Qwen3.5 9B로 고정 52개 baseline과 동일 52개 after, 별도 3개 반복 및 회귀 검증을 완료했다. 전체 요청 성공은 **23/52 → 28/52**, 실제 모델 호출 사례만은 **21/50 → 26/50**이다. 개선8개·악화3개와 잔존24개 실패를 모두 보존했다. 기존 RunPod 결과와 합치지 않았다.

## 평가 범위와 모델

사용자가 기존 커스텀 가중치를 확보할 수 없어 공개 `qwen3.5:9b`를 선택했다. MAIN과 capability에 등록명 `a4b-public-qwen35-9b-32k`를 사용했다. 두 역할의 기존 호출 경로는 유지되지만 동일 공개 가중치이며, 과거 final664/previous 커스텀 모델의 품질을 재현한 시험이 아니다. 실제 응답 model metadata와 Ollama 모델 목록·digest를 확인했다.

| 항목 | 이번 로컬 비교 조건 |
|---|---|
| endpoint / provider | `http://127.0.0.1:11434/v1` / Ollama 0.40.15 |
| 공개 모델 / 양자화 | Qwen3.5 9B, Q4_K_M |
| 등록 모델 digest | `dfd32b09718e2aa05f622795b2ab575efc817c6129928209c532c55023f4f2df` |
| GPU / 메모리 | RTX 4060 Ti 8GB / 시스템 RAM 약 15.93GiB |
| 런타임 | Python 3.13.16, Node 24.20.0, Windows 11 |
| 주요 Python 패키지 | LangGraph 1.2.14, checkpoint-sqlite 3.1.1, Pydantic 2.14.1, DeepAgents 0.7.23, OpenAI 3.29.0 |
| native 생성 설정 | context 32768, num_predict 4096, temperature 0, top_p 0.8, seed 42, think false |
| 모델 등록 추가 설정 | top_k 20, presence_penalty 1.5; 모델 provenance에 원문 기록 |
| 앱 설정 | DeepAgent; capability/condition/requirement enforce; verifier off; Python validator 유지 |
| 자원 제한 | main timeout 180초, 모델 호출 동시성 1 |
| 도구 추천 | 기존 SVM56 hint 경로 활성; 독립 추천 정확도 시험은 아님 |

서버가 보고한 모델 최대 context는 262144이고 실제 적재 context는 32768이었다. 모델 적재 스냅샷의 전체 크기와 VRAM 크기가 달라 CPU/GPU 혼합 적재 상태다. API 호환 planner는 기존 model_factory 설정을 사용했으며 native 설정이 모든 SDK 요청 인자로 전달됐다고 가정하지 않는다. 각 호출의 `request_settings`와 `effective_process_settings`를 보존했다. `.env`와 운영 endpoint는 수정하지 않고 평가 자식 프로세스에만 프로필을 적용했다.

모델·하드웨어·의존성·프로필·정답·평가기 manifest는 local baseline/after에서 동일하게 확인한다. 앱 manifest만 수정 전후가 다르다. 이전 컴퓨터의 Python 3.12, 패키지 버전, SVM 누락 환경과는 다르므로 RunPod와 수치를 합산하지 않는다.

## 데이터와 판정

고정 52개: READY 10, CLARIFY 10, 날짜·시간·수량 8, 다중 턴 12, 미지원 5, 복합 7. 모호한 1개는 제외했다. 각 유형에서 매 3번째 사례를 사전 holdout으로 지정하여 development 37 / holdout 15로 나눴다. 결과를 보고 정답을 변경하지 않았고, 앱 수정은 development 실패를 근거로 했다.

- gold canonical SHA256: `e939c051a940915c4061b45221e7f1353b8ce2157677d379ae193de13b1300da`
- 수정 전 앱 source SHA256: `6550b20928227b74c6a2c33cded04e62d359a6550c034dce935cad5230e01be5`
- 수정 후 앱 source SHA256: `6a417569cd7f1fd24065c981c6e31cf2d2622d0ff3c54e41164e60e3e60be460`
- 두 정식 실행의 평가기 SHA256: `784bd2475a1fa7ccad3f85a6a17fe5d7e708f9b5a958ef25e09b317a9959aea6`

`completed`는 측정 종료를 뜻한다. 성공은 원본 trace·수용된 구조화 상태·질문·최종 workflow를 고정 gold와 비교한 별도 라벨이다. Codex의 근거 검토이며 독립 사람 평가나 Qwen 자기평가가 아니다. 검토 라벨은 사례 hash에 연결되어 있다.

전체 요청 성공은 기대 분류, 필요한 질문 또는 미지원 사유를 만족해야 한다. READY는 모든 업무·인자·데이터 연결이 맞고 불필요한 업무가 없어야 한다. 외부 서비스 실행 성공과 구분한다. 단순 description의 원문 반복, 거절된 모델 후보, 기본 draft 값은 수용된 슬롯 추출로 세지 않았다. 슬롯 F1은 고정 gold 키와 별도 검토한 무근거 값 범위에 한정한다.

## 정식 전후 결과

전후 모두 고정 52개를 측정했다. R01/T08은 기존 코드 recipe이며 실제 모델 지표는 각 50개로 계산한다. worker 응답 완료는 baseline 50개+예외2개, after 52개지만 이는 성공 수가 아니다. after에는 X06 모델 응답 timeout 실패가 포함된다.

| 평가 범위 | Baseline | After |
|---|---:|---:|
| 전체 요청 성공 | 23/52 (44.2%) | 28/52 (53.8%) |
| 실제 모델 호출 사례의 전체 요청 성공 | 21/50 (42.0%) | 26/50 (52.0%) |
| 최종 READY 정답 대상의 전체 요청 성공 | 14/35 (40.0%) | 17/35 (48.6%) |
| 그중 실제 모델 호출 대상 | 12/33 (36.4%) | 15/33 (45.5%) |
| development 전체 요청 성공 | 21/37 (56.8%) | 25/37 (67.6%) |
| holdout 전체 요청 성공 | 2/15 (13.3%) | 3/15 (20.0%) |

개선 8개: R07/R10/C05/C07/C08/T02/T06/M06. 악화 3개: C09/T01/T04. 20개는 성공 유지, 21개는 실패 유지다. holdout의 3/15는 일반화 성능이 충분하다는 근거가 아니다.

| 유형별 전체 요청 성공 | Baseline | After |
|---|---:|---:|
| READY | 4/10 (40.0%) | 6/10 (60.0%) |
| CLARIFY | 3/10 (30.0%) | 5/10 (50.0%) |
| 날짜·시간·수량 | 4/8 (50.0%) | 4/8 (50.0%) |
| 다중 턴 | 4/12 (33.3%) | 5/12 (41.7%) |
| 미지원 | 5/5 (100.0%) | 5/5 (100.0%) |
| 복합 | 3/7 (42.9%) | 3/7 (42.9%) |

다음은 **실제 모델 50개 기준** 지표다. F1의 괄호는 사례 수가 아니라 2TP/(2TP+FP+FN) 집계다. 초기 gold 슬롯은 전체120/live113개이고, 최종 READY 업무 목표는 전체88/live84개다. 모든 gold 업무104/live100개를 업무 지표 분모로 사용한 것이 아니다.

| 지표 | Baseline | After |
|---|---:|---:|
| 의도 정확도 | 44/50 (88.0%) | 47/50 (94.0%) |
| 슬롯 F1 | 0.7188 (138/192) | 0.7638 (152/199) |
| 초기 분류 Macro F1 | 0.7561 (n=50) | 0.8347 (n=50) |
| 필수 질문 재현율 | 21/32 (65.6%) | 23/32 (71.9%) |
| 불필요 질문율 ↓ | 21/42 (50.0%) | 13/35 (37.1%) |
| 후속 응답 재질문율 ↓ | 1/5 (20.0%) | 2/4 (50.0%) |
| 관측된 답변 반영 정확도 | 10/12 (83.3%) | 12/12 (100.0%) |
| 되묻기 완료율 | 3/10 (30.0%) | 4/10 (40.0%) |
| 최종 draft 서비스 검증 통과율 | 14/18 (77.8%) | 15/25 (60.0%) |
| 도구/operation 포함 정확도 | 42/84 (50.0%) | 62/84 (73.8%) |
| 업무 의미 coverage | 36/84 (42.9%) | 52/84 (61.9%) |
| 실제 합성 표 실행 성공 | 4/4 (100.0%) | 4/4 (100.0%) |
| 사례 지연 p50 | 42.04초 (n=50) | 64.72초 (n=50) |
| 사례 지연 p95 | 102.17초 (n=50) | 143.36초 (n=50) |

분류 F1은 첫 턴 기준이며 SYSTEM_ERROR는 기대 클래스의 FN이다. 마지막 턴에서 READY에 도달한 다중 턴 성공률과 다른 지표다.

| 클래스 | Baseline TP/FP/FN · F1 | After TP/FP/FN · F1 |
|---|---:|---:|
| READY | 12/0/11 · 0.6857 | 11/0/12 · 0.6471 |
| CLARIFY | 18/7/3 · 0.7826 | 18/3/3 · 0.8571 |
| UNSUPPORTED | 6/3/0 · 0.8000 | 6/0/0 · 1.0000 |

Macro F1은 좋아졌지만 READY F1과 최종 draft 검증률은 낮아지고 지연은 늘었다. 후속 생성 단계까지 진행한 사례와 요구사항 충돌·추가 재시도가 증가한 영향이 섞여 있으며, 동일 작업 경로만의 속도 비교 실험은 아니다.

실제 합성 실행은 전후 R02/R05/M01/X02의 **4/4**이며 실행 대상6개 중4개만 도달했다. R03/R04는 도달하지 못했다. 외부 이메일·Gmail·Notion 실행 성공률이 아니다. 최종 schema bool은 전체20→27개, live18→25개이며 null은32→25개다. null·파싱 예외 후보를 모두 포함한 JSON schema 통과율은 아니다.

다중 턴 12개 중 저장된 두 응답을 확인한 것은 전후10개씩이다. 세션·이력 보존10/10씩, 실제 답변 question ID 일치8/8→9/9다. 고정된 변경 슬롯 목표14개 중 답변 반영 지표는 관측가능12개씩만 사용한다. baseline은 M02/M10, after는 M10/M12의 후속을 제외하므로 같은12기회의 완전한 대응 비교가 아니며 미관측을 성공으로 채우지 않았다. baseline M02는 적절한 질문 표현을 고정 답변 matcher가 못 맞춘 한계가 있다.

M05 after의 실패 응답에 남은 이전 요일 draft 질문1개가 후속 재질문2/4에 포함된다. 새 pending 질문은 아니며 실제 브라우저 노출도 미검증이다. 기존 집계 규칙은 전후 동일하게 유지했다.

| 기록된 모델 응답·사용량 | Baseline | After |
|---|---:|---:|
| 실제 반환 확인 응답(사례+probe) | 181 (180+1) | 193 (191+2) |
| 반환 실패 기록 | 0 | X06 TimeoutError 1 |
| usage가 있는 native 사례 응답 | 161 | 158 |
| 그 부분의 입력 token 합 | 963,448 | 977,006 |
| 그 부분의 출력 token 합 | 20,513 | 20,259 |
| usage 없는 사례 기록 | SDK 19 | SDK 33 + timeout 1 |

usage 합계는 위 부분만의 관측값이다. SDK 미기록·실패 생성·probe를 포함한 전체 token·서버 추론·비용을 뜻하지 않는다. after는 X06의 180초 제한으로 circuit breaker가 멈춰 원본 shard를 보존했고, 미실행 X07만 새 shard로 실행해 병합했다. timeout의 서버 취소·idle 로그를 확인했으며 X06을 재실행해 유리한 결과로 바꾸지 않았다.

## 별도 반복 측정과 판단

사전에 고정한 development R04/C05/M01을 같은 앱·모델·설정으로 한 번 더 측정했다. 반환 확인 응답은 probe 포함12개다. 최종 결과는 R04 실패, C05 성공, M01 성공으로 main과 같았다. M01은 합성 실행 결과도 일치했다. C05는 내부 unresolved에 무관한 예약 슬롯7개가 추가되는 변동이 있었다. 현재 두 필수 질문은 맞지만 그 후속 영향은 미실행이다. 세 사례의 한 번 반복을 전체 출력 안정성으로 일반화하지 않고 main 분모에 포함하지 않았다.

별도 sLLM·파인튜닝은 추가하지 않았다. 이번에는 검증·계약 보완 효과와 날짜 해석 퇴행, 요구사항 충돌이 함께 확인됐다. 우선 명시 조건의 충돌/중복 검증, 날짜·검색 필터 근거, 도구 후보와 필수 업무 구분, 의미 검토의 잘못된 거절을 개선해야 한다. 프롬프트도 영향 요인이지만 변경을 함께 적용한 시험이라 특정 문장만의 인과 효과는 분리하지 못했다. 낮은 holdout 성능과 잔존24개 실패를 고려하면 추가 모델이 필요하다고 결론 내릴 근거도, 운영 품질이 충분하다고 선언할 근거도 부족하다.

## 수정과 회귀 검증

| 변경 영역 | development 근거와 적용 내용 |
|---|---|
| 조건 근거·질문 | C05/C08의 중복 정책·시각 추측을 원문/실제 답변과 대조하고 이전 checkpoint 값도 재검증. C07의 시·분은 한 질문으로 묶되 내부 unresolved 둘 다 유지 |
| 업무 분류 | R04에서 전체 요청의 중복 제거 의도가 좁게 입증될 때만 잘못된 필터 해석을 교정. 추가 선별 절이 있으면 교정하지 않음 |
| 시간대·요구사항 | C01의 명시 시각과 검증된 user_timezone 결합. 명시된 시간대 변경 우선, 잘못되거나 충돌한 시간대는 추측하지 않음 |
| 요구사항 계약 | R07 열 이름 변경을 source/target으로 명시. R10을 위한 기존 scalar/evaluate 표현·근거·coverage 추가. X05의 완료 요청 승인 질문을 누락 정보 질문과 구분 |
| Gmail 의미·지원 판단 | T02의 원문에 없는 unread 조건을 Python 근거 검사로 거절. C10/M10의 제목·본문 문자값을 별도 업무로 오인하지 않도록 추출·검토·지원 프롬프트 보완 |
| 사용자 질문 | C02 자료 ID 문장을 자료 연결 UI 안내로 변환. 실제 업무 ID 열은 보존. Gmail 인증정보 질문은 계정 연결 안내로 변환 |

앱 변경은 8개 파일이며 전체 시스템을 재작성하지 않았다. 이전 미커밋 변경을 보존했고, 이번 앱 변경만의 patch와 source manifest를 별도로 저장했다. 새 `scalar_requirement.py`는 사용자에 명시된 수식·숫자 변수만 좁게 허용하며 기존 native 실행기와 validator는 유지했다. 프롬프트 변경의 효과와 검증·상태 변경의 효과를 개별 분리한 ablation 실험은 하지 않았다.

실제 모델 재평가 전 합성 실패 재현은 51 failed / 41 passed였다. 수정 후 지정 Python 회귀 범위(`tests/design`, `tests/capabilities`, `tests/conditions`)는 **2,069 passed**, TypeScript 계약·상태 테스트는 **41 passed**, Frontend 및 Backend/api typecheck는 통과했다. 합성 테스트 통과를 실제 Qwen 성공으로 계산하지 않는다. 중간 실패 XML도 보존했다. 시각 질문의 목적을 유지하기 위해 기존 fixture의 모호한 `8시`를 `오전 8시`로 명확히 한 변경은 별도 보관했고, 모호한 시각 거절 테스트를 추가했다.

## 계측과 미실행 범위

실제 시험 경로는 service.worker → LangGraph/SQLite checkpoint → Qwen → Python validator → 허용된 native 표 도구의 합성 행 실행이다. 실제 HTTP 서버·브라우저 UI는 실행하지 않았다. API/Frontend 결과는 계약·상태 테스트 및 typecheck다. 실제 Gmail·Notion·고객 DB를 조회하지 않았고 이메일도 발송하지 않았다. 계정 연결 값은 합성 metadata다.

첫 로컬 baseline은 snapshot 저장 예외가 앱 fallback에 섞일 수 있는 계측 결함을 발견해 중단했다. 원본을 보존하고 예외 격리 및 Windows 저장 경합 재시도를 합성 검증한 뒤, 앱을 변경하지 않은 상태에서 v2 전체 52개를 다시 측정했다. 첫 실행의 유리한 사례를 v2에 섞지 않았다.

v2/after의 OpenAI SDK가 httpx2를 사용하여 평가기의 httpx HTTP 계수·경로/redirect hook을 우회하는 한계가 있다. SDK 조립 응답과 실제 model metadata는 기록됐지만 원본 HTTP stream 프레임은 아니다. 별도 accounting 후처리로 선택 모델의 반환 응답 근거를 집계한다. HTTP dispatch 수는 하한이며 응답 확인 수를 서버 전체 추론·retry 수·비용으로 단정하지 않는다. host/port 제한과 합성 실행 allowlist는 별도로 유지했다. SDK redirect가 없었다는 독립 증명까지 갖춘 시험은 아니다.

`workflow_schema_validity`는 최종 draft에서 bool로 저장된 서비스 검증 결과만 사용한다. planner 파싱/변환 예외로 `null`인 후보도 있어 모든 생성 JSON 후보의 순수 schema 성공률은 아니다. 최종 집계에서 bool 분모·null 수·worker 실패를 함께 명시한다. 중단·미저장 호출과 latency는 추정하지 않는다.

질문 지표의 기존 수집 규칙은 `pending_questions`가 비면 `draft.questions`를 사용한다. 실패 응답에 남은 이전 draft 질문이 포함될 수 있으므로 실제 새 pending 질문·브라우저 노출과 구분한다. 이 지표는 백엔드 응답 질문 필드 기준이며, 관련 사례와 분모를 최종 집계에서 따로 밝힌다.

## 산출물 관계

| 파일 | 용도 |
|---|---|
| `qwen-live-baseline.json` | 과거 커스텀 RunPod 중단 결과, 그대로 보존 |
| `qwen-local-public9b-baseline.json` | 첫 로컬 중단 실행, 정식 품질 비교 제외 |
| `qwen-local-public9b-baseline-v2.json` | 정식 공개 모델 전체 baseline |
| `qwen-local-public9b-after.json` | X06 timeout 뒤 중단된 첫 after shard, 51개 측정·X07 미실행 보존 |
| `qwen-local-public9b-after-part2.json` | 같은 조건의 X07 단독 측정 |
| `qwen-local-public9b-after-complete.json` | 중복 없이 병합한 정식 전체52개 after |
| `qwen-live-after.json` | 로컬 baseline/after와 과거 RunPod의 관계를 명시한 산출물 인덱스 |
| `qwen-local-public9b-after-repeat.json` | 완료: 사전 고정 R04/C05/M01의 별도 반복 측정; 주 결과에 합치지 않음 |
| `qwen-local-public9b-*-review.json` / `*-metrics.json` | 원본과 분리된 근거 판정 및 지표 |
| `qwen-local-public9b-*-accounting.json` | 실제 모델 반환 근거와 계수 한계 |
| `qwen-local-public9b-after-change-manifest.json` / `application-changes.patch` | 이번 앱 수정의 전후 파일 hash와 patch |
| `qwen-live-failure-analysis.md` | 전후 실패 원인·잔존 문제 상세 |

정식 수치와 전후 전환은 `qwen-local-public9b-comparison.json`, 실패 분류는 `qwen-local-public9b-failure-causes.json`, 최종 무결성은 `qwen-local-public9b-final-integrity.json`을 참조한다. 앱 manifest는 나열된 경로 범위이며 모든 저장소 파일을 포괄하지 않는다. 원본 raw bytes hash는 review/comparison 및 병합 shard_history에 있다. 기존 adjudicate의 source_run_sha256은 annotation을 추가한 객체 digest이므로 raw byte hash와 혼동하지 않는다.

실행 중 추론 harness는 바꾸지 않았다. 종료 후 병합 helper의 shard probe·상태·카운터 메타데이터를 보완하고 43개 회귀를 통과했다. 요약 helper는 null query를 누락 근거로 유지하도록 수정했으며 실제 결과로 확인했다. 이 후처리 변경은 원본 사례·정답·모델 응답·점수를 바꾸지 않는다. 기존 미커밋 작업을 보존했으며 commit/push는 하지 않았다.
