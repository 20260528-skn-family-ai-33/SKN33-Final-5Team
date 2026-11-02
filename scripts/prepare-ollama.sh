#!/usr/bin/env sh
# Prepare the host Ollama used by the Docker app (macOS; Linux notes below).
# Sets the environment Ollama needs, then pulls both models. Restart the Ollama app afterwards.
set -eu
command -v ollama >/dev/null 2>&1 || { echo 'Ollama가 없습니다. https://ollama.com/download 에서 설치하세요.'; exit 1; }
if [ "$(uname)" = Darwin ]; then
  launchctl setenv OLLAMA_CONTEXT_LENGTH 16384
  launchctl setenv OLLAMA_NUM_PARALLEL 1
  launchctl setenv OLLAMA_FLASH_ATTENTION 1
  launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0
  launchctl setenv OLLAMA_KEEP_ALIVE -1
  echo 'Ollama 앱을 종료했다가 다시 실행한 뒤 Enter를 누르세요.'; read _
else
  # Linux (systemd): the container reaches the host through the Docker bridge, so Ollama must
  # listen beyond 127.0.0.1. Add these lines with: sudo systemctl edit ollama
  #   [Service]
  #   Environment=OLLAMA_HOST=0.0.0.0 OLLAMA_CONTEXT_LENGTH=16384 OLLAMA_NUM_PARALLEL=1
  #   Environment=OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_KEEP_ALIVE=-1
  echo 'Linux는 스크립트 주석의 systemd 설정을 적용하고 Ollama를 재시작하세요.'
fi
ollama pull qwen3:8b-q4_K_M
ollama pull qwen3.5:4b
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ollama create a4b-qwen35-4b-16k -f "$script_dir/../Backend/scripts/ollama/Modelfile.a4b-qwen35-4b-16k"
ollama list
echo 'Ollama 준비 완료. 저장소 루트에서 docker compose up -d 를 실행하세요.'
