"""Load the same GGUF weights and generate text with explicit Vulkan (no fallback)."""
import json
import os
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[1]
allowed = {'VK_DRIVER_FILES', 'XDG_RUNTIME_DIR', 'MINERU_LLAMA_CPP_BACKEND', 'MINERU_HOME'}
for line in (root / '.env').read_text().splitlines():
    if '=' in line:
        key, value = line.split('=', 1)
        if key in allowed:
            os.environ[key] = value
assert os.environ.get('MINERU_LLAMA_CPP_BACKEND') == 'vulkan', 'CPU fallback를 허용하지 않습니다.'
from mineru_llama_cpp import Engine, LOG_LEVEL_INFO
folder = Path(os.environ['MINERU_HOME']) / 'models/MinerU2.5-Pro-2605-1.2B-GGUF'
files = [path for path in folder.glob('*.gguf') if not path.name.startswith('._')]
main = next(path for path in files if not path.name.startswith('mmproj'))
projector = next(path for path in files if path.name.startswith('mmproj'))
started = time.perf_counter()
with Engine(main, projector, n_ctx_seq=2048, n_parallel=1, verbosity=LOG_LEVEL_INFO) as engine:
    generated = engine.generate([{'role': 'user', 'content': 'Reply only OK.'}])
    result = dict(explicit_backend='vulkan', model=main.name, projector=projector.name,
                  elapsed_seconds=time.perf_counter() - started, generated_text=generated.content,
                  gpu=subprocess.check_output(['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.used', '--format=csv'], text=True))
(root / 'output/runpod-setup/mineru-vulkan-health.json').write_text(json.dumps(result, indent=2) + '\n')
print('PASS_EXPLICIT_VULKAN', json.dumps(result), flush=True)
