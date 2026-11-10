"""The historical three-model receipt must not re-enable optional 4B warmup."""
import io
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts' / 'runpod'
sys.path.insert(0, str(SCRIPTS))
from final_model_profile import SERVING_MODELS, required_models


def receipt():
    return {**{name: str(i + 1) * 64 for i, name in enumerate(SERVING_MODELS)},
            'qwen3.5:4b': '3' * 64}


def test_old_receipt_selects_only_two_9b_models(tmp_path):
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(receipt()))
    original = path.read_bytes()
    assert set(required_models(path)) == set(SERVING_MODELS)
    assert path.read_bytes() == original


@pytest.mark.parametrize('value', [None, '', 'bad-digest'])
def test_missing_or_invalid_9b_receipt_fails_closed(tmp_path, value):
    data = receipt()
    data[SERVING_MODELS[0]] = value
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='digest'):
        required_models(path)


def run_warmup(monkeypatch, *, extra=False, bad_digest=False):
    expected = {k: v for k, v in receipt().items() if k in SERVING_MODELS}
    calls = []
    monkeypatch.setattr('final_model_profile.required_models', lambda *a: expected)

    def urlopen(request, **kwargs):
        if isinstance(request, str):
            loaded = [dict(name=k + ':latest', digest=v, size=100, size_vram=100,
                           context_length=32768) for k, v in expected.items()]
            if extra:
                loaded.append(dict(name='qwen3.5:4b'))
            if bad_digest:
                loaded[0]['digest'] = '0' * 64
            return io.BytesIO(json.dumps({'models': loaded}).encode())
        body = json.loads(request.data)
        calls.append(body)
        assert body['model'] in SERVING_MODELS
        return io.BytesIO(b'{"done":true}')

    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    with patch.object(Path, 'write_text'):
        runpy.run_path(str(SCRIPTS / 'warm-final.py'), run_name='__main__')
    return calls


def test_warmup_never_calls_4b(monkeypatch):
    calls = run_warmup(monkeypatch)
    assert {c['model'] for c in calls} == set(SERVING_MODELS)
    assert all(c['options']['num_ctx'] == 32768 and c['keep_alive'] == -1 for c in calls)


@pytest.mark.parametrize('case', ['extra', 'bad_digest'])
def test_warmup_rejects_wrong_loaded_inventory(monkeypatch, case):
    with pytest.raises(AssertionError):
        run_warmup(monkeypatch, **{case: True})


def test_ready_does_not_require_4b_but_requires_parser(monkeypatch):
    expected = {k: v for k, v in receipt().items() if k in SERVING_MODELS}
    monkeypatch.setattr('final_model_profile.required_models', lambda: expected)
    parser_ok = True

    def urlopen(url, **kwargs):
        data = {'models': [dict(name=k + ':latest', digest=v) for k, v in expected.items()]}
        if url.endswith('/health'):
            data = dict(ok=parser_ok, device='gpu:0', pdf_jobs=True)
        return io.BytesIO(json.dumps(data).encode())

    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    runpy.run_path(str(SCRIPTS / 'check-final-ready.py'), run_name='__main__')
    parser_ok = False
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(SCRIPTS / 'check-final-ready.py'), run_name='__main__')
    assert error.value.code == 1
