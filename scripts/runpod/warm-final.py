"""Load only the two pinned 9B models before opening the public gateway."""
import json
import time
import urllib.request
from pathlib import Path

from final_model_profile import required_models


def warm_models(base=Path('/workspace/a4b'), opener=urllib.request.urlopen):
    models = required_models(base / 'receipts/models-registered.json')
    started = time.monotonic()
    for model in models:
        data = json.dumps({'model': model, 'stream': False, 'keep_alive': -1,
                           'think': False, 'shift': False, 'truncate': False,
                           'options': {'num_ctx': 32768}}).encode()
        req = urllib.request.Request('http://127.0.0.1:11435/api/generate', data=data,
                                     headers={'Content-Type': 'application/json'})
        with opener(req, timeout=900) as response:
            result = json.load(response)
        if not result.get('done') or result.get('error'):
            raise RuntimeError(f'{model}: warmup did not complete')
        print('Loaded', model, flush=True)
    with opener('http://127.0.0.1:11435/api/ps', timeout=10) as response:
        loaded = json.load(response)['models']
    inventory = {m['name'].removesuffix(':latest'): m for m in loaded}
    if set(inventory) != set(models):
        raise RuntimeError('Expected only the two 9B models to remain loaded')
    for name in models:
        m = inventory[name]
        if m['digest'] != models[name]:
            raise RuntimeError(f'{name}: registered digest mismatch')
        if m['size_vram'] < m['size'] * 0.99:
            raise RuntimeError(f'{name}: CPU offload detected')
        if m.get('context_length') != 32768:
            raise RuntimeError(f'{name}: unexpected context length')
    result = {'seconds': time.monotonic() - started, 'shift': False, 'models': loaded}
    (base / 'logs/warmup-latest.json').write_text(json.dumps(result, indent=2) + '\n')
    print('Both 9B models loaded on GPU at 32768-token context; no 4B warmup.', flush=True)
    return result


if __name__ == '__main__':
    warm_models()
