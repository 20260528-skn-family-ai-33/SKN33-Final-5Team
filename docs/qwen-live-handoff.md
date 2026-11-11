# 다른 컴퓨터에서 이어서 작업할 프롬프트

아래 내용을 새 Codex 대화에 전달하세요. 프로젝트의 미커밋 파일도 함께 옮겨야 합니다. Git 저장소만 새로 clone하면 이번 작업은 포함되지 않습니다.

---

A4B의 실제 Qwen 기반 되묻기·워크플로우 생성 평가를 이전 컴퓨터에서 중단했습니다. 다음 상태를 이어받아 작업을 완료하세요. 먼저 `AGENTS.md`, 이 파일, `docs/qwen-live-user-request.txt`, `docs/clarification-system-audit.md`, `docs/qwen-live-evaluation-plan.md`를 읽으세요. 실행 결과를 실제 모델·합성 회귀·미실행으로 구분하고, 기존 변경을 되돌리거나 전체 시스템을 재작성하지 마세요.

## 1. 작업 파일 복원

- 프로젝트: `SKN33-Final-5Team`.
- 기준 Git HEAD: `525f66325a14f8d3995ad0cb57277e5a6f4f23ed`.
- 기존 브랜치: `codex/gmail-connection-setup-ux-20261110`.
- 이전 작업 폴더: `C:/Users/munky/OneDrive/Desktop/final/SKN33-Final-5Team`. 새 컴퓨터에서는 현재 경로를 찾아 사용하세요.
- 소스 변경은 커밋·push하지 않았습니다. 이전 사용자 작업과 앞선 되묻기 수정이 이미 섞여 있으므로 모두 보존하세요.
- 전달 ZIP은 **전체 저장소가 아니라 기준 HEAD 위에 복원할 변경 파일 묶음**입니다. 같은 HEAD의 별도 깨끗한 checkout에 ZIP 안의 `workspace/` 내용을 저장소 상대경로대로 복원하세요. 기존 작업 폴더에 무조건 덮어쓰지 마세요. 전체 작업 폴더를 복사했다면 ZIP을 다시 덮어쓸 필요는 없습니다.
- ZIP의 `handoff-manifest.json`에는 파일 목록·SHA256·기준 HEAD가 있습니다. `.env`, 인증정보, `.git`, 의존성 폴더, 실제 데이터, 모델 가중치는 포함하지 않았습니다.

## 2. 명시적 중단 상태

사용자가 2026-11-10 평가 중단과 다른 컴퓨터 인계를 요청했습니다. 평가 프로세스 및 잔여 평가 자식 프로세스가 없는 것을 확인했습니다. 중단 뒤 추가 Qwen 호출은 하지 않았습니다. RunPod Pod 자체를 종료한 것은 아닙니다.

- 정답 코퍼스: 52개. READY 10 / CLARIFY 10 / 날짜·시간·수량 8 / 다중 턴 12 / 미지원 5 / 복합 7. 모호한 1개는 별도 제외.
- 평가 완료 결과가 저장된 사례: **29개** (`R01–R10`, `C01–C10`, `T01–T08`, `M01`). 이 숫자는 성공 사례 수가 아닙니다. R10은 worker 예외도 그대로 보존돼 있습니다.
- 이 중 실제 Qwen을 호출한 사례: **27개**. R01, T08은 기존 코드 recipe로 처리돼 0회 호출입니다.
- 저장된 사례 내부 호출: **78회**. 구조화 연결 probe 1회는 성공 메타데이터가 있으며, 중단된 M02의 미저장 호출 수는 알 수 없습니다. 총 사용량·비용을 78회로 단정하지 마세요.
- **남은 23개:** `M02 M03 M04 M05 M06 M07 M08 M09 M10 M11 M12 U01 U02 U03 U04 U05 X01 X02 X03 X04 X05 X06 X07`.
- M02 도중 중단했습니다. 완전한 trace가 없으므로 M02부터 새 격리 세션으로 다시 실행하세요. 이전 임시 SQLite의 진행 상태를 성공 결과로 합치지 마세요.
- `docs/qwen-live-baseline.json`은 중단 사실을 반영해 `status=paused`이고 미실행 항목을 명시했습니다. `qwen-live-baseline-interrupted-raw.json`은 중단 직전 원본 사본입니다. 원래 실행기가 진행 중에도 `completed` 헤더를 쓰던 문제 때문에 **원본 헤더를 전체 완료로 읽지 마세요**.
- baseline 완료 전 애플리케이션 코드를 추가 수정하지 않았습니다. 이번 live 실패의 수정은 **아직 적용하지 않았습니다**. 실패 재현 테스트만 추가했습니다.

## 3. 고정 조건 및 모델 연결

정답 SHA256:
`e939c051a940915c4061b45221e7f1353b8ce2157677d379ae193de13b1300da`

baseline 애플리케이션 source SHA256:
`6550b20928227b74c6a2c33cded04e62d359a6550c034dce935cad5230e01be5`

실행 결과의 `code_manifest.files`가 각 소스 파일 hash를 보관합니다. 결과를 보고 정답을 변경하지 마세요. 매 유형의 3번째 사례는 사전에 holdout으로 지정했습니다. 수정은 development 실패를 근거로 하세요.

실제로 연결했던 주소:
`https://prfafjg2xk99cx-11434.proxy.runpod.net/v1`

- provider: `ollama`.
- MAIN: `a4b-qwen35-9b-final664-pilot32k`.
- capability: `a4b-qwen35-9b-previous-pilot32k`.
- 모델 목록과 실제 응답 model metadata에서 위 ID를 확인했습니다. `/api/show`는 404여서 서버 가중치·최대 context 자체는 독립 검증하지 못했습니다.
- 기존 문서화된 9B 프로필을 자식 프로세스에만 적용했습니다. `.env`나 운영 endpoint는 수정하지 않았습니다.
- `A4B_PLANNER_MODE=deepagent`; capability/condition/requirement는 `enforce`; verifier는 `off`지만 Python validator는 유지.
- native `num_ctx=32768`, `num_predict=4096`, temperature 0, top_p 0.8, seed 42, think false. main timeout 180초, 동시성 1. OpenAI 호환 planner는 기존 model_factory 설정을 사용합니다.
- API 키는 전달 파일에 없습니다. 사용자가 새 컴퓨터의 환경변수/비밀 저장소로 제공해야 합니다. 채팅에 키를 출력하거나 `.env`를 덮어쓰지 마세요.
- `.env`의 `A4B_LLM_*`를 `A4B_MAIN_*`로 매핑하는 기존 `scripts/run-configured.mjs` 경로가 있습니다. 예전 감사 보고서의 “MAIN 미설정이라 endpoint 없음” 판단은 불완전했습니다. 기존 낡은 endpoint는 404였고 사용자가 새 주소를 제공했습니다.

**로컬 GPU로 전환하는 경우:** 서비스는 로컬 Ollama/vLLM을 지원하지만, 현재 평가 실행기의 안전장치는 RunPod HTTPS host만 허용합니다. 평가기·trace의 목적지 허용 규칙을 사용자가 지정한 로컬 모델 주소에 한해 명시적으로 확장해야 합니다. 외부 데이터/발송 차단은 유지하고 임의 모델 fallback은 금지하세요. 같은 이름만 붙인 기본 Qwen은 여기서 사용한 custom 모델과 같다고 볼 수 없습니다. 모델 가중치·서빙·하드웨어가 달라지면 **새 환경 baseline을 동일 52개로 처음부터 생성**하고 기존 RunPod 결과와 별도 보고하세요. 모델/설정이 다른 결과를 기존 baseline의 나머지 부분처럼 합치지 마세요. 사용자는 아직 실제 평가를 로컬 GPU로 바꾸라고 지시하지 않았습니다.

## 4. 이전 시험 환경의 제한

- Python 3.12.14, pytest 9.2.0, LangGraph 1.2.19, Pydantic 2.14.2, checkpoint-sqlite 3.1.3.
- 이전에는 번들 Python과 작업공간 밖 `pr56-test-deps`를 PYTHONPATH로 사용했습니다. 이 경로는 새 컴퓨터에 없으므로 그대로 실행하지 마세요. 저장소 `Backend/requirements/agent*.txt` 등과 기존 setup 절차를 확인해 환경을 준비하고 실제 버전을 기록하세요.
- Node >=22.13.0, 루트 package-lock/package.json 기준 의존성을 설치하세요.
- **SVM56은 이 시험 환경에서 joblib/scikit-learn/scipy가 없어 `ml_status=failed`, `ModuleNotFoundError`였습니다.** 파일 없는 경우와 구분됩니다. 기존 코드의 전체 도구 목록 대체 경로가 작동했습니다. 추천 ML 품질을 실측했다고 주장하지 마세요. 코드나 모델을 제거하지 않았습니다.
- 나머지 baseline을 합칠 때 SVM 환경을 중간에 활성화해 비교 조건을 섞지 마세요. ML 포함 환경을 준비하려면 별도 baseline/after 평가로 구분하세요.
- 실제 HTTP 서버·브라우저 UI는 구동하지 않았습니다. live 범위는 실제 service.worker → LangGraph/checkpoint → Qwen → validator → 허용된 로컬 실행입니다. API/Frontend는 별도 계약/상태 테스트와 typecheck입니다.
- 계정 연결 값은 합성 metadata입니다. 실제 Gmail·Notion·고객 DB를 조회하거나 메일을 발송하지 않았습니다. native 표 도구 allowlist만 합성 행으로 실행했습니다.

## 5. 바로 이어서 실행할 순서

1. 전달 파일·source/gold hash·모델/의존성 환경을 확인하세요. 필요하면 현재 endpoint 모델 목록을 다시 조회하세요. 인증정보는 환경으로만 주입하세요.
2. **애플리케이션 수정 전에** 나머지 23개 baseline을 원래와 같은 모델·프로필·시간·리소스로 실행하세요. 정답 파일은 다시 생성하지 않습니다.
3. 실행기는 기존 결과 파일을 덮어쓰지 않으며 자동 resume 옵션은 없습니다. 남은 ID를 `--case`로 전달해 새 shard 파일을 만드세요.

PowerShell 예시 — `<python-path>`는 새 환경 경로로 바꾸고, 인증 및 `A4B_MAIN_BASE_URL`은 사전에 안전하게 설정:

```powershell
$remaining = Get-Content docs/qwen-live-remaining-cases.json -Raw | ConvertFrom-Json
$caseArgs = @()
foreach ($caseId in $remaining) { $caseArgs += @('--case', $caseId) }
node scripts/verify/run-qwen-live-evaluation.mjs --python '<python-path>' --profile documented-9b-deepagent --live --phase baseline --output docs/qwen-live-baseline-part2.json --max-calls 220 @caseArgs
```

4. 완료 후 다음 helper로 **같은 source/gold/model 설정의 중복 없는 결과만** 병합하세요. 기존 파일을 덮어쓰지 마세요.

```text
python scripts/verify/merge-qwen-live-runs.py docs/qwen-live-baseline.json docs/qwen-live-baseline-part2.json --output docs/qwen-live-baseline-complete.json
```

이 helper는 설정·source hash 불일치 또는 중복 수행 ID를 거절합니다. 의존성·모델 가중치까지 자동 증명하지는 않으므로 환경 동등성은 별도로 확인하세요. 반복 측정 결과를 유리한 쪽으로 골라 병합하지 마세요.

5. 전체 baseline을 검토한 뒤 development 실패에 한해 최소 수정하세요. 새 실패 재현 테스트는 아래에 있습니다. 기존 정상 기능과 Qwen/추천/validator/저장 경로를 유지하세요.
6. 동일한 전체 52개를 `--phase after --baseline docs/qwen-live-baseline-complete.json --output docs/qwen-live-after.json --max-calls 400`으로 재평가하세요.
7. 기존 회귀 테스트, 전후 수치 및 분모, 클래스별 F1, 실패 원인·잔존 문제, 모델 추가 필요 여부를 작성하세요. 실제로 하지 않은 시험은 성공으로 기록하지 마세요.

## 6. 확인된 실패와 수정 준비 상태

수정 전 재현: `Backend/tests/design/test_qwen_live_failures.py`.
최신 결과: `docs/qwen-live-failures-before3.xml`, **9 failed / 4 passed**. 이 실패들은 수정 예정 회귀 테스트이며 아직 수정하지 않았습니다.

- **C05 (development):** “중복 제거”만 요청했는데 조건 모델이 `duplicate.policy=first`를 추측하고 Python이 specified로 수용. `condition_readiness._grounded_enum`에 첫/마지막 유지 정책의 근거 확인이 빠져 있습니다. 원문 명시·실제 질문 답변을 검증하고 체크포인트의 이전 추측값도 재검증해야 합니다. 복원 후 “마지막 행” 답변이 READY로 진행하는 테스트도 추가했습니다.
- **C08 (development):** 명시하지 않은 `schedule.minute=0`을 확정. 시각도 같은 종류의 취약 경로입니다. 기존 `schedule_literals` 등을 재사용해 근거 없는 시간 확정을 막되, 명확한 시각과 정각·후속 답변은 보존하세요.
- **C07 (development):** 질문 최대 2개를 분·시 두 문항에 모두 써서 요일을 뒤로 미룸. 시·분 누락은 한 번의 시각 질문으로 묶고 다음 필수 조건을 함께 제시할 수 있습니다. 내부 unresolved에는 두 슬롯 모두 보존해야 합니다.
- **C01 (development):** API가 전달한 `user_timezone=Asia/Seoul`이 있어도 `requirement_ledger.grounded_schedule`은 원문에 시간대 문구가 없으면 실패합니다. 지정된 시간대와 명시된 매일 09:00을 검증 가능한 값으로 취급하는 방안을 검토하세요. 잘못된/미정 시간대는 추측하지 않고 명시적 시간대 변경은 우선해야 합니다. 시간대만 고쳐도 데이터 출처·수신자 누락 문제가 자동 해결되는 것은 아닙니다.
- **C02 (development):** “분석할 자료 ID를 알려주세요.” 노출. `capability_check._question_text`는 파일 ID 단독 문구만 바꿔 문장형을 놓칩니다. 자료 연결 UI 안내로 고치되 “고객 ID 열” 같은 실제 업무 열은 보존하세요.
- **R10 (development):** scalar 연산을 table compute로 추출하고, planner가 finish 출력을 표 입력으로 연결하여 두 번 생성 후 ValueError. 현재 Requirement Spec이 등록 operation 전체를 표현하지 못하는 문제가 있습니다. 대규모 재작성 없이 해결 여부를 검토하고 미해결이면 명확히 보고하세요.
- **C10 (development), C03 (holdout):** 계정 연결·자료 확인을 도구 부재로 분류. 실제 등록된 email.send와 실행 전 연결 전제를 모델이 혼동합니다. 원본 capability 응답이 근거입니다.
- **R03 (holdout):** 정렬을 filter로 잘못 해석해 비교 기준·값 질문. holdout 문장에 맞춘 특별 규칙을 만들지 마세요.
- **C09 (holdout):** 수식·결과 열이 없는데 read-only ledger를 통과한 planner가 `qty*unit_price`와 `calculated_amount`를 만들어 READY. 중요한 의미 검증 실패이며 실행하지 않았습니다.
- **R02/R04/R05/R06/R07:** 일부 requirement 응답은 read task만 남겼고 최종 planner가 원문에서 작업을 보완했습니다. 최종 결과가 맞았다고 중간 추출 완전성이 보장되는 것은 아닙니다.
- **C06 (holdout):** “중요한 메일”을 important 필터로 추측한 뒤 엉뚱한 표 workflow를 생성, validator가 거부. 기준 질문이 없었습니다.

이미 확인한 정상 근거:
- R02, R04, R05 및 M01은 허용된 native 표 workflow를 실제 로컬 합성 데이터로 실행해 기대 행과 일치했습니다.
- T01–T07은 실제 의미 추출 및 날짜/수량 보존을 확인했습니다. 뉴욕 DST 시작·종료 날짜의 query가 각각 23/25시간입니다.
- M01은 실제 질문 → “20 이상” 답변 → 같은 세션을 다시 열어 READY → 생성/실행까지 진행했습니다. 아직 다른 11개 다중 턴 평가는 끝나지 않았습니다.

## 7. 테스트·산출물·도구 위치

기존 코드/회귀의 상세 감사: `docs/clarification-system-audit.md`.

이번 중단 전 기존 회귀:
- `docs/qwen-live-regression-baseline.xml`: **1,882 passed**. 이후 새 실패 재현 테스트와 scorer 테스트를 추가했으므로 현재 전체 테스트가 모두 통과한다고 말하면 안 됩니다.
- 평가기 단독: `test_qwen_live_evaluation.py` **12 passed**.
- `docs/qwen-live-typescript-tests.tap`: **41 passed**.
- Frontend와 Backend/api `tsc --noEmit --incremental false` 통과.
- 첫 pytest는 sandbox 임시 폴더 생성 권한 때문에 fixture 오류가 났습니다. workspace 안 `--basetemp`를 사용한 재실행은 통과했습니다. 새 실행에서는 새 전용 basetemp 경로를 쓰세요.

```text
cd Backend
python -m pytest tests/design tests/capabilities tests/conditions -q --basetemp=../data/qwen-regression-new-tmp --junitxml=../docs/qwen-live-regression-after.xml
cd ..
node --import tsx --test Backend/api/tests/request-timezone.test.ts Backend/api/tests/gmail-design-context.test.ts Frontend/tests/agent-studio-state.test.ts
node node_modules/typescript/bin/tsc -p Frontend --noEmit --incremental false
node node_modules/typescript/bin/tsc -p Backend/api --noEmit --incremental false
```

평가 파일:
- `qwen-live-preflight.mjs`: 인증정보를 출력하지 않는 endpoint 점검.
- `run-qwen-live-evaluation.mjs`: 기존 환경 aliases/문서화 프로필을 자식 프로세스에 적용.
- `verify-qwen-live-evaluation.py`: real worker/checkpoint/model/validator E2E, 사례별 임시 SQLite, 외부 실행 금지.
- `qwen_live_trace.py`: 실제 native/SDK 응답과 단계별 상태 기록. SDK 응답은 원본 HTTP stream 프레임과 구분.
- `qwen_live_support.py`: hash 검증, 실제 질문에 맞춘 답변 정책, 실행 allowlist, 지표 집계.
- `review-qwen-live.mjs`: 결과를 읽기 쉬운 근거 형태로 출력.
- `adjudicate-qwen-live.py`: 수동 근거 라벨을 지표로 집계. 모델 자체평가를 정답으로 사용하지 않음.
- `compare-qwen-live-evidence.py`: 의미 상태·workflow가 정확히 동일할 때만 기존 검토 라벨 재사용.
- `summarize-qwen-live.py`: 실제 호출·세션/질문 ID/대화 보존·Gmail query 사실 요약.
- `merge-qwen-live-runs.py`: 같은 환경 식별값의 분할 결과 병합.

`docs/qwen-live-baseline-review.json`은 **검토 중인 초안**입니다. T06/T07/T08/M01 및 아직 평가하지 않은 사례의 라벨이 빠져 있습니다. intent/slot 점수의 범위와 누락 분모를 확인하고 각 판정의 근거를 검토하세요. 일부 잘못된 raw workflow가 파싱 예외로 끝나면 기존 실행기의 `schema_valid`가 null이라 전체 후보의 schema 실패율에서 빠질 수 있습니다. 최종 집계 시 이 계측 한계를 보완하거나 명시하세요. 미저장 M02 호출과 latency는 추정해 채우지 마세요.

최종 필수 산출물 중 `qwen-live-evaluation-plan.md`, `qwen-live-evaluation-cases.json`, paused `qwen-live-baseline.json`은 있습니다. **완전한 baseline, after, failure-analysis, evaluation-report는 아직 미완료**입니다. 최종 파일명과 이전 shard 관계를 명확히 유지하세요.

평가 결과가 나오기 전 별도 sLLM·파인튜닝을 추가하지 마세요. 기존 Qwen 역할 분리, 근거 검증, 상태 병합의 개선을 우선 검증하세요. 진행 상황은 한국어로 간단히 공유하고, 사용자 중단 요청이 오면 실제 평가 프로세스를 즉시 중단하고 결과를 보존하세요.
