#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export A4B_FOCUSED_DEMO=1
if [[ ! -d node_modules ]]; then npm ci; fi
if ! node --env-file-if-exists=.env --import tsx --input-type=module -e "import {agentPython} from './server/paths.ts';import {existsSync} from 'node:fs';process.exit(existsSync(agentPython())?0:1);"; then
 printf '먼저 ../Backend/scripts/setup-agent-demo.sh를 실행하세요.\n'; exit 1
fi
npm run dev
