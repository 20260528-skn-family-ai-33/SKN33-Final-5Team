# 2026-10-21 로컬 통합 작업본

RunPod 주소·모델명·API 키를 `.env`로 연결하는 방법은 [RUNPOD.md](RUNPOD.md)를 참고한다.
루트 `.env`에는 마지막 RunPod 연결 설정이 있으며 해당 테스트 Pod는 중지돼 있다. `.env.runpod.example`에 원격 서버용 예시가 있다.
원본 ZIP과의 계약 대조 및 변경 범위는 [ZIP_ALIGNMENT.md](ZIP_ALIGNMENT.md)를 참고한다.
최종 연결·시간 제한·ZIP 계약 회귀 검사는 Python 292개 및 subtest 13개 통과했다.

기준: 원격 main `1956d6f9e793836a8635ba41eb75b9ae549ae82e`.
원본 다운로드 패키지와 기존 작업 폴더는 수정하지 않았다. 원격 push는 하지 않았다.

## 연결한 내용

- 아침 통합자료의 QwenChatOpenAI 어댑터를 vLLM용 모델 팩토리에 연결했다.
- 설계, 문서 실행, 재시도, 이어서 실행, 예약 및 구형 실행 경로가 같은 모델 팩토리를 사용한다.
- Ollama는 별도 provider 경로이며 vLLM 전용 옵션을 보내지 않는다.
- 계정 키를 저장하지 않고 로컬 서버 연결을 선택할 수 있다. 서버 설정은 환경변수로 명시한다.
- 분류·검증 패키지를 `validate → verification → supervisor`에 연결했다.
- 검증 입력은 실제 자료/열/샘플/대화/초안과 현재 도구 등록부에서 만든다. 원문을 임의로 잘라 ready로 만들지 않는다.
- 같은 사용자 턴의 내부 재수정 중에도 이전 사용자 턴의 초안을 보존한다.
- 검증 입력·결과는 이 작업본의 SQLite `builder_verifications`에 저장한다. AWS migration은 아니다.
- 기존 SVM은 선택적 `A4B_TOOL_ADVICE_MODE=hint`로 설계 참고에만 사용한다. 도구 목록은 줄이지 않는다.

## 검증 모드

- `off`: 기존 경로.
- `shadow`: 검증 결과·진단을 기록하되 기존 승인 경로를 변경하지 않는다.
- `enforce`: 보류·불일치면 저장을 차단하고 기존 수정 루프로 보낸다. 검증기가 실행을 승인하지 않는다.

기본 로컬 실행 스크립트는 shadow다. 연구 단계 검증기의 정확도를 가정해 기본 업무 경로를 막지 않는다.
실제 검증기 호출 성공과 의미 정확도는 별도 보고한다. fixture는 고정된 예제 응답이며 모델 성능이 아니다.

## 실행

Ollama 서버와 필요한 모델이 준비된 상태에서 저장소 루트에서:

```sh
sh scripts/run-local.sh
```

주소: http://127.0.0.1:3041

```sh
A4B_VERIFIER_MODE=enforce sh scripts/run-local.sh
A4B_TOOL_ADVICE_MODE=hint sh scripts/run-local.sh
```

검증기 모델: `qwen3.5:4b`. 메인 모델 기본값: `qwen3:8b-q4_K_M`.
메인 모델 변경은 `A4B_MAIN_MODEL`, vLLM 사용 시 provider/base URL도 함께 지정한다.
포트가 사용 중이면 기존 로컬 실행을 종료하거나 `PORT=3042`를 지정한다.

이 작업본은 설치돼 있던 Node 패키지와 Python 의존성을 로컬 경로로 재사용한다.
다른 PC에서는 Frontend의 잠금 파일로 의존성을 설치하고,
Backend/requirements-local-integration.txt로 전용 Python 환경을 준비해야 한다.

## 검사

```sh
PYTHONPATH=Backend Backend/.venv-agent/bin/python -m pytest Backend/agent_builder Backend/integrations/qwen_verifier/tests -q
```

main에서 빠진 기존 전처리 테스트 자료는 제공된 build_dataset.py로 재생성했다.
이 자료는 합성 회귀 fixture이며 새 학습·독립 평가 데이터가 아니다.
실제 HTTP CSV 검사는 `python3 scripts/local_http_smoke.py`이며 output/에 원시 결과를 저장한다.

## 실제 확인 결과

- Python: 270개 통과, 추가 subtest 13개 통과.
- 선택한 프런트엔드 회귀 검사 16개 통과. TypeScript 검사·클라이언트/서버 빌드 통과.
- 분류·검증기 전달본의 SHA256 파일 60개 일치, 고정 응답 예제 경로 13/13 통과.
- 기존 SVM 파일을 실제로 로드해 추천 점수를 계산했다. 추천 후보에서 규칙 추가 finish/saved_workflow는 제외한다.
- 실제 Qwen3.5-4B(`qwen3.5:4b`)를 다운로드했다. Ollama 0.40.6, 모델 digest는
  `7f3251fa6878a78a606bcb3c074283306e5ad2ea411a7774a9dd98a4bc7f2d13`.
- 새 실제 검증 3건은 JSON 응답을 받았으나 기대 경로 0/3이다. 세 건 모두 manual_review로 보류됐다.
  명확한 요청도 원문에 없는 공백을 넣어 인용했고, 모호한 기준·초과 연산자 반영 판단은 잘못됐다.
  경로를 통과시키려고 정답·원문 검사·기대값을 완화하지 않았다.
- 검증기 호출 지연은 해당 3건에서 약 12.1초/21.9초/7.9초였다. 독립 성능·속도 벤치마크가 아니다.
- 기존 Qwen3-8B 메인 생성의 첫 HTTP 시험은 완료되지 않았다. 최종 실험 결과는 output/의 JSON을 확인한다.
- Ollama 비추론 옵션 보완 후 Qwen3-8B는 앱에서 약 112.4초에 응답했다.
  명확한 CSV 요청에 구현 확인 질문을 만들어 needs_clarification으로 종료했다.
  그 실제 초안을 별도 Qwen3.5 검증기에 전달하고 감사 기록까지 저장하는 경로는 동작했다.
  검증기도 인용을 정확히 재현하지 못해 manual_review였으며, shadow에서는 원래 질문 경로를 유지했다.
  생성·실행 전체 성공으로 집계하지 않는다.
- 동일 코드·동일 CSV 요청에서 메인 모델만 Qwen3.5-4B로 바꾼 추가 비교는
  180초 앱 제한에 도달해 실패했다. 원시 결과는 `output/local-http-smoke-qwen35-main.json`이다.
  최종 로컬 기본 설정은 원래 메인 Qwen3-8B와 별도 Qwen3.5-4B shadow 검증기다.

원시 신규 검증 결과: `output/qwen35-live-smoke.json`.
패키지에 동봉된 과거 reports는 새 결과로 덮어쓰지 않았다.
Qwen 메인 연결과 별도 검증기의 모델·서버·API는 구분해서 기록한다.

Ollama /v1의 비추론 설정은 공식 호환 문서의 reasoning_effort=none도 함께 지정한다:
https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx

## 추가 파서 통합

파서 handoff의 standard Docling·한영 OCR·표 후처리·본문 보완을 연결했다.
별도 `Backend/.venv-docling`과 `Backend/requirements-parser.txt`를 사용한다.
공개 OCR/표 모델을 준비한 뒤 출력된 절대 캐시 경로를 루트 `.env`에 넣는다.
LightOn은 기본 꺼짐이다. 원본·메타데이터·부분 성공 상태를 보존하며, DB 초안 계약은 워커 출력과 구분한다.
파서 회귀 51개, 현재 Python 회귀 292개와 하위 검사 13개가 통과했다.
합성 PDF의 실제 업로드→원본 보관→SQLite 재조회→검증기 문서 변환은 통과했다
(`output/parser-app-bridge-smoke.json`). 운영 AWS/RDS 연동이나 전체 문서 정확도 평가는 아니다.
메인 Qwen의 ZIP 설정 회귀는 시간 초과로 종료돼 업무 생성·실행 성공으로 세지 않는다
(`output/ollama-zip-settings-regression.json`).

## 재점검 결과

잘못된 parser_metadata/supplement_report 타입은 예외로 앱을 중단시키지 않고 검증 입력 계약 오류로 기록하여 manual_review로 보류한다.
추가 회귀 포함 Python 292개·하위 검사 13개가 통과했다.
실제 누락 본문 보완은 원문에 있는 문장을 복구했고 오류가 없었다.
텍스트 레이어가 없는 합성 PDF에서 한영 OCR이 동작했지만 1500원을 `1 5 0 0 원`으로 띄어 인식했다.
이를 임의로 교정하거나 OCR 정확도 보장으로 집계하지 않는다. 실제 문서의 금액·표 관계는 원문 대조가 필요하다.
메인 Qwen의 전체 업무 생성은 여전히 시간 초과가 남아 있다. `output/integration-recheck.json` 참조.
