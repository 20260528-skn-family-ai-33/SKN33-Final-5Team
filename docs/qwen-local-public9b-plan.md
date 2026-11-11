# 공개 Qwen 3.5 9B 로컬 평가 계획

2026-11-10, Asia/Seoul. 사용자가 공개 `qwen3.5:9b` 다운로드를 선택했다. 이전 RunPod의 private fine-tuned final664/previous 모델과 다른 가중치이며, 기존 RunPod 결과의 나머지 23개로 병합하지 않는다. 동일한 고정 52개로 별도 로컬 baseline을 처음부터 생성한다. 애플리케이션 변경은 이 baseline 완료 뒤 development 실패 근거에 한해 진행한다.

## 계측 문제로 인한 첫 로컬 baseline 중단

첫 `qwen-local-public9b-baseline.json` 실행은 29개 사례 저장 후 M02 진행 중 감독 에이전트가 중단했다. T07에서 HTTP 호출 설정조차 기록되지 않은 started-only 항목 뒤에 앱 처리가 계속된 것을 발견했다. snapshot 저장 callback의 일반 예외가 앱의 의미 추출 오류 처리에 섞일 수 있는 결함이 있으며, 실제 파일 저장 실패의 종류는 당시 로그가 없어 확정하지 않는다. Windows 파일 읽기와 원자 교체의 공유 위반은 확인 대상 가설이다.

원본은 `qwen-local-public9b-baseline-instrumentation-interrupted-raw.json`에 바이트 그대로 보존했다(SHA256 `8333bbc85ce13ea59827c4b9706e7724e25121b8dd5cc5b5af241199affec501`). 첫 baseline은 `aborted`, `eligible_for_quality_comparison=false`로 표시했으며 총 실제 추론 수는 unknown으로 정정했다. trace 95개 항목 중 응답 확인은 93개(완료 사례 응답92+probe1), T07 의심 항목1과 중단 중 M02 항목1은 별도다. 95개를 실제 추론·비용 총수로 사용하지 않는다.

기존 ready/clarify/temporal 검토 라벨도 이 중단된 진단 실행에 관한 것이며 최종 품질 비교에 자동 재사용하지 않는다. 저장 경합 재시도와 계측 실패의 별도 중단 경계를 합성 검증한 뒤, 앱 소스를 그대로 두고 **새 `qwen-local-public9b-baseline-v2.json`에 전체52개를 다시 측정**한다. 첫 실행의 일부 결과를 골라 합치지 않는다.

## 모델과 실행 설정

- upstream: 공개 `qwen3.5:9b`
- 평가 등록명: `a4b-public-qwen35-9b-32k`; 기존 custom 모델명 재사용 금지
- MAIN / capability: 모두 위 공개 모델. 지원 판단과 생성의 기존 코드 역할·호출 경로는 유지한다.
- endpoint: `http://127.0.0.1:11434/v1`, provider Ollama, API key 빈값
- native context 32768, output ceiling 4096, temperature 0, top_p 0.8, seed 42, 앱의 think=false 유지
- DeepAgent, capability/condition/requirement enforce, requirement generation baseline, verifier off, Python validator 유지
- timeout 180초, 동시성 1, SVM56 hint. 이 컴퓨터의 SVM 활성 환경은 이전 RunPod 평가와 다르다.

실행기 `--profile local-public-9b-deepagent`는 위 값을 평가 자식 프로세스에만 적용하고 private 모델 alias를 제거한다. `.env`와 운영 설정을 수정하지 않는다. 목적지 허용은 별도의 정확한 `--allow-local-endpoint` 인자가 있어야 한다.

## 준비와 평가 순서

모델 다운로드·등록·실제 호출은 주 에이전트가 담당한다. 아래 명령은 절차이며 이 문서를 작성하면서 실행한 결과가 아니다.

```powershell
ollama pull qwen3.5:9b
ollama create a4b-public-qwen35-9b-32k -f scripts/verify/Modelfile.a4b-public-qwen35-9b-32k
```

등록 후 `/api/tags`의 digest·크기, `/api/show`의 context·parameters·quantization 및 `/api/version`을 기록한다. 실제 구조화 응답 model ID를 검증한다. 실행기는 endpoint·패키지 버전·하드웨어·서버 버전·모델 digest를 baseline/after 동등성 검사에 사용한다. GPU 메모리 적재 및 처리 시간은 실측하고 32k 설정을 조용히 낮추지 않는다. 환경 변경이 필요하면 변경 내용을 밝히고 새 baseline으로 시작한다.

```powershell
node scripts/verify/run-qwen-live-evaluation.mjs --python Backend/.venv-agent/Scripts/python.exe --profile local-public-9b-deepagent --allow-local-endpoint http://127.0.0.1:11434/v1 --live --phase baseline --case R01 --case C04 --case M01 --output docs/qwen-local-public9b-smoke.json --max-calls 40
node scripts/verify/run-qwen-live-evaluation.mjs --python Backend/.venv-agent/Scripts/python.exe --profile local-public-9b-deepagent --allow-local-endpoint http://127.0.0.1:11434/v1 --live --phase baseline --output docs/qwen-local-public9b-baseline-v2.json --max-calls 400
```

smoke와 52개 baseline은 별도 결과다. 기존 파일을 덮어쓰지 않는다. baseline에 미실행 사례가 남으면 완료로 간주하지 않고, 같은 환경의 중복 없는 후속 shard만 병합한다. 모든 고정 사례의 baseline 측정이 끝난 뒤 필요한 최소 수정과 회귀 검증을 수행하고 다음 명령으로 동일 52개를 재평가한다.

```powershell
node scripts/verify/run-qwen-live-evaluation.mjs --python Backend/.venv-agent/Scripts/python.exe --profile local-public-9b-deepagent --allow-local-endpoint http://127.0.0.1:11434/v1 --live --phase after --baseline docs/qwen-local-public9b-baseline-v2.json --output docs/qwen-local-public9b-after.json --max-calls 400
```

## 보존 및 해석

정답 digest `e939c051a940915c4061b45221e7f1353b8ce2157677d379ae193de13b1300da`와 기존 앱 source baseline을 보존한다. 기존 RunPod 29개 저장 사례·27개 모델 호출 사례·78개 보존 호출은 과거 별도 근거이며 공개 모델의 baseline에 포함하지 않는다. 계정 연결은 합성 metadata이며 Gmail·Notion·고객 DB 조회 및 이메일 발송을 허용하지 않는다.

실제 모델 평가, 합성 회귀 테스트, 미실행 항목을 구분한다. 프로필 테스트 통과는 Qwen 품질 검증이 아니다. 최종 보고는 공개 모델명·digest와 환경 차이를 명시하고 local baseline/after끼리만 개선 수치를 비교한다. 추천 SVM artifact 내용 hash는 별도 환경 기록을 참조하며 Python 패키지 버전만으로 모델 내용 동일성을 증명하지 않는다.


## 출력 변동성의 별도 반복 측정

2026-11-10 23:27 KST, after의 R01/R02 측정 후 나머지 결과를 보기 전에 반복 대상 **R04, C05, M01**을 고정했다. 중복 제거, 근거 없는 정책 확정 방지, 질문·답변·로컬 실행 흐름을 대표하는 development 사례다. 전체 after와 같은 앱·모델·환경·정답·하니스로 새 격리 세션에서 한 번 더 측정한다. 3개 표본의 변동을 전체 모델 안정성으로 일반화하지 않으며, 주 52개 after에 병합하거나 유리한 결과를 고르지 않는다.

```powershell
node scripts/verify/run-qwen-live-evaluation.mjs --python Backend/.venv-agent/Scripts/python.exe --profile local-public-9b-deepagent --allow-local-endpoint http://127.0.0.1:11434/v1 --live --phase after --baseline docs/qwen-local-public9b-baseline-v2.json --case R04 --case C05 --case M01 --output docs/qwen-local-public9b-after-repeat.json --max-calls 40
```

위 반복 명령은 2026-11-11 실행 완료했다. 본 평가의 결과는 `qwen-local-public9b-after.json`, 반복은 별도 `qwen-local-public9b-after-repeat.json`으로 구분한다.

## 2026-11-11 완료 기록

정식 baseline은 qwen-local-public9b-baseline-v2.json이다. after 첫 파일은 X06의 180초 모델 응답 timeout 때문에 incomplete로 종료해 51개 측정과 X07 미실행을 보존했다. 서버의 취소·idle을 확인한 뒤 X07만 같은 조건으로 qwen-local-public9b-after-part2.json에 측정했다. X06은 재실행하거나 교체하지 않았다.

두 shard의 source/gold/model/의존성/설정/harness 일치를 확인하고 qwen-local-public9b-after-complete.json에 중복 없이 병합했다. 후처리 병합 helper의 probe·카운터·미실행 목록·출처 hash를 보완하고 43개 회귀를 통과했다. frozen inference harness 8개 파일은 변경하지 않았다.

52개 최종 판정은 baseline23/52 → after28/52, 별도 반복3개는 R04 실패/C05 성공/M01 성공이며 main에 합치지 않았다. C05 반복의 내부 unresolved 변동과 after 악화3개도 남겼다. 최종 보고서·실패 분석·산출물 관계는 qwen-live-evaluation-report.md, qwen-live-failure-analysis.md, qwen-live-after.json을 참조한다.
