#!/bin/zsh
cd -- "$(dirname -- "$0")/.." || exit 1
if ! command -v node >/dev/null || ! command -v npm >/dev/null; then
  echo "Node.js 24를 설치한 후 다시 실행해주세요: https://nodejs.org/"
  read -r "?아무 키나 누르면 종료됩니다. "
  exit 1
fi
A4B_DEMO_PORT="$(node --env-file-if-exists=.env -e 'process.stdout.write(process.env.PORT||"3030")')" || exit 1
A4B_STATUS=$(curl -fsS --max-time 5 "http://127.0.0.1:${A4B_DEMO_PORT}/api/agent-builder/catalog" 2>/dev/null)
case "$A4B_STATUS" in
  *'"conversation":"deepagents"'*) open "http://127.0.0.1:${A4B_DEMO_PORT}"; exit 0 ;;
esac
if [ ! -d node_modules ]; then npm ci || exit 1; fi
if ! node --env-file-if-exists=.env --import tsx --input-type=module -e "import {agentPython} from './server/paths.ts';import {existsSync} from 'node:fs';process.exit(existsSync(agentPython())?0:1);"; then
  ../Backend/scripts/setup-agent-demo.sh || exit 1
fi
npm run build || exit 1
export PORT="$A4B_DEMO_PORT"
export A4B_FOCUSED_DEMO=1
echo "A4B 에이전트 생성 데모를 시작합니다. 종료하려면 Ctrl+C를 누르세요."
(sleep 2; open "http://127.0.0.1:${A4B_DEMO_PORT}") &
npm start
