# 새 RunPod에서 Qwen과 GPU 파서 실행

아래는 기본 4B 설치 절차입니다. 이미 두 9B가 등록된 운영 Pod의 시작 워밍업과
DeepAgent 호출 설정은 [9B 문맥 설정 가이드](../../docs/ollama-context-shift.md)를 참고합니다.

1. RTX 4090 한 장과 CUDA 12.6을 지원하는 Ubuntu 24.04 기반 RunPod PyTorch 이미지를 선택합니다. 30GB 이상 영구 볼륨을 `/workspace`에 연결하고 HTTP 포트 `11434`(Qwen 게이트웨이), `8100`(파서)을 설정합니다. Ollama 내부 포트 `11435`는 루프백에서 사용합니다.
2. 이 변경이 포함된 커밋을 `/workspace/SKN33-Final-5Team`에 체크아웃합니다. 최초 부팅 중 SSH가 필요하면 먼저 이미지의 기본 시작 명령으로 Pod를 시작하고 체크아웃합니다.
3. RunPod의 Pod 환경 변수에 다음 두 Secret을 연결합니다. EC2 앱이 사용하는 게이트웨이 토큰 및 파서 키와 값이 같아야 합니다. 파서 키는 24자 이상이어야 합니다.

   | 변수 | 값 |
   | --- | --- |
   | `A4B_BOOT_GATEWAY` | `{{ RUNPOD_SECRET_a4b_jhd_gateway }}` |
   | `A4B_BOOT_PARSER` | `{{ RUNPOD_SECRET_a4b_jhd_parser }}` |

4. 이미지의 `/start.sh`가 `/post_start.sh`를 실행하는 RunPod PyTorch 템플릿에서는 아래 Docker Start Command를 사용합니다.

   ```bash
   bash -c '
     set -eu
     umask 077
     : "${A4B_BOOT_GATEWAY:?RunPod gateway secret required}"
     : "${A4B_BOOT_PARSER:?RunPod parser secret required}"
     if [[ "$A4B_BOOT_GATEWAY$A4B_BOOT_PARSER" == *"{{"* ]]; then
       echo "RunPod secret references were not resolved." >&2
       exit 1
     fi
     install -d -m 700 /run/a4b-auth
     printf "%s\n" "$A4B_BOOT_GATEWAY" > /run/a4b-auth/gateway-token
     printf "%s\n" "$A4B_BOOT_PARSER" > /run/a4b-auth/parser-api-key
     chmod 600 /run/a4b-auth/{gateway-token,parser-api-key}
     unset A4B_BOOT_GATEWAY A4B_BOOT_PARSER
     printf "%s\n" "exec bash /workspace/SKN33-Final-5Team/scripts/start-runpod-qwen-parser.sh" > /post_start.sh
     exec /start.sh
   '
   ```

최초 시작에는 Ollama, Qwen 가중치, Paddle GPU 의존성과 모델을 다운로드합니다. 다음 시작부터 `/workspace`의 캐시를 재사용하고, Secret은 `/run`에 다시 생성합니다. 설치·시작 상태는 Pod 로그와 `/workspace/logs/{ollama,gateway,parser}.log`에서 확인합니다.

시작 뒤 Pod 안에서 `curl -fsS http://127.0.0.1:8100/health`와 `curl -fsS http://127.0.0.1:11435/api/tags`로 서비스와 모델을 확인합니다. 새 Pod ID를 사용하면 EC2의 Qwen 및 파서 URL도 갱신한 뒤 워크플로우 설계·검증과 GPU 문서 분석을 실행합니다.
