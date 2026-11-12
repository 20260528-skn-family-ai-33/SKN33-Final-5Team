# A4B Qwen 추론 최적화 결과

## 채택본 Focused Live 결과

**동일한 네 입력에서 변경 전 3/4, 채택본 4/4가 동결된 의미·인자·의존성 판정을 통과했다.** H01·H21은 실제 합성 CSV 행과 순서까지 일치했다. H20의 두 턴은 같은 LA 현지 날짜 구간을 사용하고 최종 조회가 두 통으로 줄었다. H32의 파일과 메일 본문은 같은 요약 텍스트 포트를 직접 참조하며 수신자와 제목이 정확했다. 외부 발송은 0회다. [변경 전 실측](../runtime/final-qwen-performance/before-four.json) · [채택본 실측](../runtime/final-qwen-performance/verified-9b-only.json) · [해시·사례별 오라클·비교 수치](../runtime/final-qwen-performance/verified-comparison.json)

| 사례 | 의미 판정 전→후 | 모델 응답 전→후 | 입력 토큰 전→후 | 출력 토큰 전→후 | 사례 전체 전→후 |
|---|---|---:|---:|---:|---:|
| H01 | 통과 → 통과 | 4 → 4 | 28,806 → 28,806 | 979 → 979 | 97.11초 → 108.82초 |
| H20 | 통과 → 통과 | 2 → 2 | 4,540 → 4,540 | 174 → 174 | 26.95초 → 28.82초 |
| H21 | 재질문 실패 → 통과 | 3 → 5 | 27,149 → 40,465 | 495 → 1,061 | 48.64초 → 111.74초 |
| H32 | 통과 → 통과 | 8 → 4 | 47,178 → 37,758 | 1,709 → 1,005 | 140.95초 → 114.21초 |

네 사례의 합계는 모델 응답 17→15회, 입력 107,673→111,569토큰, 출력 3,357→3,219토큰이다. 호출 벽시계 합계는 304.82→347.96초, 사례 전체 합계는 313.64→363.59초다. 선별 p50은 72.87→110.28초, p95는 140.95→114.21초였다. 선별 p50은 30초 목표를 넘었다. H21이 재질문에서 멈추던 상태에서 초안 생성·승인·합성 실행까지 도달한 만큼, 총 지연과 총 입력이 줄었다고 보고할 수 없다.

양쪽에서 모두 성공한 H01·H20·H32만 따로 보면 모델 응답 14→10회, 입력 80,524→71,104토큰, 사례 시간 합계 265.01→251.85초였다. 이 하위 집계도 3건의 단일 관찰이다. 전체 52·35·새 30개 성공률과 사용자 전체 p50은 미측정이다.

## 실험 설계

작업 트리는 `output/pr-orchestration-ledgers-20261111`이다. 미커밋 H32 수정까지 포함한 변경 전 앱 manifest는 `d251614121aff1892bd29d3ddff2bbf3e30148d08c15079a0aa314fc02f85df8`이었다. 보고서 작성 시점의 통합 상태는 [통합 검증](a4b-final-integration-check.md)에, 전체 단계·토큰·워커 CPU와 계측 비용은 [성능 분석](a4b-qwen-performance-analysis.md)에 기록했다.

실측은 같은 공개 Qwen3.5 9B, Ollama 0.40.16, RTX 4060 Ti, 동일 양자화·가중치·32K 문맥·출력 4096·temperature 0·seed 42 설정을 사용한다. 측정기의 앱 외 변경을 양쪽에 동일하게 반영했다. 접속 점검 호출과 사례 호출을 따로 셌다. H01은 명시한 CSV의 행·순서, H20은 수량 수정 후에도 남는 원래 날짜, H21은 실제 질문 ID와 마지막 중복 행, H32는 두 산출물이 같은 요약을 참조하는지를 확인한다. 승인·합성 실행은 H01/H21의 동결 정책에서만 허용된다. H20/H32는 외부 조회·메일 발송 없이 설계에서 종료한다.

## 첫 번째 변경

`make_meaning_node`에서 이미 전체 요청을 증명하는 기존 Requirement 함수 `summary_delivery_spec`을 재사용했다. 요청에 명시한 문서·요약·저장·수신자·제목·본문 관계가 모두 확인되고 Requirement를 강제 검증할 때만 선택적 의미 해석과 그 검토를 생략한다. 이 경로에서는 가짜 모델 검토 결과를 생성하지 않는다. 실제 Capability, Requirement, 생성기, Validator, 승인·저장 검사는 그대로 진행한다. 원래 기능의 소유권 판정 함수에 새 문구나 사례별 상수를 추가하지 않았다. 재검사 대상은 사용자의 현재 발화와 연결 자료다. 다른 메일 조회, 추가 승인, 수정된 범위가 있으면 기존 판단을 수행한다.

변경 전 실패 테스트 1건과 일반 경로를 보존하는 반대 사례 3건을 확인한 후 인접 테스트 [106건](../runtime/final-qwen-performance/meaning-fast.xml)이 모두 통과했다. 기존 실제 87개 요청의 저장된 102개 턴에서 이 결정 규칙을 확인한 결과 H32 첫 턴만 해당했다. H32의 추출 후보 2개는 변경 전에도 둘 다 검토에서 거절됐으며 실제 계획은 폐기된 두 후보를 사용하지 않았다. 일반 의미 검토를 전역에서 없애는 실험은 시행하지 않았다.

| H32 측정 | 초기·최종 결과 | 응답 수 | 입력 토큰 | 출력 토큰 | 사례 전체 |
|---|---|---:|---:|---:|---:|
| 변경 전, 같은 계측 | READY / READY | 8 | 47,178 | 1,709 | 140.95초 |
| 첫 번째 변경만 적용 | READY / READY | 4 | 37,758 | 1,005 | 98.59초 |

두 실행에서 Markdown과 Gmail 본문은 같은 요약 텍스트를 참조했다. 최초 실측에 기록된 154.31초는 다른 계측의 과거 참고값이다. 위 두 실행에서도 모델 상주·호스트 부하와 스냅샷 크기는 변동하므로 42.36초 전체를 일반화된 순수 제품 효과로 해석할 수 없다. [같은 계측의 변경 전 원본](../runtime/final-qwen-performance/before-four.json) · [첫 번째 변경 원본](../runtime/final-qwen-performance/meaning-only-h32.json)

## 후속 변경과 검증 기준

**Capability 카탈로그 압축 실험은 회수했다.** 전체 도구의 설명을 같은 열 구성을 가진 연속 행끼리 묶고 공통 계약을 한 번 기술했다. 직렬화 후 역변환한 62개 도구의 ID·순서·설명·operation·포트는 원래 목록과 완전히 같았고 인접 테스트도 384/384 통과했다. 하지만 같은 정보를 다른 모양으로 읽은 모델은 H21에서 중복 정책 이외에 파일 경로와 이름까지 사용자에게 표시했고, 정책 답변을 받은 후에도 다시 물었다. H01과 H32의 입력 토큰이 줄었어도 이 표현을 채택하지 않았다. 채택본에서 실제 `assess_capability`의 함수 본문은 기존 도구 목록 표현과 같다. 후보의 입력 변환·검사·실측 기록은 보관했다. [후보 Fast 결과](../runtime/final-qwen-performance/capability-fast.xml) · [입력 구성 검산](../runtime/final-qwen-performance/candidate-work/prompt-shapes.json) · [미채택 후보 실측](../runtime/final-qwen-performance/after-four-9b.json)

**변경 전 새로 재현한 H21.** 두 모델 호출을 생략하기 전의 H32 통합본에서 H21은 초기·최종 분기가 모두 CLARIFY였다. 모델이 중복 정책 네 가지 선택지를 문장으로 반환했고 JSON 판정의 인용 필드와 설명이 별도 질문으로 따라왔다. 후속 답변 ID가 범용 슬롯에만 연결돼, Condition 모델의 올바른 last도 미정으로 남았다. `capability_check.py`는 기존 선택지의 전체 집합과 단일 열의 정책 질문을 확인해 해당 슬롯에 연결한다. 원문과 대조할 수 있는 직렬화 인용 조각은 진단으로 구분하고, 별개의 정상 질문은 유지한다. 앞부분은 이번 실패 실행의 세 응답, 생성까지 도달하지 못한 뒷부분은 과거 성공 실행의 두 응답으로 구성한 서비스 재생에서 단일 질문·같은 QID·최종 CSV 3·4·5행까지 확인했다. [재생 입력 출처와 결과](../runtime/a4b-final-integration-20261111/h21-recovered-prefix.jsonl)

**기존 캐시 재검증.** Meaning과 Capability의 지문에는 해당 요청 턴까지의 답변 이벤트(QID 및 소유 키), 모델과 사용자별 시간 기준을 포함시켰다. Capability·Requirement의 재사용은 등록 도구·자료·현재 정책 버전에도 묶인다. Condition의 권한 있는 슬롯·근거 digest는 Requirement에서 대조한다. 새 질문을 표시하면서 생기는 ID를 그 질문의 지문에 다시 넣지 않도록, 이미 제출된 사용자 답변만 포함한다. 로컬 Ollama의 명시적 별칭 매핑이 바뀌면 실제 해석되는 모델 ID가 달라져 재판정한다. 설치 환경이나 서버에서 같은 ID의 가중치를 조용히 교체하는 것까지 로컬 상태만으로 감지할 수는 없으므로, 이번 전후 측정에서는 서버가 보고한 가중치 digest를 별도로 대조했다. 변경 전 재현 8건과 변경 후 기존 재개·보류 흐름 검사를 수행했다. [캐시 518건 검사](../runtime/final-qwen-performance/cache-final.xml) · [별칭·마이그레이션 검사](../runtime/final-qwen-performance/cache-alias-final.xml)

**Repair 분기.** `supervisor_node`는 일반 Repair로 가려는 경우에만, Validator가 `other/unverified`로 반환한 같은 업무 ID를 현재 장부·초안의 coverage와 다시 대조한다. 검증할 수 있는 도구 대응이 장부에 없는 경우 초안을 그대로 보존하고 실패로 종료한다. 새 초안이 고칠 수 있는 연결 오류나 오래된 coverage, 사용자 질문은 이 단축 경로에 들어가지 않는다. 과거 8개 실패 체크포인트 중 현재 장부 규칙으로 재검증 가능한 6개에서 한 번의 불필요한 재생성 대신 즉시 같은 실패 진단으로 끝났다. 재검증할 수 없는 과거 2개에는 절감량을 부여하지 않았다. 네 Focused 사례에는 해당 Repair가 없어 실측된 절감에 합산하지 않았다. [체크포인트 재생](../runtime/final-qwen-performance/repair-checkpoint-replay.json) · [관련 162건 회귀](../runtime/final-qwen-performance/repair-regression.xml)

## 최종 코드와 측정 범위

채택한 앱 SHA-256은 `c99d88424a4e1baa113eb531b34a3ab75adfd0a6f1ec182b151845d0b8312416`이다. 이 소스의 249개 앱 파일에서 기존 실패 12건 및 관련 정상 3건을 오프라인으로 다시 확인했다. 같은 소스의 설계·조건·Capability·평가 하니스·모델 마이그레이션 테스트는 **2,413건 통과, 실패·오류·건너뜀 0건**이었다. 미채택 압축 후보에는 포맷 전용 테스트 2개가 더 있었고 모의 모델의 설정 시점과 자료 변경 후의 재평가를 올바르게 준비하지 않은 fixture 2건이 실패했다. 두 fixture를 실제 세션 순서에 맞춰 수정한 후 관련 두 파일 37건 및 채택본의 전체 대상 회귀를 재실행했다. [2,413건 통합 결과](../runtime/final-qwen-performance/regression-verified.xml) · [fixture 재확인 37건](../runtime/final-qwen-performance/session-regressions-now.xml)

속도 비교의 기본 단위는 같은 9B 모델·가중치·양자화·GPU·Ollama·문맥·샘플링·동결 입력·계측 하니스이다. GPU 상주를 입증하지 못한 시도는 실제 평가 건수에 넣지 않았다. 성능 검증 도중 사용자의 요청에 따라 `qwen3.5:4b`만 Ollama에서 삭제하고 설치 목록과 GPU 메모리에서 제거된 것을 확인했다. 평가에 4B 응답을 사용한 사례는 0건이다. 최종 실행에서는 다른 모델로 인한 재적재 상황이 다를 수 있으므로 지연 변화 전부를 코드에 귀속하지 않는다.

## 남은 최적화

1. Planner는 H32에서 13,619 입력 토큰을 사용했다. 전체 도구의 상세 스키마를 항상 모두 보내는 구조는 아니다. 실제로 중복된 `request_requirements`의 spec과 `required_execution_contracts`의 spec, 문서 요청에도 동봉된 표 전용 계약부터 각각 분리해 비교할 수 있다. 도구 발견 목록과 실제 선행 포트 계약은 보존해야 한다.
2. H32에서 형식·수신자 정보를 Requirement가 다시 추출한 뒤 기존 소유권 검사로 복원하는 1회가 남아 있다. 추가 승인·다른 본문·복수 목적지는 일반 경로로 유지할 수 있을 만큼 범위가 입증될 때 기존 계획 투영의 재사용을 검토한다.
3. 단순 표용 `compile_conditions`는 이미 존재한다. Condition의 슬롯이 완전해도 사용자가 요구한 **다른 업무가 없는지** 별도로 입증해야 한다. 이메일 발송·사람 승인·추가 산출물을 붙여도 일부 표 슬롯은 같게 나온다. 해당 경우를 구분하기 전에는 전체 표 요청을 템플릿으로 전환하거나 기존 Validator의 요구사항을 줄이지 않는다.
4. 서버 모델 적재 지연과 최초 토큰·출력 생성 시간을 여러 번 나누어 재면 지연 변동과 코드 효과를 더 잘 분리할 수 있다. 지금의 단일 Focused 실행으로 대표 사용자 p50 또는 전체 성공률을 갱신하지 않는다.

## 재현 설정

저장소 루트에서 프로젝트 잠금 파일로 준비한 워커 Python을 지정한다.

```powershell
$project = (Resolve-Path '.').Path
$python = (Resolve-Path 'Backend/.venv-agent/Scripts/python.exe').Path
$env:PYTHONUTF8 = '1'
$env:A4B_EVALUATION_REQUIRE_GPU = '1'
$out = Join-Path $project ('runtime/performance-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $out | Out-Null
& $python scripts/verify/run-local-orchestration-reassessment.py --prepare-only --output-dir $out
$runner = @('scripts/verify/run-qwen-live-evaluation.mjs','--python',$python,'--profile','local-public-9b-deepagent',
  '--allow-local-endpoint','http://127.0.0.1:11434/v1','--phase','reassessment','--live')
$legacy = 'docs/qwen-live-evaluation-cases.json'
$regression = 'docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json'
$regressionArgs = @('--corpus',$regression,'--corpus-profile','runpod-rebuild','--expected-gold-sha',
  '93c821ded55570fda569ceb983f22fd2ebf8d6b8c7d395d445e68e4931bc7e35')
$selection = @('--case','H01','--case','H20','--case','H21','--case','H32')
```

### Fast 및 Focused Live

Fast는 오프라인 테스트와 [통합 문서의 저장 원본 재생](a4b-final-integration-check.md)을 사용한다. 아래 Focused 명령은 새 실행 한 개만 생성한다. CLI 결과의 종료 상태와 의미 검사를 모두 확인한다.

```powershell
& $python -m pytest Backend/tests/design Backend/tests/conditions Backend/tests/capabilities scripts/verify/tests -q --basetemp "$out/pytest" --junitxml="$out/regression.xml"
node @runner @regressionArgs @selection --max-calls 80 --output "$out/four.json"
& $python scripts/verify/check-focused-qwen-outcomes.py "$out/four.json" @selection
& $python scripts/verify/profile-qwen-stages.py "$out/four.json"
```

이 보고서의 비교는 당시 로컬 코드 사본을 사용했다. 공개된 `runtime/final-qwen-performance` 측정 파일은 저장소의 단계별 집계기로 확인할 수 있다. 현재 체크아웃에 대한 단일 H32 실측은 위 명령의 `@selection`을 `--case H32`로 변경한다. [공개 증거 범위](orchestration-evidence.md)

### Full 재현 명령 (이번 작업에서 미실행)

아래 명령 세 개는 릴리스 후보가 고정된 후 각각 독립 실행한다. 35건은 알려진 회귀군이고, 새 동결 30건은 개발 과정에서 모델 실행·답안 튜닝에 사용하지 않는다. 결과에 맞춰 gold를 바꾸지 않는다. 전체 성공률은 각 새 raw에 별도로 작성한 semantic/ledger 라벨과 동결 기준을 기존 채점기에 함께 전달해 판정한다.

```powershell
node @runner --corpus $legacy --corpus-profile original-52 --max-calls 600 --output "$out/legacy52.json"
node @runner @regressionArgs --max-calls 600 --output "$out/regression35.json"
node @runner --corpus docs/orchestration-generalization-holdout/cases.json --corpus-profile runpod-rebuild --expected-gold-sha b861a55a0ae1b6676c68ce71d3ee9a987e661fe11bae749291ac0e266cc119d1 --max-calls 600 --output "$out/new-holdout30.json"
```
