import importlib.util
import io
import json
from pathlib import Path

import pytest


def load_warmup(monkeypatch):
    folder = Path(__file__).resolve().parents[3] / 'scripts/runpod'
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location('a4b_warmup', folder / 'warm-final.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('fault', [None, 'digest', 'context_length', 'size_vram', 'missing_model', 'inference'])
def test_warmup_uses_strict_policy_and_checks_both_models(monkeypatch, tmp_path, fault):
    warmup = load_warmup(monkeypatch)
    from final_model_profile import SERVING_MODELS
    models = dict(zip(SERVING_MODELS, ['a'*64, 'b'*64]))
    (tmp_path/'receipts').mkdir(); (tmp_path/'logs').mkdir()
    (tmp_path/'receipts/models-registered.json').write_text(json.dumps(models))
    calls = []
    def opener(request, timeout):
        if isinstance(request, str):
            loaded = [dict(name=name+':latest', digest=digest, size=100, size_vram=100,
                           context_length=32768) for name,digest in models.items()]
            if fault in ('digest', 'context_length', 'size_vram'):
                loaded[0][fault] = 'incorrect' if fault == 'digest' else 1
            if fault == 'missing_model': loaded.pop()
            return io.StringIO(json.dumps(dict(models=loaded)))
        assert request.full_url == 'http://127.0.0.1:11435/api/generate'
        payload = json.loads(request.data); calls.append(payload)
        assert payload['shift'] is payload['think'] is payload['truncate'] is False
        assert payload['keep_alive'] == -1 and payload['options']['num_ctx'] == 32768
        return io.StringIO(json.dumps({'done': fault != 'inference'}))
    if fault:
        with pytest.raises(RuntimeError): warmup.warm_models(tmp_path, opener)
        assert not (tmp_path/'logs/warmup-latest.json').exists()
    else:
        result = warmup.warm_models(tmp_path, opener)
        assert [c['model'] for c in calls] == list(models)
        assert result['shift'] is False
        assert json.loads((tmp_path/'logs/warmup-latest.json').read_text()) == result


def test_missing_registration_receipt_prevents_network_requests(monkeypatch, tmp_path):
    warmup = load_warmup(monkeypatch)
    def forbidden(*args, **kwargs): pytest.fail('unverified model must not be loaded')
    with pytest.raises(FileNotFoundError): warmup.warm_models(tmp_path, forbidden)
