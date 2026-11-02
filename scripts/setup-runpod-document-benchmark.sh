#!/usr/bin/env bash
# Run only inside the newly authorized Linux NVIDIA Pod, not on the local Mac.
set -euo pipefail
test "$(uname -s)" = Linux
test "$(uname -m)" = x86_64
nvidia-smi
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
mkdir -p output/runpod-setup data/document-runtime
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git python3-venv libgl1 libglib2.0-0 libvulkan1 vulkan-tools libreoffice-writer libreoffice-calc libreoffice-impress fonts-noto-cjk poppler-utils
if ! command -v uv >/dev/null; then
  python3 -m venv /workspace/a4b-bmt/bootstrap-tools
  /workspace/a4b-bmt/bootstrap-tools/bin/pip install --no-cache-dir uv
  export PATH="/workspace/a4b-bmt/bootstrap-tools/bin:$PATH"
fi
export UV_NATIVE_TLS=true
uv python install 3.13
for task_env in document-analysis agent docling mineru paddle bmt; do
  test -f "Backend/.venv-$task_env/pyvenv.cfg" || uv venv --python 3.13 "Backend/.venv-$task_env"
done
uv pip install --python Backend/.venv-document-analysis/bin/python -r Backend/requirements-document-analysis.txt
uv pip install --python Backend/.venv-agent/bin/python -r Backend/requirements-agent-lock.txt
uv pip install --python Backend/.venv-docling/bin/python -r Backend/requirements-parser.txt
uv pip install --python Backend/.venv-mineru/bin/python 'mineru[torch]==4.0.12' 'torch==2.14.1' 'torchvision==0.29.1' 'transformers==5.18.0'
uv pip install --python Backend/.venv-paddle/bin/python 'paddlepaddle-gpu==3.3.1' --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/
uv pip install --python Backend/.venv-paddle/bin/python 'paddleocr==3.7.0' 'paddlex[ocr]==3.7.2'
uv pip install --python Backend/.venv-bmt/bin/python PyMuPDF Pillow openpyxl python-docx python-pptx pyyaml requests certifi python-Levenshtein apted lxml beautifulsoup4 evaluate pandas numpy scipy tqdm func_timeout loguru pylatexenc python-dateutil price-parser chardet joblib multiset markdown-it-py tabulate mmeval pycocotools matplotlib
task_node_version=v22.16.0
if ! command -v node >/dev/null || ! node -e 'if(Number(process.versions.node.split(".")[0])<22)process.exit(1)'; then
  curl --fail --location --output /tmp/a4b-node.tar.xz "https://nodejs.org/dist/$task_node_version/node-$task_node_version-linux-x64.tar.xz"
  curl --fail --location --output /tmp/a4b-node-SHASUMS256.txt "https://nodejs.org/dist/$task_node_version/SHASUMS256.txt"
  TASK_NODE_VERSION="$task_node_version" python3 - <<'PY'
import hashlib, os
from pathlib import Path
name = 'node-' + os.environ['TASK_NODE_VERSION'] + '-linux-x64.tar.xz'
expected = dict(line.split()[::-1] for line in Path('/tmp/a4b-node-SHASUMS256.txt').read_text().splitlines())[name]
assert hashlib.sha256(Path('/tmp/a4b-node.tar.xz').read_bytes()).hexdigest() == expected
PY
  tar -xJf /tmp/a4b-node.tar.xz -C /usr/local --strip-components=1
fi
cd Frontend
npm ci --no-audit --no-fund
npm run build
cd "$task_root"
for task_env in document-analysis agent docling mineru paddle bmt; do
  uv pip freeze --python "Backend/.venv-$task_env/bin/python" > "output/runpod-setup/$task_env-freeze.txt"
done
nvidia-smi --query-gpu=name,uuid,driver_version,memory.total --format=csv > output/runpod-setup/gpu.csv
node --version > output/runpod-setup/node-version.txt
uname -a > output/runpod-setup/kernel.txt
printf '%s\n' 'RunPod 패키지 설치 완료. 모델 파일·원격 경로 설정과 실제 추론 검증은 별도로 수행하세요.'
