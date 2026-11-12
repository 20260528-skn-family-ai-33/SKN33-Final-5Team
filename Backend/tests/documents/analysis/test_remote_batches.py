"""Encoded payload boundaries and result contracts across remote OCR batches."""
import io
import json

import pytest

from documents.analysis import adapters
from documents.analysis.contracts import AnalysisError


@pytest.mark.parametrize('limit_delta,expected', [(0, [2, 1]), (-1, [1, 1, 1])])
def test_encoded_body_boundary_splits_and_merges_in_order(tmp_path, monkeypatch, limit_delta, expected):
    names = []
    for i in range(3):
        path = tmp_path / f'한글-{i}.png'
        path.write_bytes(bytes([i]) * 30)
        names.append(str(path))
    limit = len(adapters.remote_request_body(names[:2])) + limit_delta
    monkeypatch.setattr(adapters, 'REMOTE_MAX_BODY', limit)
    calls = []
    def send(request, timeout):
        assert len(request.data) <= limit
        sources = json.loads(request.data)['sources']
        calls.append(len(sources))
        assert 0 < timeout <= adapters.REMOTE_TIMEOUT
        return io.BytesIO(json.dumps(dict(version='fixture', outputs=[dict(source=s['name'], result={}) for s in sources])).encode())
    monkeypatch.setattr('urllib.request.urlopen', send)
    result = adapters.remote_engine('http://parser.invalid', 'paddleocr', names)
    assert calls == expected
    assert [entry['source'] for entry in result['outputs']] == names


def test_single_image_cannot_exceed_encoded_body_limit(tmp_path, monkeypatch):
    path = tmp_path / 'page.png'
    path.write_bytes(b'12345')
    body = adapters.remote_request_body([str(path)])
    monkeypatch.setattr(adapters, 'REMOTE_MAX_BODY', len(body) - 1)
    with pytest.raises(AnalysisError) as error:
        adapters.remote_engine('http://parser.invalid', 'paddleocr', [path])
    assert error.value.code == adapters.REMOTE_TOO_LARGE


def test_failed_later_batch_never_returns_partial_ocr_success(tmp_path, monkeypatch):
    import urllib.error
    path = tmp_path / 'page.png'
    path.write_bytes(b'123')
    monkeypatch.setattr(adapters, 'REMOTE_MAX_SOURCES', 1)
    calls = []
    def send(request, timeout):
        calls.append(request)
        if len(calls) == 2:
            raise urllib.error.HTTPError(request.full_url, 400, 'invalid', {}, None)
        return io.BytesIO(json.dumps(dict(version='fixture', outputs=[dict(source=str(path), result={})])).encode())
    monkeypatch.setattr('urllib.request.urlopen', send)
    with pytest.raises(AnalysisError) as error:
        adapters.remote_engine('http://parser.invalid', 'paddleocr', [path, path])
    assert error.value.code == 'remote_parser_rejected_400'


def test_actual_48_mib_limit_splits_three_max_size_images(tmp_path):
    names = []
    for i in range(3):
        path = tmp_path / f'page-{i}.png'
        # Sparse fixtures exercise the real 12 MiB image / 48 MiB encoded boundary.
        with path.open('wb') as output:
            output.truncate(adapters.REMOTE_MAX_IMAGE)
        names.append(str(path))
    batches = list(adapters.remote_request_batches(names))
    assert [len(batch) for batch, _ in batches] == [2, 1]
    assert all(len(body) <= 48 * 1024 * 1024 for _, body in batches)
