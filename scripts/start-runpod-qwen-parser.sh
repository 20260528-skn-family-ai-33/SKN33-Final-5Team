#!/usr/bin/env bash
# Run on container startup, after the image's SSH initialization.
set -euo pipefail
umask 077
if ! mountpoint -q /workspace; then
  echo "Mount the persistent volume at /workspace before starting." >&2
  exit 1
fi
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd /workspace

qwen=/workspace/a4b-qwen
parser=$repo/scripts/runpod/gpu_parser_server.py
parser_venv=/workspace/venv-gpu-parser
model_cache=/workspace/ollama-models
runtime=/run/a4b-runtime
python=$runtime/parser/bin/python
logs=/workspace/logs
auth_dir=${A4B_AUTH_DIR:-/run/a4b-auth}
gateway_key=$auth_dir/gateway-token
parser_key=$auth_dir/parser-api-key
for file in "$repo/scripts/runpod/runpod-ollama-proxy.py" "$parser"; do
  if [[ ! -s "$file" ]]; then
    echo "Missing deployment file: $file" >&2
    exit 1
  fi
done
for file in "$gateway_key" "$parser_key"; do
  if [[ ! -s "$file" || $(stat -c '%u:%a' "$file") != '0:600' ]]; then
    echo "Expected a nonempty root-owned authentication file with mode 600: $file" >&2
    exit 1
  fi
done
mkdir -p "$logs" "$qwen" "$model_cache/blobs" /workspace/paddlex
exec 9>/workspace/.a4b-start.lock
flock -n 9 || { echo "Qwen and parser are already managed by another launcher." >&2; exit 1; }

packages=()
if [[ ! -s "$parser_venv/pyvenv.cfg" || ! -d "$parser_venv/lib" ]]; then
  packages+=(python3-venv)
fi
ldconfig -p | awk '/libGL\.so\.1 / { found=1 } END { exit !found }' || packages+=(libgl1)
ldconfig -p | awk '/libglib-2\.0\.so\.0 / { found=1 } END { exit !found }' || packages+=(libglib2.0-0)
if [[ ! -x "$qwen/runtime/bin/ollama" ]]; then
  command -v zstd >/dev/null || packages+=(zstd)
fi
if (( ${#packages[@]} )); then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${packages[@]}"
fi
if [[ ! -s "$parser_venv/pyvenv.cfg" || ! -d "$parser_venv/lib" ]]; then
  # Build in the container filesystem so virtualenv symlinks work.
  staging=$(mktemp -d)
  (
    trap 'rm -rf -- "$staging"' EXIT
    /usr/bin/python3 -m venv "$staging/venv"
    "$staging/venv/bin/python" -m pip install --no-cache-dir \
      --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/ paddlepaddle-gpu==3.2.2
    "$staging/venv/bin/python" -m pip install --no-cache-dir -r "$repo/scripts/runpod/requirements-gpu-parser.txt"
    "$staging/venv/bin/python" -c 'import paddle; assert paddle.device.cuda.device_count() > 0'
    mkdir -p "$parser_venv"
    cp -R "$staging/venv/lib/." "$parser_venv/lib"
    cp "$staging/venv/pyvenv.cfg" "$parser_venv/pyvenv.cfg.new"
    mv -f "$parser_venv/pyvenv.cfg.new" "$parser_venv/pyvenv.cfg"
  )
fi
mkdir -p "$runtime/parser/bin" "$runtime/models"
cp "$parser_venv/pyvenv.cfg" "$runtime/parser/"
ln -sfnT "$parser_venv/lib" "$runtime/parser/lib"
ln -sfnT /usr/bin/python3 "$python"
ln -sfnT "$model_cache/blobs" "$runtime/models/blobs"

model_library=$runtime/models/manifests-v2/ollama.com/library
base_manifest=sha256-492e37dc69072c5a189a0cf21d32d844c983d599e4553a1e4d2ebd3f6b1299e2
custom_manifest=sha256-b6888395142551c6717f04d1ffa6ee4e9bd58540d9d91cebad65bda1d26af391
if [[ -s "$model_cache/manifests.tar" ]]; then
  tar --no-same-owner -xf "$model_cache/manifests.tar" -C "$runtime/models"
elif [[ -s "$model_cache/blobs/$base_manifest" && -s "$model_cache/blobs/$custom_manifest" ]]; then
  # Restore the manifests used by the existing Pod.
  mkdir -p "$model_library/"{qwen3.5,a4b-qwen35-4b-16k}
  ln -sfnT "../../../../blobs/$base_manifest" "$model_library/qwen3.5/4b"
  ln -sfnT "../../../../blobs/$custom_manifest" "$model_library/a4b-qwen35-4b-16k/latest"
fi

if [[ ! -x "$qwen/runtime/bin/ollama" ]]; then
  if [[ ! -s "$qwen/ollama.tar.zst" ]]; then
    curl -fsSL --retry 3 https://github.com/ollama/ollama/releases/download/v0.40.13/ollama-linux-amd64.tar.zst \
      -o "$qwen/ollama.tar.zst.new"
    mv -f "$qwen/ollama.tar.zst.new" "$qwen/ollama.tar.zst"
  fi
  (cd "$qwen"; sha256sum -c <<< 'c9490e0f520be21271d969e6703f012268b1d39f8b70dd58854e5bfef7a75bb6  ollama.tar.zst')
  staging=$(mktemp -d "$qwen/runtime.XXXXXX")
  tar --no-same-owner --zstd -xf "$qwen/ollama.tar.zst" -C "$staging"
  mv -T "$staging" "$qwen/runtime"
fi

pids=()
cleanup() {
  if (( ${#pids[@]} )); then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

export OLLAMA_HOST=127.0.0.1:11435 OLLAMA_MODELS=$runtime/models OLLAMA_NOPRUNE=1
export OLLAMA_NO_CLOUD=1 OLLAMA_CONTEXT_LENGTH=16384 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0
"$qwen/runtime/bin/ollama" serve >"$logs/ollama.log" 2>&1 &
pids+=("$!")
for ((attempt=0; attempt<60; attempt++)); do
  curl -fsS --max-time 2 "http://$OLLAMA_HOST/api/version" >/dev/null 2>&1 && break
  kill -0 "${pids[0]}"
  sleep 1
done
curl -fsS --max-time 2 "http://$OLLAMA_HOST/api/version" >/dev/null
ollama=$qwen/runtime/bin/ollama
model_cache_changed=false
if ! "$ollama" show qwen3.5:4b >/dev/null 2>&1; then
  "$ollama" pull qwen3.5:4b
  model_cache_changed=true
fi
if ! "$ollama" show a4b-qwen35-4b-16k >/dev/null 2>&1; then
  "$ollama" create a4b-qwen35-4b-16k -f "$repo/scripts/setup/ollama/Modelfile.a4b-qwen35-4b-16k"
  model_cache_changed=true
fi
if "$model_cache_changed" || [[ ! -s "$model_cache/manifests.tar" ]]; then
  # Archive metadata so symlinks survive on volumes that cannot store them.
  manifests=()
  for directory in manifests manifests-v2; do
    if [[ -d "$runtime/models/$directory" ]]; then
      manifests+=("$directory")
    fi
  done
  tar -cf "$model_cache/manifests.tar.new" -C "$runtime/models" "${manifests[@]}"
  mv -f "$model_cache/manifests.tar.new" "$model_cache/manifests.tar"
fi

A4B_GATEWAY_TOKEN=$(<"$gateway_key") python3 "$repo/scripts/runpod/runpod-ollama-proxy.py" >"$logs/gateway.log" 2>&1 &
pids+=("$!")
A4B_PARSER_API_KEY=$(<"$parser_key") PADDLE_PDX_CACHE_HOME=/workspace/paddlex \
  FLAGS_allocator_strategy=auto_growth "$python" "$parser" >"$logs/parser.log" 2>&1 &
pids+=("$!")
wait -n "${pids[@]}" || true
echo "A service exited. See $logs." >&2
exit 1
