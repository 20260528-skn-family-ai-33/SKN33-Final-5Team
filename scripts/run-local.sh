#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUNDLED_NODE="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin"
if ! command -v node >/dev/null 2>&1; then PATH="$BUNDLED_NODE:$PATH"; export PATH; fi
cd "$ROOT/Frontend"
node node_modules/vite/bin/vite.js build
node_modules/.bin/esbuild server/launch.ts --bundle --platform=node --format=esm --packages=external --outfile=build-server/index.mjs
exec node "$ROOT/scripts/run-configured.mjs"
