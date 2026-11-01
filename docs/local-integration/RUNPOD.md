# RunPod 연결 설정 — 4차 LENS 방식

## 기본 사용: 주소 한 곳만 변경

저장소 루트 `.env`에서 아래 주소를 본인의 RunPod **Ollama** 주소로 바꾼다.

```dotenv
A4B_LLM_BASE_URL=https://YOUR_POD_ID-11434.proxy.runpod.net/v1
A4B_LLM_MODEL=qwen3:8b-q4_K_M
A4B_VERIFIER_MODEL=qwen3.5:4b
A4B_VERIFIER_MODE=shadow
```

그 후 `sh scripts/run-local.sh`로 재시작한다. 화면에서 주소나 계정 키를 다시 넣지 않는다.
메인은 이 주소의 `/v1` API를 사용하고, 검증기는 같은 서버의 `/api/chat`을 사용하도록 자동 변환한다.
서버 Ollama에는 두 모델이 모두 설치돼 있어야 한다. 모델명이 다르면 MODEL 값도 서버의 이름에 맞춘다.
인증 없는 Ollama 서버에는 API 키가 필요 없다. 인증을 설정한 경우에만 `A4B_LLM_API_KEY`를 넣는다.
`.env.example`과 `.env.runpod.example`이 이 방식의 예시다.

참고한 원본: `LENS_4th_project/src/environment.py`,
`src/generation/llm.py`, `.env.example`. 3차 프로젝트 ZIP도 확인했으나 해당 .env.example에는 RunPod 설정이 없었다.
이전 프로젝트 코드는 수정하지 않았다.

## 웹 터미널에서 서버 준비

1. GPU Pod의 HTTP 포트에 `11434`를 지정하고, Connect에서 **Enable web terminal → Open web terminal**을 연다.
2. `scripts/runpod-ollama-proxy.py`를 Pod의 `/workspace/a4b_gateway.py`에,
   `scripts/setup-runpod-ollama.sh`를 `/workspace/setup-a4b.sh`에 복사한다.
3. 이번 서버 전용 키를 `A4B_GATEWAY_TOKEN` 환경변수로 전달한 후
   `bash /workspace/setup-a4b.sh`를 실행한다. 같은 키를 로컬 `.env`의 `A4B_LLM_API_KEY`에 넣는다.
4. 설치 로그는 `/workspace/setup-a4b.log`, 모델 다운로드 로그는
   `/workspace/pull-main.log`, `/workspace/pull-verifier.log`에서 확인한다.
5. 로컬에서 `PYTHONPATH=Backend Backend/.venv-agent/bin/python scripts/check-runpod.py`로
   인증 거절(401), 메인 응답, 검증기 응답을 확인한다. 결과는 `output/runpod-live-check.json`에 저장된다.

Ollama는 `127.0.0.1:11435`에서 실행되고, 외부 `11434`는 전용 Bearer 키가 필요한 게이트웨이가 받는다.
모델 다운로드·삭제 API는 게이트웨이에서 제공하지 않는다. RunPod 계정 관리 키와는 별개다.
현재 테스트는 영구 볼륨 없이 진행하므로 **Pod 중지 시 설치 파일과 모델은 사라진다**.
다시 사용할 때 서버 준비 절차를 반복하고 `.env`에 실제 Pod 주소와 해당 서버 키를 설정한다.

## 별도 선택: vLLM 서버를 사용하는 경우

아래는 선택적인 vLLM 설정이다. Ollama를 쓰면 설정할 필요 없다.
예시는 `.env.vllm-runpod.example`에 분리했다.


이 경로는 RunPod **Pod에 띄운 OpenAI 호환 vLLM 서버**용이다. RunPod Serverless의 /run, /runsync 호출은 지원하지 않는다.
Pod 생성·결제·원격 모델 배포는 이 코드가 수행하지 않는다.

## 설정 파일

저장소 루트 `.env`를 편집한다. 현재 파일은 로컬 Ollama 설정이며 실제 비밀키는 없다.
vLLM 설정 예시는 루트 `.env.vllm-runpod.example`이다. 아래 값들을 실제 값으로 바꾼다.

```dotenv
A4B_MAIN_PROVIDER=vllm
A4B_MAIN_BASE_URL=https://YOUR_MAIN_POD-8000.proxy.runpod.net/v1
A4B_MAIN_MODEL=Qwen/Qwen3-8B
A4B_MAIN_API_KEY=YOUR_VLLM_API_KEY

A4B_VERIFIER_MODE=shadow
A4B_VERIFIER_PROVIDER=vllm
A4B_VERIFIER_BASE_URL=https://YOUR_VERIFIER_POD-8000.proxy.runpod.net/v1
A4B_VERIFIER_MODEL=Qwen/Qwen3.5-4B
A4B_VERIFIER_API_KEY=YOUR_VERIFIER_VLLM_API_KEY
```

- BASE_URL은 해당 서버의 OpenAI 호환 API 루트이며 `/v1`을 포함한다.
- MODEL은 서버의 `GET /v1/models`가 반환한 이름과 일치해야 한다. 위 이름은 예시다.
- API_KEY는 **vLLM 서버의 인증키**다. RunPod 계정 관리용 API 키를 대신 넣지 않는다.
  자체 서버에서 인증을 설정하지 않았다면 이 값을 빈 값으로 둔다.
- 모델이 서로 다른 서버에서 제공되면 URL/키도 각각 다르다. 한 URL로 모델명을 바꾼다고 없는 모델이 추가되지 않는다.
- 메인만 RunPod로 쓰려면 검증기의 provider를 `ollama`, URL을 `http://127.0.0.1:11434`,
  모델을 `qwen3.5:4b`로 유지할 수 있다.
- 검증기는 기본 `shadow`로 기록만 한다. `enforce`는 보류·불일치 때 저장을 차단한다.

## 실행

저장소 루트에서:

```sh
sh scripts/run-local.sh
```

브라우저: http://127.0.0.1:3041/?screen=workflow
실행 중에 `.env`를 바꾸면 서버를 종료하고 다시 실행한다.
설계·문서 실행·이어서 실행·재시도·예약 경로에 같은 메인 연결 설정이 적용된다.
웹 화면에서 별도의 OpenAI 계정 키를 등록할 필요는 없다.

설정 우선순위는 이미 export된 환경변수 → `A4B_ENV_FILE`로 지정한 파일 또는 루트 `.env` → 기본값이다.
루트 `.env`가 없으면 Frontend/.env를 읽는다. `.env` 내용은 shell 코드로 실행하지 않는다.
실제 `.env`는 Git에서 제외되며 예시 파일만 공유한다.

## 확인 범위

환경파일 로딩·우선순위·Bearer 인증 헤더·vLLM 구조화 요청·응답 파싱·잘린 응답 차단을 오프라인 검사했다.
2026-10-21 A40(CA-MTL-1)에서 실제 연결을 확인했다. 인증 없는 요청은 401로 거절되고, 메인 qwen3:8b-q4_K_M과 검증기 qwen3.5:4b 모두 응답했다.
앱의 합성 CSV 시험은 메인 모델의 불필요한 재질문으로 needs_clarification에 머물렀으므로 업무 생성·실행 성공을 의미하지 않는다.
결과는 output/runpod-live-check.json과 output/runpod-app-smoke.json에 보존했다. 테스트 후 Pod 중지와 현재 요금 $0.00/hr를 확인했다. 임시 디스크이므로 재시작 시 모델·서버 재설치가 필요하다.
RunPod 프록시가 Python 기본 User-Agent를 403(1010)으로 거절해 앱 식별 헤더 A4B/0.1을 사용한다.
기존 패키지 원본은 integrations/qwen_verifier에 보존했고,
RunPod 지원 변경은 Backend/a4b_verifier/client.py에만 반영했다.

## 시간 제한과 오류 안내

메인 모델 호출은 `A4B_MAIN_TIMEOUT_S`(기본 180초, 1~600초),
원격 모델 설계 전체는 `A4B_DESIGN_TIMEOUT_MS`(기본 600000ms)로 제한한다.
여러 모델 턴과 별도 검증기 호출이 있으므로 두 제한을 같은 한 번의 요청 시간으로 취급하지 않는다.
디자인 제한은 최대 1800000ms이며 잘못된 값은 유한한 기본값을 사용한다.
조회 API의 기존 제한과 실행·재시도·하트비트 처리는 유지한다.
라이브러리가 API 오류를 하위 예외로 감싸도 타입 상속으로 시간 초과·인증·연결 오류를 구분한다.
이 설정이 모델의 의미 정확도나 업무 생성 성공을 보장하지는 않는다.

## 2026-10-21 연결 재확인

기존 Pod의 GPU 부족으로 새 CA-MTL-1 A40에서 연결만 재확인했다. 인증 없는 요청 401, 메인 모델 응답, 검증기 실제 응답이 통과했다. 로컬 `.env` 주소를 갱신하고 앱을 재시작했다. 테스트 후 Pod를 중지했으며 현재 요금은 $0.00/hr이다. 기능별 업무 생성·실행 성공을 뜻하지 않는다. 재사용 시 임시 디스크의 서버와 모델을 다시 준비해야 한다.
