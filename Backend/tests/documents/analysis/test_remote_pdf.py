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
