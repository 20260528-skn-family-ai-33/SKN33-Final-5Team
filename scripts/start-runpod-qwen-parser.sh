#!/usr/bin/env bash
# Run on container startup, after the image's SSH initialization.
set -euo pipefail
umask 077
if ! mountpoint -q /workspace; then
  echo "Mount the persistent volume at /workspace before starting." >&2
  exit 1
fi
cd /workspace

qwen=/workspace/a4b-qwen
parser=/workspace/SKN33-Final-5Team/scripts/runpod/gpu_parser_server.py
parser_venv=/workspace/venv-gpu-parser
runtime=/run/a4b-runtime
python=$runtime/parser/bin/python
logs=/workspace/logs
auth_dir=${A4B_AUTH_DIR:-/run/a4b-auth}
gateway_key=$auth_dir/gateway-token
parser_key=$auth_dir/parser-api-key
for file in "$qwen/a4b_gateway.py" "$parser"; do
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
if [[ ! -s "$parser_venv/pyvenv.cfg" || ! -d /workspace/ollama-models/blobs || ! -d /workspace/paddlex ]]; then
  echo "Restore the parser environment and model caches before starting." >&2
  exit 1
fi
mkdir -p "$logs"
exec 9>/workspace/.a4b-start.lock
flock -n 9 || { echo "Qwen and parser are already managed by another launcher." >&2; exit 1; }

# The volume does not support ordinary Linux symlinks. Recreate the small
# directory structures in /run and keep the large files on the volume.
mkdir -p "$runtime/parser/bin" "$runtime/models/manifests-v2/ollama.com/library/"{qwen3.5,a4b-qwen35-4b-16k}
cp "$parser_venv/pyvenv.cfg" "$runtime/parser/"
ln -sfnT "$parser_venv/lib" "$runtime/parser/lib"
ln -sfnT /usr/bin/python3 "$python"
ln -sfnT /workspace/ollama-models/blobs "$runtime/models/blobs"
model_library=$runtime/models/manifests-v2/ollama.com/library
ln -sfnT ../../../../blobs/sha256-492e37dc69072c5a189a0cf21d32d844c983d599e4553a1e4d2ebd3f6b1299e2 "$model_library/qwen3.5/4b"
ln -sfnT ../../../../blobs/sha256-b6888395142551c6717f04d1ffa6ee4e9bd58540d9d91cebad65bda1d26af391 "$model_library/a4b-qwen35-4b-16k/latest"

packages=()
ldconfig -p | awk '/libGL\.so\.1 / { found=1 } END { exit !found }' || packages+=(libgl1)
ldconfig -p | awk '/libglib-2\.0\.so\.0 / { found=1 } END { exit !found }' || packages+=(libglib2.0-0)
if [[ ! -x "$qwen/runtime/bin/ollama" ]]; then
  command -v zstd >/dev/null || packages+=(zstd)
fi
if (( ${#packages[@]} )); then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${packages[@]}"
fi
if [[ ! -x "$qwen/runtime/bin/ollama" ]]; then
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
"$qwen/runtime/bin/ollama" show qwen3.5:4b >/dev/null
"$qwen/runtime/bin/ollama" show a4b-qwen35-4b-16k >/dev/null

A4B_GATEWAY_TOKEN=$(<"$gateway_key") python3 "$qwen/a4b_gateway.py" >"$logs/gateway.log" 2>&1 &
pids+=("$!")
A4B_PARSER_API_KEY=$(<"$parser_key") PADDLE_PDX_CACHE_HOME=/workspace/paddlex \
  FLAGS_allocator_strategy=auto_growth "$python" "$parser" >"$logs/parser.log" 2>&1 &
pids+=("$!")
wait -n "${pids[@]}" || true
echo "A service exited. See $logs." >&2
exit 1
