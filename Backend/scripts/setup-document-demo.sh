#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv-docling
.venv-docling/bin/python -m pip install 'docling==2.133.0'
printf '\nDocling 텍스트 PDF 처리 환경 설치 완료. OCR은 지원하지 않습니다.\n'
