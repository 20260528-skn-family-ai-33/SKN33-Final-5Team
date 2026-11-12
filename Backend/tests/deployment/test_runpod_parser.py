"""Exercise the shared HTTP/PDF contract without downloading or loading models."""
import http.client
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / 'scripts/runpod'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


jobs_module = load('pdf_jobs')
server_module = load('gpu_parser_server')
worker = load('pod_pdf_worker')
KEY = 'synthetic-parser-test-key-0123456789'


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv('A4B_PDF_ROUTER_PYTHON', sys.executable)
    monkeypatch.delenv('A4B_API_AUTH_REQUIRED', raising=False)
    handler = server_module.make_handler(KEY, {}, 'gpu:0', 'fixture')
    service = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    yield service.server_port
    service.shutdown()
    service.server_close()
    thread.join(timeout=2)


def request(port, method, path, body=None, *, key=KEY, content_type='application/pdf', **headers):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request(method, path, body=body, headers={
            'Authorization': 'Bearer ' + key, 'Content-Type': content_type, **headers})
        response = connection.getresponse()
        data = response.read()
        return response.status, response.getheader('Content-Type'), data
    finally:
        connection.close()


def test_http_pdf_auth_validation_and_existing_health(server):
    assert json.loads(request(server, 'GET', '/health')[2])['pdf_jobs'] is True
    assert request(server, 'POST', '/v1/pdf', b'%PDF-test', key='wrong')[0] == 401
    assert request(server, 'GET', '/v1/pdf/missing', key='wrong')[0] == 401
    assert request(server, 'POST', '/v1/paddleocr', b'{}', key='wrong')[0] == 401
    assert request(server, 'POST', '/v1/pdf', b'%PDF-test', content_type='text/plain')[0] == 415
    assert request(server, 'POST', '/v1/pdf', b'%PDF-test', **{'X-A4B-PDF-Policy': 'unknown'})[0] == 400
    assert request(server, 'POST', '/v1/pdf', b'not-pdf')[0] == 400
    assert request(server, 'GET', '/v1/pdf/missing')[0] == 404


def test_explicit_existing_pod_auth_setting_is_preserved(server, monkeypatch):
    monkeypatch.setenv('A4B_API_AUTH_REQUIRED', '0')
    assert request(server, 'GET', '/v1/pdf/missing', key='wrong')[0] == 404


def test_pdf_job_is_bounded_and_result_is_delivered_as_zip(server, monkeypatch):
    running, release = threading.Event(), threading.Event()

    def process(command, *, env, check, timeout):
        assert env['A4B_PDF_DEVICE'] == 'cuda'
        assert timeout == 1500 and check
        assert command[-1] == 'docling-paddle-fixed-v1'
        work = Path(command[-2])
        assert (work / 'original.pdf').read_bytes() == b'%PDF-test'
        running.set()
        assert release.wait(5)
        (work / 'result.zip').write_bytes(b'PK-fixture-result')

    monkeypatch.setattr(jobs_module.subprocess, 'run', process)
    try:
        status, _, body = request(server, 'POST', '/v1/pdf', b'%PDF-test',
                                 **{'X-A4B-PDF-Policy': 'docling-paddle-fixed-v1'})
        assert status == 202 and running.wait(2)
        job = json.loads(body)['job_id']
        assert request(server, 'GET', '/v1/pdf/' + job)[0] == 202
        assert request(server, 'POST', '/v1/pdf', b'%PDF-next')[0] == 429
        release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, content_type, body = request(server, 'GET', '/v1/pdf/' + job)
            if status == 200:
                break
            time.sleep(.01)
        assert (status, content_type, body) == (200, 'application/zip', b'PK-fixture-result')
    finally:
        release.set()


def test_worker_failure_is_reported_and_slot_is_released(monkeypatch):
    jobs = jobs_module.PdfJobs(sys.executable)
    monkeypatch.setattr(jobs_module.threading.Thread, 'start', lambda self: None)
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(jobs_module.subprocess, 'run', fail)
    _, accepted = jobs.submit(b'%PDF-test')
    jobs._run(accepted['job_id'], b'%PDF-test', None)
    assert jobs.result(accepted['job_id']) == (422, {'error': 'pdf_gpu_processing_failed'})
    assert jobs.submit(b'%PDF-next')[0] == 202
    assert jobs_module.PdfJobs(None).submit(b'%PDF-test')[0] == 503


def test_pod_preview_bundle_is_accepted_by_local_client(tmp_path):
    from documents.analysis.contracts import block, sha256
    from documents.analysis import CONTRACT_VERSION
    from documents.analysis.remote_pdf import unpack
    source = tmp_path / 'original.pdf'
    source.write_bytes(b'%PDF-fixture')
    (tmp_path / 'page.png').write_bytes(b'fixture-preview')
    analysis = dict(contract_version=CONTRACT_VERSION, source_format='pdf', source_sha256=sha256(source),
                    units=[{'id': 'p1'}], blocks=[block('b1', 'image', {'page': 1}, unit='p1', artifact='page.png')])
    worker.write_result(tmp_path, {'document_analysis': analysis})
    result = unpack((tmp_path / 'result.zip').read_bytes(), tmp_path / 'client', sha256(source))
    assert result['document_analysis']['blocks'][0]['artifact'] == 'pdf-preview-0.png'
    assert (tmp_path / 'client/pdf-preview-0.png').read_bytes() == b'fixture-preview'


@pytest.mark.parametrize('mineru,expected', [(True, 'mineru'), (False, 'docling')])
def test_pod_worker_selects_configured_engine(monkeypatch, tmp_path, mineru, expected):
    from types import SimpleNamespace
    monkeypatch.setattr(worker, 'configure_workspace_mineru', lambda: None)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: True, get_device_name=lambda _: 'fixture GPU')))
    if mineru:
        monkeypatch.setenv('A4B_MINERU_PYTHON', '/fixture/mineru/python')
    else:
        monkeypatch.delenv('A4B_MINERU_PYTHON', raising=False)
    def run(source, work, *, pdf_primary):
        assert pdf_primary == expected
        return {'document_analysis': {'routes': [{'processor': expected, 'status': 'succeeded',
                                                'model': {'device': 'cuda', 'cuda_peak_bytes': 10}}]}}
    monkeypatch.setattr(worker.pipeline, 'run', run)
    route = worker.analyze(tmp_path)['document_analysis']['routes'][0]
    assert route['route'] == 'gpu_remote' and route['model']['remote'] is True
