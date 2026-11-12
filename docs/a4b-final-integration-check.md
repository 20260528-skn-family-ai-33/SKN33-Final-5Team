# A4B 최종 통합 검증

**H18 통합 판정 보류.** 당시 측정본은 뉴욕 현지 시각과 서머타임(DST)을 정답으로 사용했다. 이후 main의 PR #90이 예약 시간대를 서울로 고정했으므로, 이 보고서의 H18 통과 건수는 해당 과거 측정본에만 적용한다.

## 최종 9B 실측과 회귀

같은 최종 코드에서 H01·H20·H21·H32 **4/4**가 동결 정답을 만족했다. 변경 전 같은 네 사례의 실측은 3/4였고 H21이 재질문으로 실패했다. 최종 H21은 정책을 한 번 묻고 같은 질문 ID로 답변을 수용했으며 합성 CSV에서 seq 3·4·5가 남았다. H32는 요약 하나에 Markdown과 메일 본문을 직접 연결했다. 실제 메일은 보내지 않았다. 관련 정상 3건과 기존 실패 12건의 저장 응답·코드 경로 검증도 유지됐고, 채택본 회귀 테스트는 2,413건 모두 통과했다. 전체 코퍼스 성공률은 미측정이다. [최종 네 사례 원본](../runtime/final-qwen-performance/verified-9b-only.json) · [판정·요구사항 확인](../runtime/final-qwen-performance/verified-comparison.json) · [회귀 테스트](../runtime/final-qwen-performance/regression-verified.xml)

## 검증 기준

**최종 후보 12개 대상은 같은 249개 앱 파일에서 각 오프라인 판정 기준을 모두 통과했다.** 최종 SHA-256은 `c99d88424a4e1baa113eb531b34a3ab75adfd0a6f1ec182b151845d0b8312416`이다. H01·H18·H20·H21·H24·H28·H32는 동결 최종 코드로 서비스 세션 전체를 재생했고, 나머지 H13~H16·H29는 각 실제 단계의 녹화 응답·도구 인자·저장 검사와 지정된 합성 행을 검증했다. 최종 후보는 실행 환경이 명시적으로 지정한 로컬 모델 별칭의 재매핑도 해시에 반영한다. 별칭이 다른 모델로 바뀐 사례의 실패를 수정 전에 재현했고 수정 후 관련 42개 테스트가 모두 통과했다. [별칭 변경 회귀](../runtime/final-qwen-performance/cache-alias-final.xml) [최종 코드 식별](../runtime/a4b-final-integration-20261111/verified-manifest.jsonl) · [최종 표·순서](../runtime/a4b-final-integration-20261111/verified-tables.jsonl) · [최종 날짜](../runtime/a4b-final-integration-20261111/verified-calendar.jsonl)

최종 후보에서 H01의 3개 행과 H21의 3개 행은 승인 후 실제 합성 실행으로 확인했다. H18·H24는 실제 예약 및 메일 전송 없이 설계를 비교했다. H28은 모델 응답을 전혀 소비하지 않았다. H20은 2개 응답과 두 턴, H32는 기존 8개 중 4개 응답만 소비해 같은 최종 그래프를 보존했다. 정상 R02·R04·R05도 다시 승인·저장·합성 실행을 통과했다. 새 H21 실패의 실제 원문을 사용한 별도 재생도 duplicate.policy 질문 하나, 다음 턴 READY와 기대 CSV를 확인했다. [H01·H18·H28](../runtime/a4b-final-integration-20261111/verified-identity.jsonl) · [H20](../runtime/a4b-final-integration-20261111/verified-H20.jsonl) · [H21](../runtime/a4b-final-integration-20261111/verified-H21.jsonl) · [H21 새 표현](../runtime/a4b-final-integration-20261111/verified-H21-prefix.jsonl) · [H24](../runtime/a4b-final-integration-20261111/verified-H24.jsonl) · [H32 서비스](../runtime/a4b-final-integration-20261111/verified-H32-session.jsonl) · [H32 저장 검사](../runtime/a4b-final-integration-20261111/verified-h32.jsonl) · [정상 3건](../runtime/a4b-final-integration-20261111/verified-normal.jsonl)

1차 구현이 통합된 작업 트리 `output/pr-orchestration-ledgers-20261111`의 브랜치는 `codex/orchestration-ledger-ownership`, 기준 커밋은 `c932680f`이다. 이번 최적화 직전 앱 manifest `d251614121aff1892bd29d3ddff2bbf3e30148d08c15079a0aa314fc02f85df8`의 249개 파일은 직전 H32 성공 실측의 파일별 SHA-256과 모두 일치했다. 기본 `main`의 일부 미추적 모듈만으로 동일성을 판단하지 않았다. 기존 앱 변경과 미추적 평가 자료는 보존했다. [1차 구현](local-public9b-verification/implementation-report.md) · [잔존 오류](orchestration-generalization-failure-analysis.md) · [기능별 수정](orchestration-generalization-fix-report.md) · [종전 성능](orchestration-performance-report.md) · [H32 실측 원본](../runtime/generalization-focused/h32-compound-after.json)

12개 사례를 같은 앱의 오프라인 코드 경로에 통과시켰다. 기록에는 두 종류가 있다. **원본 단계 재생**은 실제 저장된 Qwen 응답과 초안을 현재 소유권·검증 함수로 처리한다. **서비스 세션 재생**은 합성 자료를 임시 SQLite에 넣고, 모델 응답을 턴·그래프 단계·호출 방식별로 주입한다. 실제 worker와 LangGraph가 질문을 생성하고 응답을 같은 질문 ID에 연결하며 체크포인트를 재개한다. 네트워크는 차단했다. 성공 원본의 사용자 턴·표시 질문·워크플로우와 대조하고 안전한 표 변환에만 승인·저장·합성 출력 검사를 수행했다. 어느 방식도 변경 후 Qwen이 같은 답변을 생성할 확률을 측정하지 않는다.

### 압축 실험과 채택한 소스

중간 후보 `b34a54d9f848…`는 Capability 단계의 표 도구 설명을 축약했다. 기존 녹화 응답을 주입하면 통과했지만 실제 9B H21은 원래 필요했던 중복 보존 정책 외에 파일 이름·경로, 오류 표시 값, 인코딩·구분자를 추가로 물었고 두 번째 턴도 CLARIFY로 끝났다. 이 결과는 과거 녹화 자료의 성공이 새로운 모델 응답의 정확도를 보증하지 않음을 보여준다. 모델 없는 Condition 후보에 deduplicate만 넣으면 원문에 독립 파일명·사전 승인·메일 발송·다른 인코딩을 추가한 반례도 동일한 미정 슬롯 한 개를 반환했다. 그러므로 실측 실패를 숨기려고 나머지 질문을 일괄 제거하지 않았다. [중간 후보 실측](../runtime/final-qwen-performance/after-four-9b.json)

최종 후보 `c99d88424a4e…`는 이 Capability 카탈로그 축약을 철회하고 기존 도구 설명 형식을 사용했다. 동일한 12개 통합 대상을 이 최종 후보에서 다시 오프라인으로 판정했다. 후보별 실제 Qwen 판정·호출·속도는 별도의 같은 빌드 실측으로 확인한다. [최적화 실측 보고서](a4b-qwen-optimization-report.md)

## 사례별 통합 상태

| 사례 | 유지해야 할 계약 | 최적화 전 동일 소스에서 확인한 내용 |
|---|---|---|
| H01 | 같은 표·수치 경계·단일 CSV의 반복 제안 병합 | `project_owned`, `restates_table_export`로 당시 두 초안이 모두 Validator·승인 후 저장 검사를 통과했다. 각 합성 CSV의 원본 행 1·2·4와 사용자 지정 순서가 일치했다. |
| H18 | 예약과 CSV가 각각 한 번 유지 | 같은 조건 투영과 예약 근거 검사로 두 초안 모두 승인 검사 통과. 성공 세션에서 단일 `America/New_York` 주간 금요일 17:20과 단일 CSV도 확인했다. 실제 예약은 실행하지 않았다. |
| H20 | 처음 확정한 현지 날짜를 유지하고 수량 변경 | `selected_day`, `_apply_reviewed_count_edit`, `apply_gmail_count` 경로. 두 턴의 `email.list` 구간이 `[1830153600,1830240000)`로 같고 수량이 100→2다. 두 턴 모두 READY와 승인 후 저장 검사 통과. |
| H21 | 중복 정책 질문 ID에 답한 last를 보존 | `_question_key`, `await_capability_node`, `validate_conditions`. 성공 세션의 녹화 응답 5개로 유일한 duplicate.policy 질문과 같은 소유자 ID에 답변, CLARIFY→READY. 합성 CSV 원본 3·4·5행 일치. 아래에 새 모델 출력의 추가 실패와 수정도 기록했다. |
| H24 | 수신자를 한 번 확인하고 확정 제목·본문을 보존 | `answered_mail_spec`, `constrain_request_settings`, `coverage_report` 경로. 녹화 응답 4개로 질문 ID를 거쳐 단일 `email.send`에 night-shift@example.invalid, ‘저장 작업’, ‘삭제라는 단어는 예시다.’가 연결됐다. 발송 0회. |
| H28 | 독립 다운로드 두 개는 현재 단일 공개 링크 계약을 벗어남 | `_download_publication_gap`이 한 번의 실제 서비스 분기에서 두 요청 형식을 담은 동일한 UNSUPPORTED 결과를 반환했다. 녹화 응답 소비 0개. |
| H13~H16 | 요청 턴의 현지 두 자정과 Gmail의 실제 조회 경계 일치 | `source_calendar_zone`, `selected_day`, Gmail 감사·저장 경로. 카트만두 24시간, 베를린 23·25시간, 서울 윤일 24시간의 양 끝 epoch가 동결 기준과 일치했다. 네 건 모두 불필요한 unread 조건 없이 READY·승인 후 저장 검사 통과. |
| H29 | 선별된 행을 중복 제거에 전달 | `ordered_row_steps`, `_separate_ordered_steps`, `plan_issues`와 Requirement 검증. 당시 첫 초안과 재생성 초안이 각각 합성 CSV에서 원본 2·3행을 반환했다. |
| H32 | 한 번의 요약에서 Markdown과 메일 본문으로 같은 텍스트 전달 | `_constrain_email_send`, `bind_confirmed_summary`, `coverage_report`. 녹화 생성 원문이 이번 decoder를 통과했고 생성 그래프가 당시 성공 그래프와 동일했다. 두 소비자가 `step_2.text`를 참조하고 지정 수신자·제목이 유지됐다. 질문 0개, READY·승인 상태를 가정한 저장 검사 통과, 파일 생성·발송 0회. |

관련 정상 사례 R02·R04·R05의 성공 응답과 초안을 재생했다. 세 건 모두 READY와 저장 검사를 통과했고 정답과 비교한 합성 결과는 각각 2·2·3행이었다. **관측된 세 정상 사례의 회귀는 0건**이다. 11개 관련 테스트 파일의 최적화 전 Fast 결과는 **228개 모두 통과**였다. 다른 수신자·숨은 참조 추가·취소, 다른 요약 생산자, 순서 반전, H20 날짜 변경·실제 추가 조건 등이 반대 사례에 포함됐다. [통합 Fast 결과](../runtime/a4b-final-integration-20261111/pre-fast.xml) · [표·순서](../runtime/a4b-final-integration-20261111/pre-tables.jsonl) · [현지 날짜·수량](../runtime/a4b-final-integration-20261111/pre-calendar.jsonl) · [요약 연결](../runtime/a4b-final-integration-20261111/pre-h32.jsonl) · [정상 사례](../runtime/a4b-final-integration-20261111/pre-normal.jsonl)

고정한 최적화 전 스냅샷에서 H01·H18·H20·H21·H24·H28·H32는 서비스 전체 세션으로도 재생해 각 턴의 질문 슬롯, 최종 그래프·예약이 성공 원본과 일치하는지 검산했다. H32는 이 스냅샷에서 기록된 응답 8개를 모두 소비했다. [H01·H18·H28](../runtime/a4b-final-integration-20261111/pre-frozen-identity.jsonl) · [H20](../runtime/a4b-final-integration-20261111/pre-frozen-H20.jsonl) · [H21](../runtime/a4b-final-integration-20261111/pre-frozen-H21.jsonl) · [H24](../runtime/a4b-final-integration-20261111/pre-frozen-H24.jsonl) · [H32](../runtime/a4b-final-integration-20261111/pre-frozen-H32-session.jsonl)

### H21의 새 표현

동일 가중치로 다시 실행한 직전 기준 측정에서 H21이 재차 CLARIFY에 머물렀다. 첫 질문에는 등록된 보존 정책 선택지 네 개가 모두 있었지만 계층이 범용 키 `capability.condition.0`을 부여했다. 다음 `missing_conditions` 항목은 원문을 인용한 직렬화 필드와 설명이었고 별도 사용자 질문으로 표시됐다. 이전 성공 원문의 양호한 재생 결과와 이번 실제 실패는 함께 보존했다. [새 실측 원본](../runtime/final-qwen-performance/before-four.json)

`capability_check.py`의 표시 질문에서 단일 중복 정책 질의·실제 자료 열·정식 선택지 전체가 확인되면 기존 슬롯과 옵션을 연결했다. 자료 인용이 검증되는 직렬화 필드만 사용자 질문에서 제외하고 원본 위치를 진단에 기록한다. 사용자에게 남은 별도 질문과 복합·취소·최대 행 수 질문은 유지한다. 새 실패에서 실제 얻은 앞 세 응답과 과거 성공 기록의 미도달 후반 두 응답을 조합한 세션 재생 결과는 첫 질문 duplicate.policy 1개, 답변 뒤 READY와 합성 CSV 3·4·5행 일치였다. 이 조합은 실측 사례 하나로 집계하지 않는다. [새 실패 응답 재생](../runtime/a4b-final-integration-20261111/h21-recovered-prefix.jsonl) · [인접 회귀](../runtime/a4b-final-integration-20261111/h21-capability.xml)

## 재현 명령

### 체크포인트 재사용 경계

수정 전에는 같은 사용자 문구에서 모델 ID나 응답 소유 질문이 달라도 기존 Meaning 승인이 재사용됐고, Requirement도 모델·소유 질문·기준 시각 변경만으로는 이전 plan을 다시 사용했다. 기존 Capability의 보류 응답 분기는 자료가 바뀌어 이전 fingerprint가 더 이상 맞지 않아도 지난 질문을 재사용했다. 신규 테스트로 모델 별칭·질문 ID·슬롯 키·모델·시간대·날짜 anchor 변경과 이전 턴 재구성을 확인했고, 기존 테스트로 자료·도구 카탈로그·확정 조건 장부의 유효성 검사를 유지했다. 이전 턴 해시 계산은 그 시점까지의 답변·anchor만 소비하고, 보류 응답의 기존 자료·상태가 달라지면 지원 판단을 재수행한다. H20의 수량만 바꾸는 답변은 이전 날짜 근거를 유지했다. [변경 전 실패 테스트](../runtime/final-qwen-performance/cache-before.xml) · [수정 후 인접 회귀](../runtime/final-qwen-performance/cache-final.xml)

### 실행 방법

아래 PowerShell은 저장소 루트에서 실행한다. `Backend/requirements/agent-lock.txt`로 준비한 워커 Python을 지정한다. 공개된 케이스별 체크포인트는 실측 당시의 동결 정답을 사용한다. [공개 증거 범위](orchestration-evidence.md)

```powershell
$py = (Resolve-Path 'Backend/.venv-agent/Scripts/python.exe').Path
$env:PYTHONUTF8 = '1'
$env:A4B_EVALUATION_REQUIRE_GPU = '1'
$outDir = Join-Path (Resolve-Path runtime) ('verify-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $outDir | Out-Null
$env:TMP = $outDir
$env:TEMP = $outDir
$legacy = 'runtime/generalization-focused/replay-fixtures'
$corpus = 'docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json'
$recorded = 'runtime/generalization-focused'
```

**Fast**는 모델을 호출하지 않는다. 첫 명령은 당시 통합 검증 11개 파일을 지정한다. 최종 최적화에서 추가한 테스트는 최종 결과에 적은 범위로 함께 수행한다.

```powershell
$fast = 'test_capability_slot_handoff','test_projected_table_effects','test_ordered_table_conditions','test_summary_consumers','test_summary_delivery_lineage','test_request_meaning','test_requirement_coverage','test_deep_agent_conversation','test_export_content_path','test_operation_steps' | ForEach-Object { "Backend/tests/design/$_.py" }
& $py -m pytest @fast Backend/tests/capabilities/test_download_publication.py -q --basetemp "$outDir/fast" --junitxml "$outDir/fast.xml"
& $py scripts/verify/replay-table-requirements.py --run "$legacy/independent35.selected.json" --corpus $corpus --case H01 --case H18 --case H29 --scratch "$outDir/tables"
& $py scripts/verify/replay-table-requirements.py --run "$legacy/legacy52.selected.json" --corpus docs/qwen-live-evaluation-cases.json --case R02 --case R04 --case R05 --scratch "$outDir/normal"
& $py scripts/verify/replay-calendar-deliveries.py --trace "$legacy/independent35.selected.json" --corpus $corpus --ids H13 H14 H15 H16 H20
& $py scripts/verify/replay-calendar-deliveries.py --trace "$recorded/h32-compound-after.json" --corpus $corpus --ids H32
& $py scripts/verify/replay-qwen-checkpoints.py --run "$recorded/answers-focused.json" --corpus $corpus --case H21 --scratch "$outDir/sessions"
& $py scripts/verify/replay-qwen-checkpoints.py --run "$recorded/h24-send-focused.json" --corpus $corpus --case H24 --scratch "$outDir/sessions"
& $py scripts/verify/replay-qwen-checkpoints.py --run "$recorded/identity-focused.json" --corpus $corpus --case H28 --scratch "$outDir/sessions"
& $py scripts/verify/replay-qwen-checkpoints.py --run "$recorded/answers-focused.json" --response-prefix runtime/final-qwen-performance/before-four.json --corpus $corpus --case H21 --scratch "$outDir/sessions"
```

**Focused Live**는 H01의 표 생성, H20의 날짜·수량 재사용, H21의 질문 재개, H32의 복합 생성으로 변경이 의미·실제 도구 인자에 미치는 영향을 확인한다. 종료 후 사례별 모델 응답, 생성·재생성, 최종 그래프, 동결된 행·날짜·수신자·본문을 직접 대조한다. H24도 주소 확정 회귀 대상이며 위 Fast 명령에서 같은 ID의 후속 답변과 실제 발송 노드 구성을 재생한다.

```powershell
& $py scripts/verify/run-local-orchestration-reassessment.py --prepare-only --output-dir $outDir
$live = @('scripts/verify/run-qwen-live-evaluation.mjs','--python',$py,'--profile','local-public-9b-deepagent','--allow-local-endpoint','http://127.0.0.1:11434/v1','--phase','reassessment','--live')
$gold35 = '93c821ded55570fda569ceb983f22fd2ebf8d6b8c7d395d445e68e4931bc7e35'
node @live --corpus $corpus --corpus-profile runpod-rebuild --expected-gold-sha $gold35 --case H01 --case H20 --case H21 --case H32 --max-calls 100 --output "$outDir/focused.json"
```

**Full**은 릴리스 후보에서 별도로 실행할 명령이다. 이번 개발에서는 52·35·30 전체를 실행하지 않았다. 새 holdout의 데이터는 개발용으로 읽거나 조정하지 않았다. 서로 다른 결과 파일을 사용한다.

```powershell
node @live --corpus docs/qwen-live-evaluation-cases.json --corpus-profile original-52 --max-calls 600 --output "$outDir/legacy52.json"
node @live --corpus $corpus --corpus-profile runpod-rebuild --expected-gold-sha $gold35 --max-calls 600 --output "$outDir/regression35.json"
node @live --corpus docs/orchestration-generalization-holdout/cases.json --corpus-profile runpod-rebuild --expected-gold-sha b861a55a0ae1b6676c68ce71d3ee9a987e661fe11bae749291ac0e266cc119d1 --max-calls 600 --output "$outDir/holdout30.json"
```

전체 52·35건의 성공률·p50/p95, 새 독립 30건의 일반화, 실제 Gmail 발송·외부 연결·예약 게시 성능은 이번 범위에서 미측정이다. 소수 Focused Live와 저장 응답 재생을 같은 정확도 분모나 전체 응답 지연 분포로 합산하지 않는다.
