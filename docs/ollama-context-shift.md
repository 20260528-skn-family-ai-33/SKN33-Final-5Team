# 9B 호출과 시작 워밍업의 문맥 설정

Ollama 0.40.13의 `/v1/chat/completions`는 `shift`를 native 요청으로 전달하지 않습니다.
Qwen3.5에서 생략된 값은 context shifting을 켜며, native `shift=false` 호출과
교대로 사용하면 같은 가중치도 runner를 다시 로딩할 수 있습니다.

이 변경은 Ollama용 `ChatOpenAI`의 HTTP 경계만 `/api/chat`으로 변환합니다.
DeepAgent의 도구 선택·도구 실행·구조화 응답 검사·출력 길이 재시도는 유지합니다.
OpenAI와 vLLM 연결은 이 변환기를 사용하지 않습니다. 기존 AWS 주소의 `/v1` 접미사는
그대로 두면 됩니다. 변환기가 같은 호스트의 `/api/chat`을 호출합니다.

- 모든 로컬 Ollama 호출: `think=false`, `shift=false`, `truncate=false`.
- DeepAgent: `A4B_OLLAMA_NUM_CTX`를 native `options.num_ctx`로 전달하고,
  요청별 출력 한도와 배포 상한을 유지합니다. 문맥 초과는 자동으로 잘라 숨기지 않습니다.
- 현재 두 9B 프로필: 문맥 32768, 모델 상주 `keep_alive=-1`.
- 시작 워밍업: `scripts/runpod/warm-final.py`가 두 모델을 같은 설정으로 로딩하고,
  등록 영수증의 digest·GPU 적재·문맥 길이를 검사합니다.

## 기존 9B Pod에 적용

기존 `/workspace/a4b/app/scripts/runpod/start-final.sh`는 공개 게이트웨이를 열기 전에
`warm-final.py`를 실행합니다. 해당 시작 코드는 별도 운영 브랜치에서 관리되고 있으며,
이 PR은 supervisor 전체를 변경하지 않습니다. 기존 시작 명령이 이 파일을 실행하는지 먼저 확인합니다.

1. 기존 `warm-final.py`와 `final_model_profile.py`를 백업합니다.
2. 이 PR의 두 파일을 `/workspace/a4b/app/scripts/runpod/`에 배치합니다.
3. `/workspace/a4b/receipts/models-registered.json`에 previous/final664의 검증된 digest가 있는지 확인합니다.
4. 내부 Ollama(11435)가 준비된 뒤 `python3 /workspace/a4b/app/scripts/runpod/warm-final.py`를 실행합니다.
5. 백엔드도 이 PR의 이미지로 배포해야 DeepAgent 호출까지 같은 설정이 됩니다.

기본 4B 설치 절차에는 이 두 9B 전용 워밍업을 연결하지 않습니다.
모델 원본·등록 영수증·네트워크 볼륨·API 인증 정책은 이 변경의 대상이 아닙니다.

## 재로딩 검증

처음 설정을 맞추는 동안에는 기존 runner가 한 번 교체될 수 있습니다. 워밍업이 끝난
뒤 두 모델의 `/api/ps` 정보와 runner PID·시작 시각을 기록하고, 지원 판정과
9B DeepAgent 호출을 반복한 후 비교합니다. `/api/ps`에 이름이 있다는 사실만으로
재로딩이 없었다고 판단하지 않습니다. 같은 구간의 Ollama `starting llama-server`
로그도 증가하지 않아야 합니다.

이 수정은 외부 팀원의 `/v1` 호출까지 바꾸지 않습니다. 공용 Pod에서 기존 방식의
호출이 섞이면 재로딩이 재발할 수 있습니다. 또한 Pod 재시작·메모리 부족·서비스
종료·다른 문맥 설정으로 인한 로딩, 네트워크 디스크의 첫 로딩 시간까지 제거하지 않습니다.

근거: [Ollama 0.40.13 OpenAI 변환](https://github.com/ollama/ollama/blob/v0.40.13/openai/openai.go),
[runner 재사용 검사](https://github.com/ollama/ollama/blob/v0.40.13/server/sched.go).
