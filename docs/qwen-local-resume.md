# 최종 완료 상태 — 2026-11-11 KST

공개 Qwen3.5 9B의 baseline52개, after52개(51+1 shard), 별도3개 반복 및 회귀 검증을 완료했다. 전체 요청 성공23/52→28/52, 실제 모델21/50→26/50이며 잔존실패24개다. 최종 보고서 docs/qwen-live-evaluation-report.md와 산출물 인덱스 docs/qwen-live-after.json을 먼저 참조한다. 원래 RunPod와 첫 로컬 중단 결과는 보존했다. commit/push하지 않았다.

아래는 이전 진행 시점의 역사 기록이다.

# 로컬 GPU 평가 재개 상태

2026-11-10, Asia/Seoul. **최신 선택: 공개 Qwen3.5 9B로 변경.** 사용자가 커스텀 가중치를 구할 수 없다고 답하고 공개 9B 다운로드를 선택했다. 다운로드와 `a4b-public-qwen35-9b-32k` 등록, 실제 구조화 응답과 32k 로딩 확인 및 3개 smoke 측정을 마쳤다. 현재 실행 프로필·명령은 [공개 9B 평가 계획](qwen-local-public9b-plan.md), 실제 모델 정보는 `qwen-local-public9b-model.json`을 따른다.

## 현재 진행 — 2026-11-10 23:24 KST after 시작

새 정식 baseline `qwen-local-public9b-baseline-v2.json`의 전체 52개 측정과 근거 검토를 완료했다. 사례 측정 종료 50개와 worker 실패 2개를 모두 포함하며, 실제 모델 호출 사례 50개·코드 recipe 2개다. 별도 응답 근거 집계는 사례180+probe1=181개의 반환 응답을 확인했다. 전체 서버 추론·비용 총수는 이 숫자로 단정하지 않는다. 전체 요청 설계 성공은23/52, 실제 모델 사례만21/50이다. 통합 라벨·지표·계수는 각각 `qwen-local-public9b-baseline-v2-review.json`, `qwen-local-public9b-baseline-v2-metrics.json`, `qwen-local-public9b-baseline-v2-accounting.json`에 저장했다. development 실패의 최소 수정과 Python 통합 회귀2069개, TypeScript41개 및 양쪽 타입 검사를 마쳤다. `qwen-local-public9b-after.json`에 같은 환경의52개 after를 실행 중이다. 수정 후 앱 source SHA256은 `6a417569cd7f1fd24065c981c6e31cf2d2622d0ff3c54e41164e60e3e60be460`이며 평가기는 baseline과 동일하다. 앱·테스트·평가기를 동결했다. 첫 로컬 baseline은 저장 callback 오류가 앱 fallback에 섞일 수 있음을 발견해 중단했으며 최종 품질 비교에서 제외했다. 원본과 경위는 `qwen-local-public9b-baseline-instrumentation-interrupted-raw.json`, `qwen-local-public9b-instrumentation-audit.json`에 보존했다. 첫 실행의 일부 사례를 새 baseline에 합치지 않는다.

저장 오류의 별도 fatal 중단, Windows 파일 공유경합 재시도 및 완성된 복구본 보존을 수정했다. 실제 Win32 파일 핸들 잠금 등을 포함한 합성 검사 57개가 통과했고 독립 코드 검토도 마쳤다(`qwen-local-storage-fatal-tests-v2.xml`). baseline 당시 앱 소스는 기존 242개 파일의 SHA256 `6550b20928227b74c6a2c33cded04e62d359a6550c034dce935cad5230e01be5`이며, v2 평가기 manifest SHA256은 `784bd2475a1fa7ccad3f85a6a17fe5d7e708f9b5a958ef25e09b317a9959aea6`이다.

v2의 SDK 응답은 raw·실제 model·사용량으로 보존되지만, 설치된 OpenAI SDK의 `httpx2` 경로가 `httpx` 전송 계측을 우회한다. 따라서 원본의 dispatch/응답 계수 및 `live_model_calls`는 SDK가 누락된 하한이다. 이는 응답 내용이나 앱 처리 흐름이 바뀐 증거가 아니며, 동일 평가기를 baseline/after에 유지하고 별도 증거 기반 후처리 결과로 응답 수를 보완한다. SDK의 HTTP 시도 횟수 자체는 복원하지 않는다. HTTP 경로/redirect 훅이 SDK에 적용되지 않은 한계도 함께 보고한다. 명시한 localhost 모델 주소와 선택 모델 확인, socket 목적지 제한, 외부 도구 실행 차단은 유지한다.

수정 전 소스 242개 바이트는 `runtime/qwen-public9b-pre-fix-source-20261110`에 hash와 함께 보존했다. 새 회귀의 첫 실행은 pytest 예약 매개변수 이름 때문에 수집 오류였으며, 이를 바로잡은 `qwen-local-public9b-failures-before2.xml`에는 수정 전51 failed/41 passed가 기록돼 있다. 이는 실제 모델 품질 수치가 아니다. TypeScript 계약·상태41개 및 Frontend/Backend API 타입 검사는 통과했다. Python 통합 회귀는 `qwen-local-public9b-regression-after.xml`에2069 passed로 기록했다. 동일52개 after 및 별도반복3개, 최종 전후 보고가 남아 있다.

이하 커스텀 모델 부재·추론 0회·환경 미검증·예정 명령은 **공개 모델 선택 전 초기 점검 기록**이다. 현재 진행 상태나 실행 명령으로 해석하지 않는다. 로컬 baseline/after는 공개 모델 전용 파일로만 비교한다.

## 보존 및 검증

- 기존 HEAD와 애플리케이션 manifest 242개 파일, 고정 정답 hash가 인계 기록과 일치한다. 기존 미커밋 애플리케이션 변경을 그대로 보존했다.
- 기존 RunPod baseline은 `paused`, 저장 사례 29개(완료 기록 28개 + R10 worker 실패 1개), 실제 호출 사례 27개, 저장 사례 trace 78회, 미실행 23개다. 이것은 성공 사례 수나 총 사용량이 아니다.
- 원본 RunPod baseline, interrupted raw, 검토 초안과 정답 파일을 수정하지 않았다. 상세 hash는 `qwen-local-resume-integrity.json`에 기록했다.
- 새 로컬 환경은 기존 RunPod 결과에 병합하지 않는다. 가중치가 같더라도 하드웨어·의존성·SVM 조건이 달라 동일 52개 전체를 새 baseline으로 측정한다.

## 이전에 지정했던 커스텀 모델 설정

```text
A4B_MAIN_PROVIDER=ollama
A4B_MAIN_BASE_URL=http://127.0.0.1:11434/v1
A4B_MAIN_API_KEY=
A4B_MAIN_MODEL=a4b-qwen35-9b-final664-pilot32k
A4B_CAPABILITY_MODEL=a4b-qwen35-9b-previous-pilot32k
A4B_OLLAMA_NUM_CTX=32768
A4B_OLLAMA_NUM_PREDICT=4096
A4B_OLLAMA_TEMPERATURE=0
A4B_MAIN_TIMEOUT_S=180
```

이 설정은 평가 자식 프로세스에만 적용한다. 기존 `.env` 또는 운영 endpoint는 변경하지 않는다. 인계된 deepagent / capability·condition·requirement enforce / verifier off / Python validator 유지 조건도 적용한다.

모델 ID만으로 가중치가 설치되지는 않는다. 현재 기본 Ollama 모델 저장소에는 `qwen3.5:4b`, `qwen3:8b-q4_K_M`의 manifest가 있고 위 커스텀 9B 두 모델은 없다. 샌드박스 내부 첫 조회는 네트워크 권한 오류(EACCES)로 실패했지만, 호스트에서 다시 조회한 `/v1/models`와 `/api/tags`는 모두 HTTP 200이다. 실제 서버 모델 목록에도 두 커스텀 모델이 없다. 다른 경로의 모델 파일 존재 여부는 확인되지 않았다.

읽기 전용 연결 점검은 `qwen-local-endpoint-preflight.json`(샌드박스 접근 오류) 및 `qwen-local-endpoint-preflight-host.json`(서버 응답 정상, 지정 모델 없음)에 별도로 보존했다. 두 점검 모두 추론 0회이며 인증 키를 사용하지 않았다.

기존 모델 패키지나 Ollama manifest·참조 blob, 또는 검증된 GGUF/어댑터 및 원본·template·Modelfile 설정이 필요하다. 공개 Qwen 모델에 같은 이름을 붙여 대체하지 않는다. 반입 위치와 파일 용량을 확인하고 사용자 선택 없이 공개 모델을 다운로드하지 않는다.

## 현재 실행 환경

- GPU: NVIDIA GeForce RTX 4060 Ti, VRAM 8,188 MiB, driver 610.88.
- 시스템 RAM 약 15.93 GiB. 실제 9B 두 모델의 로딩, 32k context, GPU/CPU 분담 및 지연은 미검증이다. 동시성 1을 유지하고 메모리 부족이나 timeout을 숨기지 않는다.
- Ollama CLI 0.40.15. 서버 버전·실제 모델 digest·template·context는 모델 반입 후 확인한다.
- `Backend/.venv-agent/Scripts/python.exe`: Python 3.13.16. agent-lock/canonical-tools/ml-svm56 81개 패키지 일치 및 `pip check` 통과.
- Node 24.20.0. 기존 node_modules와 package-lock 검증 통과. 추가 설치하지 않았다.
- 로컬 SVM 의존성과 아티팩트가 있으며 오프라인 추천 probe가 `svm_candidates_verified`를 반환했다. 추천 품질 및 Qwen 전달 품질의 실측은 아니다. 기존 RunPod의 SVM 로딩 실패 조건과 구분하며 로컬 baseline/after에서는 같은 환경을 유지한다.
- 구체적 버전과 의존성 hash는 `qwen-local-python-environment.json`에 기록했다.

## 이번 환경의 합성 검증

`qwen-local-precheck-before.xml`:

- 기존 평가기/scorer 테스트: 12 passed.
- 기존 live 실패 재현 테스트: 9 failed / 4 passed. 인계 기록과 동일하다.
- 합계 9 failed / 16 passed. 실제 Qwen 호출·HTTP 서비스·브라우저 UI 검증은 아니다.
- 애플리케이션 실패 수정은 로컬 전체 baseline 측정 전까지 적용하지 않는다.

추가한 로컬 평가기 검증은 `qwen-local-harness-tests-20261110-v2.xml`에 44 passed(신규 32개 + 기존 평가기 12개)로 기록했다. Node 목적지 정책 테스트 3개도 통과했다. 이는 위 실패 재현 9개를 수정했다는 의미가 아니다.

평가기만 다음과 같이 보완했다: 명시적 loopback opt-in, 목적지·port·API 경로 및 redirect 제한, 선택 모델과 응답 model ID 확인, endpoint·패키지·하드웨어·Ollama 버전·모델 digest/설정 비교, 불완전 baseline의 after 거절, 암묵 기본 모델 제거, 실행 중 `running` 및 active case 호출의 원자적 저장. graceful interruption과 진행 중 호출 snapshot 보존도 합성 테스트했다. 강제 종료 시 서버가 처리했으나 응답을 아직 받지 못한 요청의 실제 사용량까지 증명하지는 않는다.

이번 로컬 평가기 지원은 모델 provenance를 조회할 수 있는 Ollama로 한정한다. 애플리케이션의 기존 vLLM 지원은 변경하지 않았다. SVM 가중치 내용은 현재 환경 문서의 파일 hash로 별도 확인하며 평가기 환경 identity가 자동 재검증하는 항목은 아니다.

평가기 수정 후에도 애플리케이션 242개 source hash 및 기존 baseline/raw/review/gold 파일의 바이트 hash가 이전과 같은 것을 재확인했다.

## 다음 실행 순서

1. 사용자가 지정한 커스텀 모델의 위치를 확인하고 실제 가중치와 설정을 반입한다. 가중치·template·모델 digest와 32k 등록 context를 확인한다. native `num_ctx` 환경변수만으로 OpenAI 호환 경로의 context가 설정됐다고 간주하지 않는다.
2. 명시한 loopback 주소만 허용하도록 보완한 평가기를 합성 테스트한다. 외부 Gmail/Notion/고객 DB·이메일 발송 차단을 유지한다. endpoint·모델 digest·의존성·하드웨어 환경 식별값으로 다른 환경의 merge/after를 거절한다.
3. 모델 목록, 구조화 출력, 실제 응답 model metadata 및 GPU 로딩을 확인한다. probe와 smoke 결과는 전체 baseline과 분리한다.
4. `docs/qwen-local-baseline.json`에 고정 52개 전체 baseline을 새로 생성한다. 기존 RunPod의 남은 23개만 실행해 이어 붙이지 않는다. 실행 중 헤더는 `running`, 중도 종료는 미완료로 기록한다.
5. 완전한 baseline의 development 실패만 근거로 최소 수정하고 동일 로컬 환경에서 `docs/qwen-local-after.json`을 생성한다. holdout을 수정용 예제로 사용하지 않는다.
6. 실제 모델/합성 회귀/미실행을 구분하고 지표의 분모·미검토 범위·원인·잔존 문제를 최종 분석한다. 원래 schema 분모에서 parser 예외 후보가 빠질 수 있는 계측 한계도 보완하거나 명시한다.

완전한 local baseline/after 및 최종 품질 보고서는 아직 없다. 가상의 결과 파일을 생성하지 않았다. 커스텀 가중치 위치 대기는 사용자의 공개 모델 선택으로 해소되었다. 실제 로컬 평가에는 공개 전용 이름과 별도 결과 파일을 사용한다.

## 과거 커스텀 모델 구성의 참고 명령

아래 커스텀 모델 명령은 실행하지 않았다. 현재 공개 모델에는 사용하지 않으며, 현재 명령은 `qwen-local-public9b-plan.md`의 `local-public-9b-deepagent` 프로필이다. 기존 출력이 있으면 새 파일명을 지정한다.

```powershell
$env:A4B_MAIN_PROVIDER = 'ollama'
$env:A4B_MAIN_BASE_URL = 'http://127.0.0.1:11434/v1'
$env:A4B_MAIN_API_KEY = ''
$python = (Resolve-Path Backend/.venv-agent/Scripts/python.exe).Path
$localEndpoint = 'http://127.0.0.1:11434/v1'

# documented-9b-deepagent는 위 두 커스텀 모델과 32k/4096/0/180 프로필을 적용한다.
# 먼저 모델 반입과 context/GPU 로딩을 확인한 뒤 독립 smoke를 수행한다.
node scripts/verify/run-qwen-live-evaluation.mjs --python $python --profile documented-9b-deepagent --allow-local-endpoint $localEndpoint --live --phase baseline --case R01 --case C04 --case M01 --output docs/qwen-local-smoke-baseline.json --max-calls 40

# smoke를 전체 결과에 합치지 않고, 52개 모두 새 세션으로 평가한다.
node scripts/verify/run-qwen-live-evaluation.mjs --python $python --profile documented-9b-deepagent --allow-local-endpoint $localEndpoint --live --phase baseline --output docs/qwen-local-baseline.json --max-calls 400

# 전체 baseline 검토 및 최소 애플리케이션 수정 후에만 실행한다.
node scripts/verify/run-qwen-live-evaluation.mjs --python $python --profile documented-9b-deepagent --allow-local-endpoint $localEndpoint --live --phase after --baseline docs/qwen-local-baseline.json --output docs/qwen-local-after.json --max-calls 400
```

샌드박스의 localhost 접근이 `EACCES`로 막히면 모델 오류로 기록하지 않고 호스트 실행 경계에서 확인한다. 모델 파일 확보 전에는 위 명령을 실행하지 않는다.
