# 9B Planner 및 Capability 독립 지연 실험

## 최종 측정 결과

기준본, 범용 Planner 실험, Capability 단독, 표 전용 Planner 단독, 채택 조합에서 H01·H21·H32를 실제 9B로 각각 측정했다. 다섯 구성 모두 세 사례의 동결 정답을 통과했다. H21을 각 실행의 첫 사례로 둔 별도 기준본·최종본 대조도 양쪽 모두 통과했다. 서로 다른 실제 요청은 3개이며, 재측정을 포함한 사례 실행은 17회다. 실제 이메일 발송은 0회다. 전체 52·35·30건은 실행하지 않았다.

| 지표 | 안정본 | 최종 조합 |
|---|---:|---:|
| H01 · 필터 CSV | 113.71초 | 91.55초 |
| H21 · 정책 질문 및 CSV, 두 턴 합산 | 227.50초 | 113.76초 |
| H32 · 동일 요약 파일 및 메일 본문 | 105.53초 | 70.44초 |
| 세 사례 p50 | 113.71초 | 91.55초 |
| 사례 모델 호출, 사전 점검 제외 | 13회 | 11회 |
| 모델 입력 / 출력 토큰 | 107,029 / 3,045 | 80,599 / 2,354 |

H32는 지원 판단에서 2회가 줄었고 최종 실행에서 남은 두 단계는 Planner 50.96초, Requirement 19.26초였다. 표 Planner를 고정한 별도 대조에서도 H32는 111.33초에서 70.44초로 줄었다. H01의 Planner는 입력 2,651토큰과 출력 92토큰이 줄었다. H01 전체는 기준본·표 단독 대조에서 24.66초 줄었으나, Capability 단독과 조합을 비교하면 0.77초 길었다. H01의 사용자 대기 시간이 모든 실행에서 줄었다고 판단할 수 없다. [단계별 대조 및 부하 차이](../runtime/planner-capability-9b-experiment/measured-results.md)

### H21의 30초 목표

| 실행 조건 | 턴 | 안정본 | 최종 조합 | 최종값과 30초 목표의 차이 |
|---|---|---:|---:|---:|
| H01 다음에 H21 실행 | 첫 정책 질문 | 38.75초 | 40.22초 | 10.22초 초과 |
| H01 다음에 H21 실행 | 답변 후 검증된 결과 | 188.54초 | 73.26초 | 43.26초 초과 |
| 사전 점검 직후 H21 단독 | 첫 정책 질문 | 22.46초 | 22.15초 | 7.85초 여유 |
| 사전 점검 직후 H21 단독 | 답변 후 검증된 결과 | 79.91초 | 68.35초 | 38.35초 초과 |

세 사례 연속 측정에서 안정본 H21 둘째 턴의 네이티브 호출들은 모델 로드 98.91초를 보고했다. H21을 첫 사례로 실행한 추가 대조에서는 같은 턴의 네이티브 로드가 안정본 0.02초, 최종본 0.04초였다. 이 조건에서 답변 후 지연은 **11.56초 단축**됐고 Planner 단계는 **52.48초 → 40.62초**였다. 첫 질문은 양쪽 모두 1회, 입력 10,119·출력 269토큰이었으며 질문 ID와 최종 중복 정책은 그대로였다. 첫 턴의 30초 달성은 실행 순서와 모델 로드 상태에 영향을 받았다. [H21 단독 전후 대조](../runtime/planner-capability-9b-experiment/h21-first-comparison.json)

## 채택한 변경

표 경로에서 확정된 Requirement의 같은 spec은 한 번 전달하고 task가 참조하는 인덱스를 남겼다. 허용 도구가 이미 4개로 닫힌 경우에만 다른 도메인의 설명과 도구 추천을 생략한다. 일반 요청에서 기존 시스템 문구, 전체 도구 탐색 및 상세 계약은 유지된다. 저장 세션의 SDK 전송 직전 요청 전체를 비교해 H01·H21은 별도의 표 최적화 후보와 같고 H32는 안정본과 같음을 확인했다. [모델 입력 비교](../runtime/planner-capability-9b-experiment/planner-table-boundary.json) · [표 경로 수정](../Backend/agent_builder/design/nodes/conversation.py)

문서 요약을 파일과 메일 본문으로 함께 전달하는 요청은 기존 소유 함수가 요청 전체를 해석하고 원본 문서, 두 소비자의 포트, Markdown 지원, 발송 필수 인자·실행 도구 계약을 증명할 때 기존 Capability 판단을 재사용한다. 다른 본문, 추가 수신자, 승인 단계, 불완전한 계약은 기존 판단을 따른다. H21의 첫 지원 응답에 원본 읽기·CSV 출력의 근거가 빠져 있어 두 번째 턴의 판단은 유지했다. Validator의 기존 텍스트 출력 형식 목록을 공유했고 승인·실행 권한은 그대로 유지된다. [지원 판단](../Backend/agent_builder/capabilities/capability_judgement.py) · [허용 형식 회귀](../runtime/planner-capability-9b-experiment/capability-export-formats.json)

[5개 구성의 전체 성능표 및 사례별 실측](../runtime/planner-capability-9b-experiment/measured-results.md) · [최종 9B 원본](../runtime/planner-capability-9b-experiment/COMBINED.json) · [최종 전후 비교](../runtime/planner-capability-9b-experiment/combined-comparison.json)

## 정상·반대 사례 및 회귀

최종 앱에서 자동 테스트 **2,433/2,433**이 통과했다. 이전 2,413개의 테스트 식별자가 모두 포함됐고 20개가 추가됐다. 실패, 오류, 건너뜀은 0건이다. pytest 경고 출력은 10건이다. 모델 없는 정상 R02·R04·R05는 모두 저장 게이트 및 기대 행 검사를 통과했다. 표 범위 밖의 문서 입력 보존, 추가 승인·다른 본문·복수 수신자, 등록 도구/필드 변경, Markdown·JSON 허용 및 CSV 거절을 반대 사례로 확인했다. 저장 응답 H01·H21·H32의 서비스 재생은 각 최종 단독 후보에서도 3/3이었다. [최종 자동 회귀](../runtime/planner-capability-9b-experiment/final-regression.xml) · [정상 3건](../runtime/planner-capability-9b-experiment/normal-replay.jsonl) · [표 Planner 세션 재생](../runtime/planner-capability-9b-experiment/planner-table-replay.jsonl) · [지원 판단 세션 재생](../runtime/planner-capability-9b-experiment/capability-session-replay.jsonl)

## 측정 조건

통합 안정본과 각 후보의 차이를 하나씩 분리했다. 같은 RTX 4060 Ti, 같은 로컬 Ollama 9B 가중치 `dfd32b09718e…`, 문맥 32,768, temperature 0, seed 42를 사용했다. H01·H21·H32 순서로 세션을 새로 열고, 실제 워커의 저장·복구 및 Validator를 통과시켰다. 합성 CSV의 내용과 행 순서를 검사하고, H32는 같은 요약이 원래 수신자·제목의 메일 본문과 Markdown 파일에 연결된 설계에서 종료했다. 외부 커넥터는 실행하지 않았다.

지연은 단조 시계로 측정했다. 각 발화는 워커에 메시지를 제출한 직전부터 유효한 질문 또는 최종 결과 반환까지다. 전체 사례 시간에는 세션 준비와 필요한 로컬 합성 실행이 포함된다. 사용자의 답변 대기, 모델 사전 점검은 제외했다. 네이티브 Ollama의 로드 시간은 원본 응답의 관측값이고 SDK를 이용하는 Planner의 서버 내부 로드 시간은 미기록이다. 선별 3건 p50은 세 사례 전체 시간의 중앙값이다.

## 범용 Planner 후보를 좁힌 근거

실제 9B에서 세 건 모두 의미 검사를 통과했다. H01과 H21은 이미 표 도구 4개만 허용되는 경로여서 이 범위 밖의 설명과 추천을 제외했다. 반면 일반 워크플로우인 H32의 입력은 줄었지만 출력은 355토큰 늘었고 생성 단계가 20.69초 길어졌다. 따라서 이 후보의 일반 경로 수정은 채택 대상에서 제외했다. 다음 값은 서로 다른 업무 단계의 시간이 섞이지 않도록 Planner 단계만 집계한 것이다.

| 사례 | 입력 토큰, 기준 → 후보 | 출력 토큰, 기준 → 후보 | Planner 전체, 기준 → 후보 |
|---|---:|---:|---:|
| H01 | 9,974 → 7,323 | 633 → 541 | 76.71 → 50.52초 |
| H21 | 10,119 → 7,732 | 527 → 396 | 59.46 → 37.11초 |
| H32 | 13,619 → 12,345 | 473 → 828 | 49.66 → 70.35초 |

[범용 Planner 비교](../runtime/planner-capability-9b-experiment/planner-comparison.json)

## 재현

저장소 루트에서 잠금 파일로 준비한 워커를 사용한다. 아래 명령은 현재 체크아웃을 새로 측정한다. 과거 5개 후보의 측정값과 H21 추가 비교는 공개된 입력 파일로 다시 집계할 수 있다. [증거 공개 범위와 해시](orchestration-evidence.md)

```powershell
$root = (Resolve-Path '.').Path
$worker = (Resolve-Path 'Backend/.venv-agent/Scripts/python.exe').Path
$env:PYTHONUTF8 = '1'
$env:A4B_EVALUATION_REQUIRE_GPU = '1'
$results = Join-Path $root 'runtime/planner-capability-9b-experiment'
$variant = $root
$raw = Join-Path $results ('repeat-' + [guid]::NewGuid().ToString('N') + '.json')
Push-Location $variant
try {
  node scripts/verify/run-qwen-live-evaluation.mjs --python $worker --profile local-public-9b-deepagent `
    --allow-local-endpoint http://127.0.0.1:11434/v1 --phase reassessment --live `
    --corpus docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json --corpus-profile runpod-rebuild `
    --expected-gold-sha 93c821ded55570fda569ceb983f22fd2ebf8d6b8c7d395d445e68e4931bc7e35 `
    --case H01 --case H21 --case H32 --max-calls 44 --output $raw
} finally { Pop-Location }
& $worker scripts/verify/check-focused-qwen-outcomes.py $raw --case H01 --case H21 --case H32
& $worker scripts/verify/profile-qwen-stages.py $raw --case H21
```

[사전 판정 기준](../runtime/planner-capability-9b-experiment/protocol.md) · [동결 정답 판정기](../scripts/verify/check-focused-qwen-outcomes.py) · [단계별 집계기](../scripts/verify/profile-qwen-stages.py)

추가 H21 실측은 같은 사전 점검을 포함해 사례 선택을 `--case H21`로 바꾼다. 서로 다른 코드의 전후 비교 시 같은 하니스와 모델 설정을 사용한다. Fast 및 기록된 실행의 비교는 다음 명령을 사용한다.

```powershell
& $worker -m pytest Backend/tests/design Backend/tests/conditions Backend/tests/capabilities Backend/tests/infrastructure/test_model_migration.py scripts/verify/tests -q
& $worker "$results/compare.py" "$results/BASE.json" "$results/COMBINED.json"
& $worker "$results/compare.py" "$results/H21-FIRST-BASE.json" "$results/H21-FIRST-FINAL.json"
```
