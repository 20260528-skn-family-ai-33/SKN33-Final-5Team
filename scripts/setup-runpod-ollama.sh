#!/bin/bash
set -euo pipefail
: "${A4B_GATEWAY_TOKEN:?Supply the dedicated model gateway token}"
cd /workspace
command -v lspci >/dev/null || { apt-get update -qq; apt-get install -y -qq pciutils; }
command -v ollama >/dev/null || { curl -fsSL https://ollama.com/install.sh -o /workspace/install-ollama.sh; sh /workspace/install-ollama.sh; }
mkdir -p /workspace/ollama-models
export OLLAMA_HOST=127.0.0.1:11435
export OLLAMA_MODELS=/workspace/ollama-models
nohup ollama serve > /workspace/ollama.log 2>&1 &
for attempt in $(seq 1 60); do
 if curl -fsS http://127.0.0.1:11435/api/version >/dev/null; then break; fi
 sleep 1
done
curl -fsS http://127.0.0.1:11435/api/version
ollama pull qwen3:8b-q4_K_M > /workspace/pull-main.log 2>&1
ollama pull qwen3.5:4b > /workspace/pull-verifier.log 2>&1
nohup python3 /workspace/a4b_gateway.py > /workspace/gateway.log 2>&1 &
ollama list
printf '\nA4B setup complete\n'
