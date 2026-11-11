# Qwen 실제 모델 평가 계획

2026-11-10, Asia/Seoul. 기존 합성 평가와 분리한다. 기존 애플리케이션 소스는 baseline 완료 전 변경하지 않는다. 평가 실행기·계측기의 수정은 모델 개선으로 집계하지 않는다.

## 환경과 설정

기존 `.env`는 `A4B_LLM_BASE_URL/MODEL/API_KEY`를 사용한다. `scripts/run-configured.mjs:13–21`이 MAIN 설정으로 연결한다. 과거 보고서의 “MAIN 주소 미설정이므로 endpoint 없음” 판단은 불완전했다. 기존 주소는 실제 조회에서 `/v1/models`, `/api/tags` 모두 404였다. 사용자가 현재 주소를 제공했고, 새 서버의 두 경로는 모두 200이다. 인증은 프로세스 메모리에만 전달하며 `.env`를 쓰지 않는다.

새 서버에서 확인한 등록 모델은 `a4b-qwen35-9b-final664-pilot32k:latest`, `a4b-qwen35-9b-previous-pilot32k:latest`, `qwen3.5:4b`다. 로컬 설정에 남은 `a4b-qwen35-4b-16k`는 등록돼 있지 않다.

평가에는 저장소의 `.env.aws.qwen35-9b.example`와 `.env.aws.qwen35-9b-deepagent.example`를 **자식 프로세스에만** 적용한다. 운영 서버의 환경을 변경하지 않는다. 이 프로필이 현재 운영 앱의 실환경과 완전히 동일하다고 주장하지 않는다.

| 항목 | 평가 프로필 |
|---|---|
| provider | Ollama, 기존 native `/api/chat` 및 DeepAgent `/v1/chat/completions` |
| 설계/요구사항/의미 추출 | 최종664 9B |
| 지원 판단 | 기존105 9B |
| capability / condition / requirement | enforce / enforce / enforce |
| planner | deepagent, requirement generation baseline |
| temperature | 0, 저장소의 기존 9B 설정 |
| native context / output ceiling | 32768 / 4096 |
| thinking | 기존 클라이언트 설정 false/none |
| top_p / seed | 기존 모델 연결 코드 값; 요청 기록에 남김 |
| timeout | 호출당 180초; 코드의 기존 제한된 repair 유지 |
| concurrency | 1 |
| verifier | off; Python 검증기는 유지 |
| 추천 | hint / svm56. 실제 로딩 성공 여부는 trace로 별도 확인 |
| 업무 저장소 | 매 사례 전용 임시 SQLite. 운영 DB·계정·파일 접근 없음 |

`/api/show`에서 제공하는 모델 메타데이터가 있으면 context length·family·quantization을 기록한다. gateway가 해당 경로를 제한하면 “조회 불가”로 기록하고 실제 응답 model ID와 구분한다. 모델 이름만으로 가중치 내용의 무결성을 증명하지 않는다.

## 고정 정답

`qwen-live-evaluation-cases.json`: 52개 평가 대상, 모호한 1개 제외. READY 10, CLARIFY 10, 시간/수량 8, 다중 턴 12, UNSUPPORTED 5, 복합 7이다. 다중 턴 중 10개는 CLARIFY → 실제 질문에 맞춘 답변 → READY를 목표로 하고, 2개는 수량 수정/다음날 기간 유지다.

각 항목은 요청, 사전 맥락, 합성 자료·연결 메타데이터, 고정 기준시각/시간대, 의도·슬롯, 누락/금지 질문, 허용 도구/operation, 순서와 의존성, 실행 모드와 기대 결과를 가진다. 모든 도구/operation은 현재 카탈로그와 코드로 대조했다. 정답 파일은 hash로 고정하며 실행 결과에 맞춰 변경하지 않는다.

Notion 저장과 고객 DB 직접 조회는 현재 카탈로그에 없으므로 각각 복합 F 사례와 DB 답변 사례에서 미지원으로 판정한다. DB 연결 메타데이터만으로 없는 DB 조회 도구를 만들어내지 않는다. 누락 조건과 미지원 기능은 별개의 정답이다.

각 유형의 매 3번째 사례를 holdout으로 미리 분리한다. 수정은 development 실패를 근거로 하고 holdout은 평가에만 사용한다. source/골드/설정 hash를 각 실행에 저장한다.

## 실행 순서와 비용 제한

1. 모델 목록과 작은 JSON schema 요청을 확인한다. 실제 응답 model ID가 요청 모델과 다르면 중단한다.
2. R01/C04/M01 세 사례로 평가 계측기와 실제 서비스 세션 경로를 점검한다. 이 결과는 전체 baseline이 아니다.
3. 동일 기준시각·자료·프로필로 52개 baseline을 순차 실행한다. 호출 예산과 첫 transport 실패에서 중단하는 회로를 둔다. 미실행은 실패율의 성공 분자로 넣지 않는다.
4. 질문은 실제 출력된 pending_questions 또는 draft.questions에 대응한다. 정의된 슬롯에 맞지 않거나 여러 다른 답변과 모호하게 대응되면 추측해 답하지 않고 검토 대상으로 멈춘다. 이 경우 harness 제한과 모델 질문 오류를 구분한다.
5. 각 사용자 턴은 실제 worker handler를 다시 호출해 DB/checkpoint를 다시 연다. 같은 session/thread ID, 질문 ID, 메시지·revision과 snapshot을 기록한다.
6. 실제 반환 JSON·도구 호출·서버 model ID·usage·단계별 state·최종 workflow·검증 결과를 수집한다. 원본 HTTP stream 프레임이 아닌 SDK에서 합쳐진 응답은 그 사실을 표시한다. 비밀값·실제 주소 패턴은 저장 직전에 제거한다.
7. 실행은 allowlist의 native 표 도구만 합성 자료로 허용한다. Gmail/Notion/DB/이메일 발송, agent_node, 중첩 저장 workflow와 임의 경로 operation은 실행하지 않는다. 외부 workflow는 설계/구조 검증과 실행을 구분한다.
8. 실패를 분류하고 원본 응답으로 재현 가능한 오류만 최소 수정한다. 동일 골드·설정으로 after를 실행한다. 전체 재평가가 예산/서버 오류로 중단되면 완료분/미실행분을 구분한다.
9. 대표 개발/holdout 일부를 추가 1회 실행해 변동성을 관찰한다. 이를 전체 코퍼스의 통계적 반복 검증이라고 표시하지 않는다.

## 지표와 분모

- Intent Accuracy: 고정 의도/업무를 독립 검토한 사례 중 일치한 수 / 검토 사례 수.
- Slot Extraction F1: 검토한 슬롯의 `2TP / (2TP+FP+FN)`. 날짜는 고정 절대 구간, 수량·최신 변경은 실제 인자를 대조한다.
- Routing Macro F1: READY/CLARIFY/UNSUPPORTED 클래스별 F1 평균. 시스템 오류를 정답 CLARIFY로 바꾸지 않고 정답 클래스 FN으로 센다.
- Necessary Question Recall: 실제 필요한 누락 조건 중 질문한 수 / 필요한 누락 조건 수.
- Unnecessary Question Rate: 불필요한 질문 수 / 검토한 질문 수.
- Repeated Question Rate: 이미 해결한 슬롯을 다시 물은 수 / 검토한 후속 질문 수.
- Answer Incorporation Accuracy: 새 답변·수정 값이 올바르게 반영된 수 / 검토 가능한 답변 조건 수.
- Clarification Completion Rate: 올바른 READY에 도달한 다중 턴 사례 / 시작한 되묻기 사례.
- Workflow Schema Validity: 생성 후보 중 스키마·기존 검증을 통과한 후보 수 / 생성 후보 수. 생성 자체가 안 된 사례는 별도 전체 성공률에서 실패 처리한다.
- Tool/Operation Accuracy: 고정 업무별 올바른 tool/operation 대응 수 / 검토한 업무 수.
- Requirement Coverage: 설정과 데이터 의존성까지 충족한 필수 업무 수 / 필수 업무 수. 도구가 존재한다는 부분 점수와 다르다.
- Workflow Execution Success: 합성 실행 결과까지 일치한 수 / 실제 로컬 실행 수. 외부 실행 미실시는 분모에서 제외하고 개수를 별도 표시한다.
- Latency p50/p95: 실제 모델 호출을 포함한 완료/실패 시나리오 시간. p95는 nearest-rank. 미실행과 코드 전용 recipe는 별도다.

자동 판정으로 입증할 수 없는 의미·질문 적절성은 독립 검토 전까지 null이다. 모델의 자체 평가를 정답으로 사용하지 않는다. `adjudicated_metrics`에는 검토 근거와 위 정의의 분자·분모를 기록한다. 모든 지표에 미검토/미실행 개수를 표시한다.

## 재현 명령

인증·현재 endpoint는 기존 환경변수로 공급하고 명령/로그에 값을 쓰지 않는다. `.env`는 변경하지 않는다. `--python`에는 Backend 의존성이 준비된 Python을 지정한다.

```powershell
node scripts/verify/qwen-live-preflight.mjs --output docs/qwen-live-environment-new.json
node scripts/verify/run-qwen-live-evaluation.mjs --python <python-path> --profile documented-9b-deepagent --live --phase baseline --output docs/qwen-live-baseline.json --max-calls 400
node scripts/verify/run-qwen-live-evaluation.mjs --python <python-path> --profile documented-9b-deepagent --live --phase after --baseline docs/qwen-live-baseline.json --output docs/qwen-live-after.json --max-calls 400
```

이미 있는 결과 파일은 실행기가 덮어쓰지 않는다. 추가 실행에는 새 파일명을 사용한다. 이 실행기는 Backend 서비스 E2E이며 실제 브라우저/HTTP 서버 전체를 구동하는 E2E와 동일하지 않다. Frontend/API 전달은 별도 계약·컴포넌트 회귀 검증으로 구분한다.


## 로컬 공개 모델 평가 완료 — 2026-11-11

사용자의 로컬 GPU·공개9B 선택에 따라 기존 RunPod와 분리한 전체52개 baseline/after와 별도3개 반복을 완료했다. 과거 qwen-live-baseline.json은 paused 상태로 보존했다. 정식 비교 파일은 qwen-local-public9b-baseline-v2.json과 qwen-local-public9b-after-complete.json이며, qwen-live-after.json은 이 관계를 명시한 인덱스다. 실제 모델 평가와 합성 회귀·미실행 범위, 실패를 포함한 최종 수치는 qwen-live-evaluation-report.md를 참조한다.
