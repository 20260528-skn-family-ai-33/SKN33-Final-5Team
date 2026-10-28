#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv-docling/bin/python ]]; then
  docling_python="${A4B_DOCLING_SETUP_PYTHON:-}"
  if [[ -z "$docling_python" ]]; then
    docling_python="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)"
  fi
  "$docling_python" -c 'import sys; assert (3, 10) <= sys.version_info[:2] < (3, 14), "Docling 설치에는 Python 3.10~3.13을 지정하세요(A4B_DOCLING_SETUP_PYTHON)."'
  "$docling_python" -m venv .venv-docling
fi
.venv-docling/bin/python -m pip install -r requirements-parser.txt
printf '\n파서 환경 설치 완료. 모델은 별도 단계로 준비하세요:\n'
printf '.venv-docling/bin/python scripts/prepare_parser_models.py\n'
printf '출력된 OCR/Docling 캐시 경로를 저장소 루트 .env에 설정하세요.\n'
