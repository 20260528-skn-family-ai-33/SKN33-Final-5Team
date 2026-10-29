#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv-document-analysis
.venv-document-analysis/bin/python -m pip install -r requirements-document-analysis.txt
printf '\n문서 구조 리더 설치 완료. 엔진은 각각의 로컬 환경과 모델 경로를 설정하세요.\n'
printf '설정·검증 절차: docs/document-analysis-routing.md\n'
