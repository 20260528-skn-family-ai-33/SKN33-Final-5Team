#!/usr/bin/env bash
# RunPod /post_start.sh entry point. Everything durable lives on the network disk.
set -euo pipefail
umask 077
base=/workspace/a4b
mountpoint -q /workspace
mkdir -p "$base/logs" /run/a4b-final
exec 9>/run/a4b-final/start.lock
flock -n 9 || exit 0
rm -f /run/a4b-final/ready
exec >>"$base/logs/startup.log" 2>&1
echo "$(date -u +%FT%TZ) starting a4b-final-gpu"
until [[ -f "$base/receipts/models-registered.json" && -f "$base/receipts/pdf-prepared" && -f "$base/receipts/parser-prepared" && -f "$base/receipts/runtimes-packed" ]]; do sleep 5; done
mkdir -p /opt/a4b-envs
for runtime in parser mineru; do
  if [[ ! -f "/opt/a4b-envs/$runtime/.a4b-runtime-complete" ]]; then
    (cd "$base/runtime-archives" && sha256sum -c "$runtime.tar.gz.sha256")
    tar --no-same-owner -xzf "$base/runtime-archives/$runtime.tar.gz" -C /opt/a4b-envs
    touch "/opt/a4b-envs/$runtime/.a4b-runtime-complete"
  fi
done
[[ -s /run/a4b-auth/gateway-token && -s /run/a4b-auth/parser-api-key ]]
export A4B_GATEWAY_TOKEN
export A4B_PARSER_API_KEY
A4B_GATEWAY_TOKEN=$(cat /run/a4b-auth/gateway-token)
A4B_PARSER_API_KEY=$(cat /run/a4b-auth/parser-api-key)
[[ ${#A4B_GATEWAY_TOKEN} -ge 24 && ${#A4B_PARSER_API_KEY} -ge 24 ]]

# RunPod recreates the container root on stop/start; Python environments and
# model files stay on /workspace. Only small OS libraries may need restoration.
if ! dpkg-query -W -f='${Status}' libgl1 2>/dev/null | grep -q 'install ok installed'; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libgl1 libglib2.0-0
fi
export OLLAMA_HOST=127.0.0.1:11435
export OLLAMA_MODELS=$base/models/ollama
export OLLAMA_NUM_PARALLEL=1
# Only the two fine-tuned 9B roles are warmed; optional 4B review is disabled.
export OLLAMA_MAX_LOADED_MODELS=2
export OLLAMA_CONTEXT_LENGTH=32768
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=f16
export OLLAMA_KEEP_ALIVE=-1
export A4B_PARSER_PORT=8100
export A4B_PADDLE_DEVICE=gpu:0
export PADDLE_PDX_CACHE_HOME=$base/models/paddle
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
export FLAGS_allocator_strategy=auto_growth
export A4B_PDF_ROUTER_PYTHON=$base/envs/pdf/bin/python
export A4B_PDF_ARTIFACTS_DIR=$base/models/pdf/docling
export A4B_PDF_OCR_MODEL_DIR=$base/models/pdf/docling/EasyOcr
export A4B_MINERU_PYTHON=/opt/a4b-envs/mineru/bin/python
# FlashInfer invokes Ninja by name when compiling GPU kernels on a fresh host.
export PATH=/opt/a4b-envs/mineru/bin:/usr/local/cuda/bin:$PATH
export MINERU_HOME=$base/models/mineru
export HF_HOME=$base/cache/mineru-hf
export TORCH_HOME=$base/cache/torch
export TRITON_CACHE_DIR=$base/cache/triton
export VLLM_CACHE_ROOT=$base/cache/vllm
export MINERU_MODEL_SMALL_BACKEND=torch
export MINERU_MODEL_VLM_ENGINE=vllm
export PYTHONUNBUFFERED=1

pids=()
all_alive() {
  # `kill -0 pid1 pid2` succeeds if either PID exists; require every service.
  local pid
  for pid in "$@"; do
    kill -0 "$pid" 2>/dev/null || return 1
  done
}
cleanup() {
  rm -f /run/a4b-final/ready
  if ((${#pids[@]})); then
    for pid in "${pids[@]}"; do kill -- "-$pid" 2>/dev/null || true; done
    wait "${pids[@]}" 2>/dev/null || true
  fi
  pids=()
}
trap 'cleanup; exit 0' TERM INT
trap cleanup EXIT
while true; do
  setsid "$base/ollama/bin/ollama" serve 9>&- >>"$base/logs/ollama.log" 2>&1 &
  pids+=("$!")
  setsid /opt/a4b-envs/parser/bin/python "$base/app/legacy-pdf/scripts/runpod/gpu_parser_server.py" 9>&- >>"$base/logs/parser.log" 2>&1 &
  pids+=("$!")
  ready=0
  for ((attempt=0; attempt<180; attempt++)); do
    if ! all_alive "${pids[@]}"; then break; fi
    if python3 "$base/app/scripts/runpod/check-final-ready.py"; then ready=1; break; fi
    sleep 2
  done
  if ((ready)); then
    # Track warmup in its own process group so cleanup can cancel model requests
    # along with required services instead of waiting for their 900s timeout.
    setsid python3 "$base/app/scripts/runpod/warm-final.py" 9>&- &
    warm_pid=$!
    pids+=("$warm_pid")
    # Watch required services during warmup too. A bounded poll also handles
    # children that exit before waiting starts; wait below collects warmup status.
    while all_alive "${pids[@]}"; do
      sleep .2
    done
    if all_alive "${pids[0]}" "${pids[1]}" && wait "$warm_pid"; then
      pids=("${pids[@]:0:2}")
      setsid python3 "$base/app/scripts/runpod/runpod-ollama-proxy.py" 9>&- >>"$base/logs/gateway.log" 2>&1 &
      pids+=("$!")
      gateway_ready=0
      for ((attempt=0; attempt<60; attempt++)); do
        if ! all_alive "${pids[@]}"; then break; fi
        if python3 "$base/app/scripts/runpod/check-gateway-ready.py"; then gateway_ready=1; break; fi
        sleep 2
      done
      if ((gateway_ready)) && all_alive "${pids[@]}"; then
        date -u +%FT%TZ > /run/a4b-final/ready
        echo "$(date -u +%FT%TZ) authenticated model gateway and GPU parser ready"
        wait -n "${pids[@]}" || true
      fi
    fi
  fi
  cleanup
  echo "$(date -u +%FT%TZ) restarting services after failure"
  sleep 5
done
