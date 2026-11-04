#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../Backend"
python3 -m venv .venv-agent
.venv-agent/bin/python -m pip install -r requirements/agent-lock.txt
printf '\nDeep Agents 실행 환경 설치 완료. 프로젝트 루트에서 Node.js 24 환경으로 npm ci 및 npm run dev를 실행하세요.\n'
