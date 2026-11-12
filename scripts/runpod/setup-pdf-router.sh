#!/usr/bin/env bash
# Prepare the dedicated Docling worker on a RunPod PyTorch image.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/parser-paths.sh"
repo=$A4B_PARSER_SOURCE_ROOT
venv=$A4B_PDF_VENV
models=$A4B_PDF_MODELS
# The downloader creates docling/EasyOcr beneath --output. Reject inconsistent
# overrides before installing into a location the server would never use.
if [[ "$A4B_PDF_ROUTER_PYTHON" != "$venv/bin/python" || "$A4B_PDF_ARTIFACTS_DIR" != "$models/docling" || "$A4B_PDF_OCR_MODEL_DIR" != "$models/docling/EasyOcr" ]]; then
  echo 'Set A4B_PDF_VENV and A4B_PDF_MODELS consistently with the PDF runtime paths.' >&2
  exit 1
fi
mkdir -p "$(dirname "$venv")" "$models"
if [[ ! -x "$venv/bin/python" ]]; then
  python -m venv --system-site-packages "$venv"
fi
# Retain the image's CUDA Torch/torchvision pair instead of resolving CPU wheels.
"$venv/bin/python" - <<'PY' >"$venv/cuda-constraints.txt"
from importlib.metadata import version
import torch
assert torch.cuda.is_available(), 'A CUDA-enabled RunPod PyTorch image is required'
for package in ('torch', 'torchvision'):
    print(f'{package}=={version(package)}')
PY
"$venv/bin/python" -m pip install --disable-pip-version-check -c "$venv/cuda-constraints.txt" \
  'docling==2.133.0' 'docling-core==2.99.0' 'easyocr==1.7.2' 'pymupdf==2.0' \
  'beautifulsoup4==4.15.0' 'markdown-it-py==4.2.0'
if [[ ! -s "$models/.docling-2.133.0-ready" || ! -d "$models/docling/EasyOcr" ]]; then
  "$venv/bin/python" "$repo/scripts/setup/prepare_parser_models.py" --output "$models"
  printf '%s\n' ready > "$models/.docling-2.133.0-ready"
fi
