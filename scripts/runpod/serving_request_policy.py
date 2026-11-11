"""Keep the two resident 9B runners compatible across native and OpenAI calls."""
import json
import urllib.request

from final_model_profile import SERVING_MODELS

SHIFT_RUNTIME_VERSION = '0.40.13-a4b-shift1'


def ensure_shift_support():
    with urllib.request.urlopen('http://127.0.0.1:11435/api/version', timeout=5) as response:
        version = json.load(response).get('version')
    if version != SHIFT_RUNTIME_VERSION:
        raise RuntimeError('Pinned serving policy requires the Ollama OpenAI shift extension')


def pin_serving_request(path, body):
    if path.split('?')[0] not in {'/api/chat', '/v1/chat/completions'}:
        return body
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError('Request body must be an object')
    model = payload.get('model', '')
    if not isinstance(model, str) or model.removesuffix(':latest') not in SERVING_MODELS:
        return body
    # In Ollama, shift changes the runner launch configuration, not just sampling.
    # The patched OpenAI adapter passes it through instead of silently dropping it.
    # Keep strict no-context-shift evaluation semantics for both public APIs.
    if payload.get('shift') is not None and payload['shift'] is not False:
        raise ValueError('These resident models require shift=false')
    payload['shift'] = False
    if payload.get('truncate') is not None and payload['truncate'] is not False:
        raise ValueError('These resident models require truncate=false')
    payload['truncate'] = False
    payload['keep_alive'] = -1
    if path.split('?')[0] == '/api/chat':
        options = payload.get('options')
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise ValueError('options must be an object')
        if options.get('num_ctx', 32768) != 32768:
            raise ValueError('These resident models require num_ctx=32768')
        # Other explicit runner settings can also evict a resident model. Reject
        # them rather than silently change the caller's requested configuration.
        launch_options = {'num_batch', 'num_gpu', 'main_gpu', 'num_thread',
                          'use_mmap', 'use_mlock', 'low_vram', 'numa',
                          'f16_kv', 'vocab_only', 'logits_all'}
        if launch_options.intersection(options):
            raise ValueError('Runner options are fixed for these resident models; use sampling options only')
        payload['options'] = {**options, 'num_ctx': 32768}
    return json.dumps(payload, ensure_ascii=False).encode()
