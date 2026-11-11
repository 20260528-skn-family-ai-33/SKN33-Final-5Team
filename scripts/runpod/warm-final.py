"""Load only the two pinned 9B models before opening the public gateway."""
import json
import time
import urllib.request
from pathlib import Path
from final_model_profile import required_models

base = Path('/workspace/a4b')
models = required_models(base / 'receipts/models-registered.json')
started = time.monotonic()
for model in models:
    data = json.dumps({'model': model, 'stream': False, 'keep_alive': -1, 'shift': False, 'truncate': False,
                       'options': {'num_ctx': 32768}}).encode()
    req = urllib.request.Request('http://127.0.0.1:11435/api/generate', data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=900) as response:
        result = json.load(response)
    assert result.get('done') and not result.get('error'), result
    print('Loaded', model, flush=True)
with urllib.request.urlopen('http://127.0.0.1:11435/api/ps', timeout=10) as response:
    loaded = json.load(response)['models']
inventory = {m['name'].removesuffix(':latest'): m for m in loaded}
assert set(inventory) == set(models), 'Expected only the two 9B models to remain loaded'
for name in models:
    m = inventory[name]
    assert m['digest'] == models[name], f'{name}: registered digest mismatch'
    assert m['size_vram'] >= m['size'] * 0.99, f'{name}: CPU offload detected'
    assert m.get('context_length') == 32768, f'{name}: unexpected context length'
result = {'seconds': time.monotonic() - started, 'models': loaded}
(base / 'logs/warmup-latest.json').write_text(json.dumps(result, indent=2) + '\n')
print('Both 9B models loaded on GPU at 32768-token context; no 4B warmup.', flush=True)
