#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export A4B_FOCUSED_DEMO=1
export PORT="${PORT:-3030}"
if [[ ! -x .venv-agent/bin/python ]]; then printf '먼저 ./setup-agent-demo.sh를 실행하세요.\n'; exit 1; fi
npm run dev
