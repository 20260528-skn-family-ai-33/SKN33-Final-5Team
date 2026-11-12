#!/usr/bin/env bash
# Prepare the dedicated Docling worker on a RunPod PyTorch image.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
venv=${A4B_PDF_VENV:-/opt/a4b-pdf-router/venv}
models=${A4B_PDF_MODELS:-/workspace/a4b-pdf-models}
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
