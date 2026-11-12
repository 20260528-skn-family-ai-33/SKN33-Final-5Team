import io
import json
import zipfile
from unittest.mock import patch

import pytest

from documents.analysis import remote_pdf
from documents.analysis.contracts import AnalysisError, block, sha256
from documents.analysis import CONTRACT_VERSION


def bundle(source_hash, preview='pdf-preview-0.png'):
    data = io.BytesIO()
    analysis = dict(contract_version=CONTRACT_VERSION, source_format='pdf', source_sha256=source_hash,
                    units=[{'id': 'p1'}], blocks=[block('b1', 'image', {'page': 1}, unit='p1', artifact=preview)])
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('result.json', json.dumps({'document_analysis': analysis}))
        archive.writestr(preview, b'preview')
    return data.getvalue()


def test_result_binds_original_and_preserves_only_declared_previews(tmp_path):
    source = tmp_path / 'original.pdf'
    source.write_bytes(b'%PDF-test')
    digest = sha256(source)
    with pytest.raises(AnalysisError, match='원본'):
        remote_pdf.unpack(bundle('wrong'), tmp_path, digest)
    with pytest.raises(AnalysisError, match='미리보기'):
        remote_pdf.unpack(bundle(digest, '../outside.png'), tmp_path, digest)
    parsed = remote_pdf.unpack(bundle(digest), tmp_path, digest)
    assert (tmp_path / parsed['document_analysis']['blocks'][0]['artifact']).read_bytes() == b'preview'


def test_pdf_client_sends_original_then_polls_same_authenticated_service(tmp_path, monkeypatch):
    import email.message
    from uuid import uuid4
    source = tmp_path / 'a.pdf'
    source.write_bytes(b'%PDF-original-bytes')
    job = str(uuid4())
    requested = []

    def response(status, body, content_type):
        stream = io.BytesIO(body)
        stream.status = status
        stream.headers = email.message.Message()
        stream.headers['Content-Type'] = content_type
        return stream

    replies = [response(202, json.dumps({'job_id': job}).encode(), 'application/json'),
               response(202, b'{}', 'application/json'), response(200, bundle(sha256(source)), 'application/zip')]
    def send(req, timeout):
        requested.append((req.full_url, req.data, req.get_header('Authorization')))
        return replies.pop(0)

    monkeypatch.setenv('A4B_PARSER_URL', 'https://parser.example')
    monkeypatch.setenv('A4B_PARSER_API_KEY', 'test-key')
    with patch('urllib.request.build_opener') as opener, patch('time.sleep'):
        opener.return_value.open.side_effect = send
        remote_pdf.parse_remote_pdf(source, tmp_path)
    assert requested[0] == ('https://parser.example/v1/pdf', source.read_bytes(), 'Bearer test-key')
    assert requested[1:] == [('https://parser.example/v1/pdf/' + job, None, 'Bearer test-key')] * 2


def test_pdf_client_waits_while_the_pod_queue_is_full(tmp_path, monkeypatch):
    import email.message
    import urllib.error
    from uuid import uuid4
    source = tmp_path / 'a.pdf'
    source.write_bytes(b'%PDF-original-bytes')
    job = str(uuid4())

    def response(status, body, content_type):
        stream = io.BytesIO(body)
        stream.status = status
        stream.headers = email.message.Message()
        stream.headers['Content-Type'] = content_type
        return stream
    busy = urllib.error.HTTPError('https://parser.example/v1/pdf', 429, 'busy', {}, io.BytesIO(b'{"error":"pdf_router_busy"}'))
    replies = [busy, response(202, json.dumps({'job_id': job}).encode(), 'application/json'),
               response(200, bundle(sha256(source)), 'application/zip')]

    def send(req, timeout):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setenv('A4B_PARSER_URL', 'https://parser.example')
    monkeypatch.setenv('A4B_PARSER_API_KEY', 'test-key')
    with patch('urllib.request.build_opener') as opener, patch('time.sleep') as sleep:
        opener.return_value.open.side_effect = send
        remote_pdf.parse_remote_pdf(source, tmp_path)
    assert replies == [] and sleep.call_args_list[0].args == (5,)


@pytest.mark.parametrize('failure', ['timeout', 'network', 'invalid_result'])
def test_client_cancels_accepted_job_when_leaving_early(tmp_path, monkeypatch, failure):
    import email.message
    import urllib.error
    from uuid import uuid4
    source = tmp_path / 'a.pdf'
    source.write_bytes(b'%PDF-test')
    job = str(uuid4())
    now, calls = [0.], []
    monkeypatch.setenv('A4B_PARSER_URL', 'https://parser.example')
    monkeypatch.setenv('A4B_PARSER_API_KEY', 'test-key')
    monkeypatch.setenv('A4B_PDF_REMOTE_TIMEOUT_S', '10')
    monkeypatch.setattr(remote_pdf.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(remote_pdf.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    def send(req, timeout):
        calls.append(req.get_method())
        assert req.get_header('Authorization') == 'Bearer test-key'
        if req.get_method() == 'DELETE':
            assert req.full_url.endswith('/' + job) and timeout == 2
            # A failed cancellation must not replace the original error.
            raise urllib.error.URLError('cancel unreachable')
        if req.get_method() == 'POST':
            assert req.get_header('X-a4b-pdf-timeout') == '10'
            assert timeout <= 10
            reply = io.BytesIO(json.dumps({'job_id': job}).encode())
            reply.status = 202
            return reply
        assert timeout <= 10 - now[0]
        if failure == 'network':
            raise urllib.error.URLError('disconnected')
        reply = io.BytesIO(b'{}')
        reply.status = 200 if failure == 'invalid_result' else 202
        reply.headers = email.message.Message()
        reply.headers['Content-Type'] = 'application/json'
        return reply
    with patch('urllib.request.build_opener') as opener:
        opener.return_value.open.side_effect = send
        with pytest.raises(AnalysisError) as error:
            remote_pdf.parse_remote_pdf(source, tmp_path)
    assert calls[0] == 'POST' and calls[-1] == 'DELETE'
    assert error.value.code == {'timeout': 'timeout', 'network': 'remote_pdf_error', 'invalid_result': 'invalid_pdf_result'}[failure]


def test_queue_wait_reduces_worker_budget(monkeypatch):
    import urllib.error
    import urllib.request
    from uuid import uuid4
    now, budgets = [0.], []
    monkeypatch.setattr(remote_pdf.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(remote_pdf.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    def send(request, timeout):
        budgets.append(int(request.get_header('X-a4b-pdf-timeout')))
        if len(budgets) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, 'busy', {}, io.BytesIO())
        reply = io.BytesIO(json.dumps({'job_id': str(uuid4())}).encode())
        reply.status = 202
        return reply
    with patch('urllib.request.build_opener') as opener:
        opener.return_value.open.side_effect = send
        remote_pdf.submit(opener.return_value, urllib.request.Request('https://parser.example', data=b'%PDF'), 10)
    assert budgets == [10, 5]
