#!/usr/bin/env bash
# The manager prepares immutable generations and restores them before services start.
set -euo pipefail
umask 077
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$scripts/parser-paths.sh"
exec python3 "$scripts/pdf_runtime.py" "${1:-}"
