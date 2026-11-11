# 오케스트레이션 일반화 수정과 검증

**H18 통합 판정 보류.** 당시 측정본은 뉴욕 현지 시각과 서머타임(DST)을 정답으로 사용했다. 이후 main의 PR #90이 예약 시간대를 서울로 고정했으므로, 이 보고서의 H18 통과 건수는 해당 과거 측정본에만 적용한다.

## H32 수정 후 단건 실측 · 2026-11-11

**H32가 첫 생성 초안에서 READY를 받았다.** 공개 Qwen3.5 9B로 H32만 실행했다. 사례 시간은 154.31초, 모델 응답은 8회(의미 추출·검토 4회, 지원 판단 2회, 요구사항 추출 1회, 초안 생성 1회)였다. 재질문·초안 재생성·메일 바인딩 사후 보정은 각각 0회였다. 테스트는 설계에서 종료했으며 파일 생성과 실제 메일 발송은 수행하지 않았다. [수정 후 H32 실측 원본](../runtime/generalization-focused/h32-compound-after.json)

| 실제 생성 그래프 | 확인한 값 |
|---|---|
| 문서와 요약 | `read_document`가 연결 자료 `synthetic-shift-guide`를 읽도록 설정됐고 `step_2`의 `compose_answer`가 그 문서 출력을 받는다. |
| Markdown 파일 | `step_3`의 `export_result`는 형식이 `markdown`이고 텍스트 입력은 `step_2.text`다. |
| Gmail | `step_4`의 `email.send/execute`는 수신자 `shift@example.invalid`, 제목 `야간 인계`를 상수로 유지한다. 유일한 바인딩은 `body ← step_2.text`다. |

생성기는 확정된 메일 업무와 선행 요약의 관계에서 실행 인자의 형식을 만든다. 특정 요청 문장이나 노드 번호에 의존하지 않는다. 검증기는 파일과 메일이 같은 실행 경로에서 **동일한 요약 노드의 텍스트 출력**을 직접 사용하는지 확인한다. 이전에 저장한 두 모델 응답도 수정 전에 재현하고 같은 원문으로 다시 검증했다. 두 경우 모두 이미 선택한 요약 출력을 메일 본문에만 연결한 뒤 Validator와 승인 상태를 가정한 저장 검사를 통과했다. 관련 회귀 테스트는 147건 모두 통과했다. [저장 응답 재생 근거](../runtime/generalization-focused/h32-binding-replay.json) · [회귀 테스트 결과](../runtime/generalization-focused/h32-binding-regression.xml)

기능별 검증을 누적하면 기존 실패 12건 중 실제 Qwen으로 확인한 6건, 서비스의 코드 전용 분기로 확인한 1건, 저장 응답 재생으로만 확인한 5건이 해결됐다. 이번 수정 후 재측정 범위는 H32 한 건이다. 이전 결과는 아래에 측정 순서대로 구분해 보존했다.

## H32 수정 전 단건 실측 · 2026-11-11

공개 Qwen3.5 9B로 H32만 추가 실행한 결과는 **실패**였다. 사례 처리 시간은 200.95초, 실제 모델 응답은 9회였다(별도 접속 점검 1회 제외). 질문은 0개였다. 요구사항 장부는 Markdown과 메일 본문이 모두 같은 요약을 쓰도록 보존했다. 최종 그래프에서 Markdown은 `step_2.text`를 올바르게 참조했지만, 발송 노드는 같은 출력을 수신자·제목·본문 세 입력 모두에 연결하고 해당 인자들에 상수도 지정했다. 본문 인자에는 문자열 `"$binding"`이 들어갔다. Validator가 입력 충돌을 차단했다. 이 후속 실측을 반영하면 선정했던 기존 실패 12건 중 11건 해결, 1건 미해결이다. 아래 표는 추가 실측 전의 검증 범위를 기록한다. 문서 요약과 파일 생성, 메일 발송을 포함한 워크플로우 실행은 0회다. [H32 실제 응답·최종 그래프·검증 결과](../runtime/generalization-focused/h32-focused.json)

## 작업 위치

이 파일이 있는 작업 트리의 루트에서 실행한다. `git rev-parse --show-toplevel`로 1차 리빌딩 통합본을 확인할 수 있다. 원래 사용하던 기본 `main` 작업 폴더와 이 리빌딩 브랜치는 별도로 등록돼 있다. 기존 로컬 Ollama 서버, Qwen, LangGraph, Condition/Requirement 장부, 기존 생성기·Validator·승인 및 저장 경로를 사용한다.

## 첫 선별 검증 결과

명시된 기존 실패 12건 중 **11건을 각 사례에 표시된 범위에서 해결했고, H32 한 건은 부분 검증**이다. 35개 회귀군의 나머지 실패 9건과 기존 52건의 실패 17건은 이번 개선의 해결 분모에 넣지 않았다.

| 확인 방법 | 중복 없는 사례 수 | 해결 | 부분 검증 | 원본에서 확인한 결과 |
|---|---:|---:|---:|---|
| 실제 Qwen 응답을 받은 서비스 평가 | 5 | 5 | 0 | H01 단일 CSV와 원본 1·2·4행, H18 뉴욕 금요일 17:20 예약, H20 날짜 유지와 조회 수량 2, H21 질문 한 번 후 CSV 3·4·5행, H24 수신자·제목·본문이 일치하는 단일 메일 발송 초안 |
| 실제 서비스의 코드 전용 분기 | 1 | 1 | 0 | H28은 실행 화면에서 독립 다운로드 링크 두 개를 제공할 수 없음을 설명하고 UNSUPPORTED. 사례 모델 호출 0회 |
| 저장된 응답 재생만 수행 | 6 | 5 | 1 | H13~H16 실제 Gmail 조회 인자와 동결된 날짜 경계 일치. H29 합성 CSV 2·3행 일치. H32는 질문 없이 요약의 두 소비자까지 복원했고 생성 초안은 원래 기록되지 않음 |
| 전체 | 12 | 11 | 1 | 서로 다른 시점의 기능별 고정 빌드에서 얻은 선별 검증 결과 |

**정상 사례**는 1차 릴리스에서 해결되어 있던 R02·R04·R05 세 건을 재생하여 **3/3 유지, 관측 회귀 0건**이었다. 최종 소스의 설계·조건·지원 판단 및 평가 도구 테스트는 **2,352건 통과, 실패·오류·건너뜀 0건**이었다. [통합 테스트 결과](../runtime/generalization-focused/core-final.xml)

**전체 평가 미실시 범위**는 기존 52건 및 35건의 전체 성공률·회귀 건수·p50/p95와 새 독립 holdout 30건의 일반화 성능이다. 외부 메일·실계정·실제 예약 발송은 실행하지 않았다. H18은 예약과 파일 연결을 검증했다. H24는 설계 단계의 단일 발송 노드와 실제 인자를 검사했다.

### 원본 근거

| 사례 | 사용한 고정 실행 또는 재생 | 판정 세부사항 |
|---|---|---|
| H01·H18·H28 | [선별 실행 원본](../runtime/generalization-focused/identity-focused.json) | H01 합성 행 전체 대조. H18 자료 읽기→CSV 연결 및 DST를 따르는 주간 예약 확인. H28에는 모델 응답이 없음. |
| H21 | [답변 선별 실행에서 H21](../runtime/generalization-focused/answers-focused.json) | 실제 질문 키 duplicate.policy, 같은 ID로 답변 제출, 원본 seq 3·4·5. 이 파일 안의 H20·H24는 중간 실패로 보존했다. |
| H20 | [최종 수량 수정 실측](../runtime/generalization-focused/h20-final.json) | 두 턴의 `email.list` 구간이 동일하고 한도가 100→2가 됐다. 두 번 모두 요약 노드는 조회 결과에 직접 연결됐다. |
| H24 | [최종 수신자 실측](../runtime/generalization-focused/h24-send-focused.json) | 기존 제목·본문과 사용자가 답한 수신자를 가진 `email.send/execute` 노드 하나, 질문 한 번, 최종 READY. |
| H13~H16·H29·H32 | 아래 Fast 재생 명령 및 [최초 오류·오라클](orchestration-generalization-failure-analysis.md) | H32는 실제 Qwen 생성 전체 성공을 확인하지 못했다. |

## 수정한 경로

| 책임 | 기존 경로와 변경 내용 |
|---|---|
| 질문·답변·확정값 | `capability_check.py`의 질문 생성·재개가 선택한 ID와 소유 슬롯을 기존 이벤트에 함께 기록한다. `condition_readiness.py`는 선택한 보존 정책 답변만 원래 사용자 턴의 근거로 복원한다. 중복 제거 응답의 선택 항목에 null 필터가 포함돼도 새 작업을 만들지 않는다. 잘못된 ID·복합 질문·명시적 변경은 별도로 검사한다. |
| Gmail 수량·수신자 | `request_meaning.py`, `gmail_summary.py`, `requirement_ledger.py`는 이전에 검토한 의미와 사용자 턴 지문을 대조한다. 단일 수량 수정이 확실하면 최초 기간을 유지하고, 최종 조회 노드의 max_results를 검사한다. 수신자가 확정된 단일 문자 본문 발송은 기존 Requirement 스키마에 투영한다. `requirement_generation.py`가 실제 변수와 도구 계약을 전달하며 `requirement_coverage.py`에서 발송 노드의 수와 실제 인자를 검사한다. |
| 업무 동일성과 지원 한도 | `requirement_task_identity.py`와 `condition_requirements.py`에서 동일 표·근거 턴·수치 조건 또는 예약·최종 출력이 같은지 확인한다. 설명에서 추가 동작이 남거나 출력 자료·폴더·포맷이 달라지면 보존한다. `capability_judgement.py`가 실행기의 1회 1개 다운로드 한도를 반영한다. 이후 요구가 취소되거나 한 형식으로 수정되면 과거 복수 링크 요구로 조기 종료하지 않는다. |
| 순서·복수 결과 | `condition_check.py`의 기존 감사 단계에서 단일 표의 filter_sort 내부 순서가 명시한 처리 순서와 다를 때 필터 결과를 중복 제거 입력에 연결한다. `condition_readiness.py`와 Requirement 검증은 실제 선행 관계를 검사한다. `content_lineage.py`와 `requirement_coverage.py`는 Markdown과 email.send의 본문이 같은 요약 결과를 소비하고 실행 경로에서 도달 가능한지 확인한다. |
| 현지 날짜 | `temporal_context.py`와 `gmail_summary.py`가 각 발화의 기존 시계에서 현지 자정 두 개를 계산한다. 한국어 도시명 검증에는 Babel의 CLDR 자료를 사용한다. 브라우저 설정과 명시한 도시에 대한 증거가 충돌하면 틀린 날짜 인자를 승인하지 않는다. 실제 이메일·외부 커넥터는 실행하지 않았다. |

## 정상·반대 사례

저장된 성공 사례 R02·R04·R05는 각각 실제 추출 응답과 생성 초안을 재생했다. 세 건 모두 Validator와 승인 후 저장 게이트를 통과했고, 로컬 합성 출력 2·2·3행이 각 동결 정답과 순서까지 같았다. 같은 표의 다른 수치 경계·다른 시간대·별도 발송은 별개 업무로 남겼다. 정책을 바꾸는 사용자 입력, 잘못된 질문 ID, 결과 링크 취소, 다른 도시의 Gmail 경계, 추가 사용자 승인이 필요한 발송을 반대 사례로 확인했다. H32의 수정 전 저장 원본에는 생성 초안이 하나도 없었다. 별도로 구성한 정상 그래프에서 같은 요약의 파일·메일 연결을 검증했지만 H32의 실측 성공 건수에는 넣지 않았다.

## 재현 명령

이 Windows 작업 공간은 기존 Python 환경을 두 작업 트리에서 공유한다. 독립 체크아웃에 환경이 설치돼 있으면 첫 줄의 경로만 `.\Backend\.venv-agent\Scripts\python.exe`로 바꾼다.

```powershell
$py = (Resolve-Path 'Backend/.venv-agent/Scripts/python.exe').Path
$outDir = Join-Path (Resolve-Path 'runtime') ('generalization-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $outDir | Out-Null
$env:PYTHONUTF8 = '1'
$env:A4B_EVALUATION_REQUIRE_GPU = '1'
```

Babel 2.17.0은 한국어·영어 도시명과 IANA 시간대를 대조하는 데 사용한다. 새 워커에는 기존 설치 절차로 `Backend/requirements/agent-lock.txt`를 적용한다. 복제한 환경에서는 아래 명령으로 워커에 설치한 버전을 확인한다.

```powershell
& $py -c "import babel; print(babel.__version__)"
```

### Fast

다음 테스트는 실제 모델에 접속하지 않는다. 저장된 모델 후보에 대응하는 단위 테스트와 음성 대조군을 먼저 실행한다. 이어서 기존 설계·조건·지원 판단을 전부 재검사할 수 있다.

```powershell
& $py -m pytest Backend/tests/design/test_capability_slot_handoff.py Backend/tests/design/test_projected_table_effects.py Backend/tests/capabilities/test_download_publication.py Backend/tests/design/test_ordered_table_conditions.py Backend/tests/design/test_summary_consumers.py Backend/tests/design/test_request_meaning.py -q --basetemp "$outDir/fast"
& $py -m pytest Backend/tests/design Backend/tests/conditions Backend/tests/capabilities -q --basetemp "$outDir/core" --junitxml "$outDir/core.xml"
```

### Focused Live

서버와 평가 별칭을 준비한 후 핵심 사례만 선택한다. 기존 성공 사례와 새 독립 평가의 실측은 이번 선별 범위에서 제외한다. 실행기는 합성 자료를 사용하고 외부 커넥터를 실행하지 않는다.

```powershell
& $py scripts/verify/run-local-orchestration-reassessment.py --prepare-only --output-dir $outDir
$common = @('scripts/verify/run-qwen-live-evaluation.mjs', '--python', $py,
  '--profile', 'local-public-9b-deepagent', '--allow-local-endpoint', 'http://127.0.0.1:11434/v1',
  '--phase', 'reassessment', '--live', '--corpus-profile', 'runpod-rebuild',
  '--corpus', 'docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json',
  '--expected-gold-sha', '93c821ded55570fda569ceb983f22fd2ebf8d6b8c7d395d445e68e4931bc7e35')
$ids = 'H20','H21','H24'
$selection = foreach ($caseId in $ids) { '--case'; $caseId }
node @common --max-calls 180 @selection --output "$outDir/focused.json"
& $py scripts/verify/profile-qwen-stages.py "$outDir/focused.json"
```

먼저 실측한 업무 동일성 묶음은 `$ids = 'H01','H18','H28'`이다. H28은 실제 서비스가 코드만으로 처리한 사례다. 성능 전후 비교 시 과거 `independent35.complete.json`에 같은 `@selection`을 전달한다. 이전에 사용한 출력 파일을 재사용하지 않는다. 종료 코드와 함께 결과 JSON의 `status`, 완료 사례 ID, 실제 수신한 응답과 GPU 점유를 확인한다. 시스템 오류와 실패한 Validator도 결과에 포함한다.

### Full

릴리스 후보의 기존 52건 및 종전 독립 35건을 측정하는 저장소 기본 명령이다. 두 코호트 모두 이번 개선의 회귀군이다. 새 holdout은 별도 코퍼스·별도 사전 동결 해시·별도 결과 파일을 사용한다. 기존 35건의 판정 라벨을 새 trace로 복사하지 않는다.

```powershell
& $py scripts/verify/run-local-orchestration-reassessment.py --output-dir "$outDir/full"
```

채점은 각 trace의 원문, 실제 인자 및 연결, 승인된 범위의 합성 실행 결과를 동결 기준과 대조한다. 기존 채점기에는 `--run`, `--corpus`, 새로 작성한 `--labels`, `--ledger-labels`, `--output`을 전달한다. 흐름의 종료 상태가 READY여도 시간대·수량·순서 등 의미 요구가 틀리면 실패다.


### 저장 원본 재생

세 정상 사례는 1차 릴리스에서 성공한 R02·R04·R05다. 원본의 Requirement 응답과 생성 초안을 그대로 사용하며 격리된 자료 저장소에서 판정한다. H01·H18·H29는 동일한 인터페이스로 실패 당시의 첫 초안과 재생성 초안을 각각 통과시킨다. 합성 실행이 지정된 사례만 실제 행과 순서를 검사한다.

```powershell
$baseline = (Resolve-Path 'runtime/generalization-focused/replay-fixtures').Path
& $py scripts/verify/replay-table-requirements.py --run "$baseline/legacy52.selected.json" --corpus docs/qwen-live-evaluation-cases.json --case R02 --case R04 --case R05 --scratch "$outDir/normal"
& $py scripts/verify/replay-table-requirements.py --run "$baseline/independent35.selected.json" --corpus docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json --case H01 --case H18 --case H29 --scratch "$outDir/failed"
```

달력 및 복수 산출물 재생은 다음 명령을 사용한다. H13~H16은 실제 최종 조회 인자와 저장 직전 검사까지 점검한다. H32는 보존된 생성 초안이 없으므로 업무 장부와 질문까지만 재생한다.

```powershell
& $py scripts/verify/replay-calendar-deliveries.py --trace "$baseline/independent35.selected.json" --corpus docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json
```

### 사전 동결해 둔 다음 독립 평가

30개 새 사례와 정답, 단계 의존성, 합성 행, 날짜 경계, 문항별 판정 기준을 `docs/orchestration-generalization-holdout`에 동결했다. 이번 작업에서는 이 코퍼스로 모델 호출을 하지 않았다. 향후 Full 실행 명령은 아래와 같다.

```powershell
node scripts/verify/run-qwen-live-evaluation.mjs --python $py --profile local-public-9b-deepagent --allow-local-endpoint http://127.0.0.1:11434/v1 --phase reassessment --live --corpus-profile runpod-rebuild --corpus docs/orchestration-generalization-holdout/cases.json --expected-gold-sha b861a55a0ae1b6676c68ce71d3ee9a987e661fe11bae749291ac0e266cc119d1 --max-calls 600 --output "$outDir/new-holdout.json"
```
