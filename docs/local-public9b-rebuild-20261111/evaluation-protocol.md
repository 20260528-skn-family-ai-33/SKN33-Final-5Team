# 로컬 공개 Qwen3.5 9B 부분 리빌딩 평가

기존 52건은 회귀 평가로 사용한다. 독립 35건은 같은 Git HEAD에서 사전 작성·봉인한 holdout이다. 기존 52건과 독립 35건은 ID와 요청 문장이 서로 겹치지 않는다. 독립 세트의 정답, 후속 답변, 허용·금지 질문, 도구·operation, 업무별 산출물·의존성, 합성 실행 oracle과 의미 판정 기준은 기존 봉인본을 그대로 쓴다. 35건 전체를 실행한다. 앱 수정과 회귀 검증이 끝나기 전에는 독립 요청을 실행하거나 구현용 예시로 제공하지 않는다.

| 자료 | 저장소 경로 |
|---|---|
| 기존 52 정답 | `docs/qwen-live-evaluation-cases.json` |
| 독립 35 정답 | `docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json` |
| 독립 35 봉인 및 판정 기준 | `docs/runpod-custom-rebuild-20261111/independent-evaluation-freeze.json`, `independent-evaluation-rubric.md` |
| ledger 검토 기준·라벨 | 같은 디렉터리의 `metric-contract.md`, `ledger-review-format.md` |
| 로컬 공개 9B의 변경 전 비교점 | `docs/qwen-local-public9b-after-complete.json`, `qwen-local-public9b-after-review.json` |

변경 전 비교점은 위 완료 trace의 앱 소스 243개 SHA-256을 검증했다. 현재 구현을 시작하기 직전 243개가 모두 일치했다. 당시 52건 중 R01/T08은 코드 전용 경로였다. 이전 하니스는 SDK 일부 HTTP 계측이 누락돼 완료 응답을 원문·모델 ID로 대조한 수를 쓴다. 이번 계측은 SDK의 실제 HTTP 전송까지 기록한다. 아래의 요청 성공률과 지연은 코드 전용 경로를 포함한 전체 서비스 기준이다. 실측 케이스 수와 확인된 모델 응답 수는 별도 제시한다.

| 지표 | 분자와 분모 또는 단위 |
|---|---|
| 전체 요청 성공률 | 초기·최종 분기, 질문, 요청한 모든 업무·인자·연결·결과, 불필요한 추가 작업 부재까지 통과한 사례 / 검토한 사례. 각 사례의 전체 배점은 1이다. |
| 필요한 질문 재현율 | 실제 표시한 질문이 요청한 피질문 슬롯의 수 / gold에 필요한 누락 슬롯 수. 한 슬롯은 사례별 1회로 센다. |
| 불필요한 재질문 비율 | 후속 턴에서 불필요하며 이미 해결된 의미를 반복한 질문 수 / 후속 턴 표시 질문 수. 모든 턴의 불필요 질문률도 제공한다. |
| 확정 조건 충돌률 | 동일 호출·소유 키에서 사용자 근거에 반하는 채택값 / 모델 후보에 해당 키 주장이 있고 채택 상태를 판독할 수 있는 검토 기회. 후보가 키를 전혀 생성하지 않으면 이 분모의 관측 기회에서 빠진다. |
| 중복 업무 생성률 | 각 채택 Requirement Ledger에서 의미·대상·의존성·산출물이 이미 존재하는 두 번째 이후 task 수 / 검토한 채택 task 수. 각 단계가 새로 방출한 장부만 센다. |
| 답변 반영 정확도 | 후속 답변이나 수정으로 바뀐 gold 슬롯 중 마지막 관측 상태에 정확하게 반영된 수 / 실제 후속 응답을 받은 사례의 변경 슬롯 수. |
| 워크플로우 요구사항 충족률 | 최종 READY가 정답인 사례에서 도구·인자·입력 의존성까지 충족한 업무 수 / 해당 사례의 전체 gold 업무 수. 초안에 도달하지 못한 업무도 분모에 들어간다. |
| 생성 후 검증 성공률 | 노드가 있는 draft를 검사한 validate 단계 중 ready이며 issues가 없는 횟수 / 그런 draft를 검사한 횟수. 첫 초안과 재시도를 포함한다. 최종 사례별 schema_valid도 별도 산출한다. |
| 모델 호출 횟수 | 모델 호출 trace 수, 요청·응답 model ID와 body가 확인된 응답 수. 연결용 probe는 본 사례 수치에서 분리한다. |
| p50/p95 | 백엔드 케이스 시작~종료 wall time(초). p50은 중앙값, p95는 nearest rank이다. 후속 턴·검사·허용된 합성 실행을 포함한다. |

### 실측과 채점

루트 PowerShell에서 호스트 실행기를 실행한다. 코퍼스 2개를 같은 프로필에서 순차 처리한다. 실제 연결과 모델 digest를 확인한 후 완료한 raw trace는 이 디렉터리의 `legacy52.complete.json`, `independent35.complete.json`이다. 사례·턴별 원문 모델 응답, 지연, 분기, 질문, Workflow/Validator 결과와 합성 실행 경계가 남는다.

```powershell
.\Backend\.venv-agent\Scripts\python.exe scripts/verify/run-local-orchestration-reassessment.py
```

검토자는 아래 명령을 회귀군과 독립군에 각각 적용한다. `$runFile`, `$goldFile`에 해당 실측·정답 파일 경로를 지정한다. `$caseId`는 검토할 사례 ID이다. 요약에서 근거가 빠지면 raw의 `turns`, `stages`, `model_calls`를 확인한다.

```powershell
node scripts/verify/review-qwen-live.mjs $runFile --corpus $goldFile $caseId
.\Backend\.venv-agent\Scripts\python.exe scripts/verify/score-qwen-ledgers.py $runFile --corpus $goldFile --template --output ledger-template.json
.\Backend\.venv-agent\Scripts\python.exe scripts/verify/score-qwen-reassessment.py --run $runFile --corpus $goldFile --labels semantic-review.json --ledger-labels ledger-review.json --output metrics.json
```

의미 라벨은 비교점의 `qwen-local-public9b-after-review.json` 양식을 사용한다. gold SHA와 각 사례 trace digest, 전체 통과 여부, 관측 슬롯, 질문별 필요·반복 슬롯과 업무별 충족 여부를 근거와 함께 기록한다. ledger 라벨에는 검토한 task ID, 소유 키 충돌, 누락된 업무 및 출처를 기록한다. 의미 라벨이 없는 때에도 마지막 명령에서 두 라벨 인자를 생략하면 기계 관측 항목만 채점할 수 있다. 성공률을 인용할 때 실행한 사례·검토한 사례·분모를 함께 표시한다. 분모 0 또는 미검토는 null이다. 실행 후 발견한 gold 문제는 예외로 기록하며 봉인된 정답은 유지한다.
