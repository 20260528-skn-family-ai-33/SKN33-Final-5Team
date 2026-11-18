import io
import json
import urllib.error
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

import pytest

from documents.analysis import remote_docling
from documents.analysis.adapters import LocalEngines
from documents.analysis.contracts import AnalysisError, sha256


def reply(status, body):
    stream = io.BytesIO(json.dumps(body).encode())
    stream.status = status
    return stream


@pytest.mark.parametrize('bad_hash', [False, True])
def test_polling_binds_crop_and_cancels_invalid_result(tmp_path, monkeypatch, bad_hash):
    source = tmp_path / 'crop.png'
    source.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    job = str(uuid4())
    result = dict(engine='docling', source_sha256='wrong' if bad_hash else sha256(source),
                  version='test', blocks=[], model={'device': 'cuda'})
    responses = [reply(202, {'job_id': job}), reply(202, {}), reply(200, result), reply(200, {})]
    requests = []
    monkeypatch.setenv('A4B_PARSER_URL', 'https://parser.example')
    monkeypatch.setenv('A4B_PARSER_API_KEY', 'fixture-key')
    def send(request, timeout):
        requests.append(request)
        return responses.pop(0)
    with patch('urllib.request.build_opener') as opener, patch('time.sleep'):
        opener.return_value.open.side_effect = send
        if bad_hash:
            with pytest.raises(AnalysisError, match='계약'):
                remote_docling.parse_remote_image(source)
            assert requests[-1].method == 'DELETE'
        else:
            assert remote_docling.parse_remote_image(source) == result
            assert len(requests) == 3
    assert requests[0].data == source.read_bytes()
    assert all(r.get_header('Authorization') == 'Bearer fixture-key' for r in requests)
    assert requests[1].full_url.endswith('/v1/docling-image/' + job)


def test_png_supplement_uses_remote_without_local_docling(tmp_path, monkeypatch):
    source = tmp_path / 'crop.png'
    source.write_bytes(b'fixture')
    monkeypatch.setenv('A4B_PARSER_URL', 'https://parser.example')
    monkeypatch.delenv('A4B_DOCLING_LOCAL_ONLY', raising=False)
    result = dict(version='test', model={'device': 'cuda'}, blocks=[], partial=True)
    engines = LocalEngines(tmp_path / 'attempts')
    with patch.object(remote_docling, 'parse_remote_image', return_value=result), patch('subprocess.run') as local:
        assert engines.run('docling', source=source, phase='supplement') == result
    local.assert_not_called()
    assert engines.attempts[0]['route'] == 'gpu_remote'
    assert engines.attempts[0]['status'] == 'partial'


def test_remote_failure_is_not_hidden_by_missing_cpu_dependency(tmp_path, monkeypatch):
    source = tmp_path / 'crop.png'
    source.write_bytes(b'fixture')
    monkeypatch.setenv('A4B_PARSER_URL', 'https://parser.example')
    monkeypatch.delenv('A4B_DOCLING_LOCAL_ONLY', raising=False)
    engines = LocalEngines(tmp_path / 'attempts')
    with patch.object(remote_docling, 'parse_remote_image', side_effect=AnalysisError('timeout', 'timeout')), patch('subprocess.run') as local:
        with pytest.raises(AnalysisError):
            engines.run('docling', source=source)
    local.assert_not_called()
    assert engines.attempts[0]['error_code'] == 'timeout'


def test_pod_local_only_does_not_reenter_shared_queue(tmp_path, monkeypatch):
    source = tmp_path / 'crop.png'
    source.write_bytes(b'fixture')
    monkeypatch.setenv('A4B_PARSER_URL', 'https://parser.example')
    monkeypatch.setenv('A4B_DOCLING_LOCAL_ONLY', '1')
    engines = LocalEngines(tmp_path / 'attempts')
    def local(command, **kwargs):
        from pathlib import Path
        Path(command[-1]).write_text(json.dumps({'version': 'test', 'blocks': []}))
        return SimpleNamespace(returncode=0)
    with patch.object(remote_docling, 'parse_remote_image') as remote, patch('subprocess.run', side_effect=local):
        assert engines.run('docling', source=source)['version'] == 'test'
    remote.assert_not_called()


def test_busy_retries_within_budget_and_timeout_cancels(tmp_path, monkeypatch):
    source = tmp_path / 'crop.png'
    source.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    monkeypatch.setenv('A4B_PARSER_URL', 'https://parser.example')
    monkeypatch.setenv('A4B_PARSER_API_KEY', 'fixture-key')
    busy = urllib.error.HTTPError('https://parser.example', 429, 'busy', {}, io.BytesIO(b'{}'))
    replies = [busy, reply(202, {'job_id': str(uuid4())}), TimeoutError(), reply(200, {})]
    requests = []
    def send(request, timeout):
        requests.append(request)
        response = replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
    with patch('urllib.request.build_opener') as opener, patch('time.sleep'):
        opener.return_value.open.side_effect = send
        with pytest.raises(AnalysisError):
            remote_docling.parse_remote_image(source)
    assert [r.get_method() for r in requests] == ['POST', 'POST', 'GET', 'DELETE']
