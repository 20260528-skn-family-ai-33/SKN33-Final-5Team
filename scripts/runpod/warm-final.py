"""Load the two pinned 9B models with the same context policy as the backend."""
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
        request = urllib.request.Request('http://127.0.0.1:11435/api/generate', data=data,
                                         headers={'Content-Type': 'application/json'})
        with opener(request, timeout=900) as response:
            result = json.load(response)
        if not result.get('done') or result.get('error'):
            raise RuntimeError(f'{model}: warmup did not complete')
        print('Loaded', model, flush=True)
    with opener('http://127.0.0.1:11435/api/ps', timeout=10) as response:
        loaded = json.load(response)['models']
    inventory = {m['name'].removesuffix(':latest'): m for m in loaded}
    if set(inventory) != set(models):
        raise RuntimeError('Expected only the two 9B models to remain loaded')
    for name, digest in models.items():
        model = inventory[name]
        if model['digest'] != digest:
            raise RuntimeError(f'{name}: registered digest mismatch')
        if model['size_vram'] < model['size'] * 0.99:
            raise RuntimeError(f'{name}: CPU offload detected')
        if model.get('context_length') != 32768:
            raise RuntimeError(f'{name}: unexpected context length')
    result = {'seconds': time.monotonic() - started, 'shift': False, 'models': loaded}
    (base / 'logs/warmup-latest.json').write_text(json.dumps(result, indent=2) + '\n')
    print('Both 9B models loaded on GPU: context=32768, shift=false.', flush=True)
    return result


if __name__ == '__main__':
    warm_models()
