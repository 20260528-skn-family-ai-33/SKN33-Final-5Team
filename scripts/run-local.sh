#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js를 설치하고 PATH에 추가해 주세요." >&2
  exit 1
fi
cd "$ROOT/Frontend"
node ../node_modules/vite/bin/vite.js build
cd "$ROOT"
node_modules/.bin/esbuild Backend/api/src/launch.ts --bundle --platform=node --format=esm --packages=external --outfile=Backend/api/build-server/index.mjs
exec node "$ROOT/scripts/run-configured.mjs"
