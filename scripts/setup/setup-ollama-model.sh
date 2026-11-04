#!/usr/bin/env bash
# Create the A4B baseline Ollama model (qwen3.5:4b + 16k context) on this machine.
# Run once per Ollama host (local PC, RunPod, ...). Existing models are not changed.
set -euo pipefail
cd "$(dirname "$0")"
NAME="${A4B_OLLAMA_MODEL_NAME:-a4b-qwen35-4b-16k}"
ollama pull qwen3.5:4b
ollama create "$NAME" -f ollama/Modelfile.a4b-qwen35-4b-16k
ctx=$(ollama show "$NAME" --modelfile | awk '$1=="PARAMETER" && $2=="num_ctx"{print $3}')
if [ "${ctx:-0}" -lt 16384 ]; then
  echo "num_ctx 확인 실패: ${ctx:-없음}" >&2
  exit 1
fi
printf '\n%s 생성 완료 (num_ctx=%s). .env의 A4B_LLM_MODEL=%s 로 설정하세요.\n' "$NAME" "$ctx" "$NAME"
