#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv-docling
.venv-docling/bin/python -m pip install -r requirements-parser.txt
printf '\n파서 환경 설치 완료. 모델은 별도 단계로 준비하세요:\n'
printf '.venv-docling/bin/python scripts/prepare_parser_models.py\n'
printf '출력된 OCR/Docling 캐시 경로를 저장소 루트 .env에 설정하세요.\n'
