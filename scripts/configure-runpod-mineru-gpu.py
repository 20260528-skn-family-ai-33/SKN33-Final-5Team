"""Use NVIDIA's headless EGL Vulkan ICD; never install/change a host driver."""
import json
import os
from pathlib import Path
import platform
import subprocess

assert platform.system() == 'Linux', '새 RunPod 내부에서만 실행하세요.'
root = Path(__file__).resolve().parents[1]
library = Path('/lib/x86_64-linux-gnu/libEGL_nvidia.so.0')
assert library.is_file(), '컨테이너에 NVIDIA EGL 드라이버 라이브러리가 필요합니다.'
runtime = root / 'data/document-runtime'
runtime.mkdir(parents=True, exist_ok=True)
icd = runtime / 'nvidia-egl-icd.json'
existing = Path('/etc/vulkan/icd.d/nvidia_icd.json')
api = json.loads(existing.read_text())['ICD']['api_version'] if existing.exists() else '1.3.0'
icd.write_text(json.dumps(dict(file_format_version='1.0.0', ICD=dict(library_path=str(library), api_version=api)), indent=2) + '\n')
xdg = runtime / 'xdg'
xdg.mkdir(mode=0o700, exist_ok=True)
settings = dict(VK_DRIVER_FILES=str(icd), XDG_RUNTIME_DIR=str(xdg), MINERU_LLAMA_CPP_BACKEND='vulkan')
result = subprocess.run(['vulkaninfo', '--summary'], env={**os.environ, **settings}, capture_output=True, text=True, timeout=30)
output = root / 'output/runpod-setup'
output.mkdir(parents=True, exist_ok=True)
(output / 'vulkan-egl-validated.txt').write_text(result.stdout + result.stderr)
assert result.returncode == 0 and 'NVIDIA L40S' in result.stdout and 'DISCRETE_GPU' in result.stdout, result.stdout + result.stderr
env_file = root / '.env'
lines = env_file.read_text().splitlines() if env_file.exists() else []
lines = [line for line in lines if line.split('=', 1)[0] not in settings]
env_file.write_text('\n'.join(lines) + '\n' + ''.join(key + '=' + value + '\n' for key, value in settings.items()))
print('PASS: NVIDIA L40S Vulkan ICD via EGL; explicit Vulkan selected, CPU fallback disabled.')
