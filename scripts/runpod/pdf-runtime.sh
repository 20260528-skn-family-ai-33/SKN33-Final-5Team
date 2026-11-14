#!/usr/bin/env bash
# Prepare once on local disk; restore the same environment after container replacement.
set -euo pipefail
umask 077
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$scripts/parser-paths.sh"
archive_dir=$A4B_BASE_DIR/runtime-archives
venv=$A4B_PDF_VENV
if [[ "$A4B_PDF_ROUTER_PYTHON" != "$venv/bin/python" || "$EASYFLOW_DOCLING_PYTHON" != "$venv/bin/python" ]]; then
  echo 'PDF worker and Docling must use the prepared local PDF environment.' >&2
  exit 1
fi
case "$(readlink -m "$venv")" in
  /workspace|/workspace/*) echo 'PDF runtime must be on local disk, outside /workspace.' >&2; exit 1 ;;
esac
mkdir -p "$archive_dir" "$(dirname -- "$venv")"
exec 8>"$archive_dir/pdf-runtime.lock"
flock 8
case "${1:-}" in
  prepare)
    rm -f -- "$venv/.a4b-runtime-complete"
    bash "$scripts/setup-pdf-router.sh"
    "$venv/bin/python" -c 'import torch; from docling.document_converter import DocumentConverter; assert torch.cuda.is_available()'
    "$venv/bin/python" -m pip freeze > "$venv/requirements.freeze.txt"
    printf '%s\n' "$venv" > "$venv/.a4b-runtime-prefix"
    # The prefix is kept fixed: venv console scripts contain absolute shebangs.
    tar -czf "$archive_dir/pdf.tar.gz.partial" --transform='flags=r;s,^\.,pdf,' -C "$venv" .
    mv -f "$archive_dir/pdf.tar.gz.partial" "$archive_dir/pdf.tar.gz"
    (cd "$archive_dir"; sha256sum pdf.tar.gz > pdf.tar.gz.sha256.partial; mv -f pdf.tar.gz.sha256.partial pdf.tar.gz.sha256)
    read -r digest _ < "$archive_dir/pdf.tar.gz.sha256"
    printf '%s\n' "$digest" > "$venv/.a4b-runtime-complete"
    ;;
  restore)
    (cd "$archive_dir"; sha256sum -c pdf.tar.gz.sha256)
    read -r digest _ < "$archive_dir/pdf.tar.gz.sha256"
    if [[ -x "$venv/bin/python" && -f "$venv/.a4b-runtime-complete" && "$(cat "$venv/.a4b-runtime-complete")" == "$digest" ]]; then
      exit 0
    fi
    stage=$(mktemp -d "$(dirname -- "$venv")/.pdf-restore.XXXXXX")
    trap 'rm -rf -- "$stage"' EXIT
    tar --no-same-owner -xzf "$archive_dir/pdf.tar.gz" -C "$stage"
    [[ "$(cat "$stage/pdf/.a4b-runtime-prefix")" == "$venv" ]]
    [[ -x "$stage/pdf/bin/python" ]]
    # Do not overlay a partial environment or silently delete an existing one.
    if [[ -e "$venv" ]]; then
      echo "Existing PDF environment has a different or incomplete archive: $venv. Stop services and move it aside before restoring." >&2
      exit 1
    fi
    printf '%s\n' "$digest" > "$stage/pdf/.a4b-runtime-complete"
    mv "$stage/pdf" "$venv"
    ;;
  *) echo 'Usage: pdf-runtime.sh prepare|restore' >&2; exit 2 ;;
esac
