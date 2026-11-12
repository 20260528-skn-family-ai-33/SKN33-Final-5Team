"""Exercise the shared HTTP/PDF contract without downloading or loading models."""
import http.client
import importlib.util
import json
import os
import signal
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


readiness_module = load('parser_readiness')
jobs_module = load('pdf_jobs')
server_module = load('gpu_parser_server')
worker = load('pod_pdf_worker')
KEY = 'synthetic-parser-test-key-0123456789'


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_PDF_ROUTER_PYTHON', sys.executable)
    monkeypatch.delenv('A4B_API_AUTH_REQUIRED', raising=False)
    monkeypatch.delenv('A4B_MINERU_PYTHON', raising=False)
    models = tmp_path / 'docling'
    ocr = models / 'EasyOcr'
    ocr.mkdir(parents=True)
    (ocr / 'fixture-model').touch()
    monkeypatch.setenv('A4B_PDF_ARTIFACTS_DIR', str(models))
    monkeypatch.setenv('A4B_PDF_OCR_MODEL_DIR', str(ocr))
    handler = server_module.make_handler(KEY, {}, 'gpu:0', 'fixture')
    service = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    yield service.server_port
    handler.pdf_jobs.close()
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


def wait_for(check, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('Fixture did not reach expected state')


def install_worker(monkeypatch, tmp_path, body):
    script = tmp_path / 'worker.py'
    script.write_text('import os,sys,time,signal,subprocess\nfrom pathlib import Path\n' + body)
    monkeypatch.setattr(jobs_module, 'WORKER', script)
    return script


def test_pdf_job_is_bounded_and_result_is_delivered_as_zip(server, monkeypatch, tmp_path):
    running, release = tmp_path / 'running', tmp_path / 'release'
    install_worker(monkeypatch, tmp_path, f"""
assert os.environ['A4B_PDF_DEVICE'] == 'cuda'
assert sys.argv[-1] == 'docling-paddle-fixed-v1'
work = Path(sys.argv[1])
assert (work / 'original.pdf').read_bytes() == b'%PDF-test'
Path({str(running)!r}).touch()
while not Path({str(release)!r}).exists(): time.sleep(.02)
(work / 'result.zip').write_bytes(b'PK-fixture-result')
""")
    status, _, body = request(server, 'POST', '/v1/pdf', b'%PDF-test',
                             **{'X-A4B-PDF-Policy': 'docling-paddle-fixed-v1'})
    assert status == 202
    wait_for(running.exists)
    job = json.loads(body)['job_id']
    assert request(server, 'GET', '/v1/pdf/' + job)[0] == 202
    assert request(server, 'POST', '/v1/pdf', b'%PDF-next')[0] == 429
    release.touch()
    wait_for(lambda: request(server, 'GET', '/v1/pdf/' + job)[0] != 202)
    assert request(server, 'GET', '/v1/pdf/' + job) == (200, 'application/zip', b'PK-fixture-result')


def test_worker_failure_is_reported_and_slot_is_released(monkeypatch, tmp_path):
    install_worker(monkeypatch, tmp_path, 'raise SystemExit(1)\n')
    jobs = jobs_module.PdfJobs(sys.executable)
    try:
        _, accepted = jobs.submit(b'%PDF-test')
        job = accepted['job_id']
        wait_for(lambda: jobs.result(job)[0] != 202)
        assert jobs.result(job) == (422, {'error': 'pdf_gpu_processing_failed'})
        assert jobs.submit(b'%PDF-next')[0] == 202
        assert jobs_module.PdfJobs(None).submit(b'%PDF-test')[0] == 503
    finally:
        jobs.close()


@pytest.mark.parametrize('missing', ['python', 'worker', 'docling_models', 'ocr_models', 'mineru_python'])
def test_missing_runtime_is_not_ready_and_rejects_pdf(server, monkeypatch, tmp_path, missing):
    if missing == 'python':
        # Existing non-executable files are not a valid worker interpreter.
        invalid = tmp_path / 'not-executable'
        invalid.touch()
        monkeypatch.setattr(readiness_module, 'executable', lambda value: False)
    elif missing == 'worker':
        monkeypatch.setattr(readiness_module, 'WORKER', tmp_path / 'missing-worker')
    else:
        key = {'docling_models': 'A4B_PDF_ARTIFACTS_DIR', 'ocr_models': 'A4B_PDF_OCR_MODEL_DIR',
               'mineru_python': 'A4B_MINERU_PYTHON'}[missing]
        monkeypatch.setenv(key, str(tmp_path / 'missing'))
    health = json.loads(request(server, 'GET', '/health')[2])
    assert health['pdf_jobs'] is False
    assert health['pdf_readiness']['checks'][missing] is False
    assert request(server, 'POST', '/v1/pdf', b'%PDF-test')[0] == 503


def alive(pid):
    try:
        os.kill(pid, 0)
        stat = Path('/proc') / str(pid) / 'stat'
        return not stat.exists() or stat.read_text().split(') ')[1][0] != 'Z'
    except ProcessLookupError:
        return False


@pytest.mark.parametrize('ending', ['cancel', 'lease', 'deadline', 'close'])
def test_abandoned_jobs_kill_worker_and_child_then_release_slot(monkeypatch, tmp_path, ending):
    marker = tmp_path / 'child.pid'
    child_code = ('import signal,time; from pathlib import Path; import os; '
                  'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                  f'Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)')
    # The child ignores TERM; cancellation must still remove its GPU process.
    install_worker(monkeypatch, tmp_path, f"""
child = subprocess.Popen([sys.executable, '-c', {child_code!r}])
while True: time.sleep(.02)
""")
    jobs = jobs_module.PdfJobs(sys.executable, lease_seconds=.5 if ending == 'lease' else 2)
    try:
        status, accepted = jobs.submit(b'%PDF-test', timeout=1 if ending == 'deadline' else 10)
        assert status == 202
        identifier = accepted['job_id']
        wait_for(lambda: marker.exists() and marker.read_text())
        child = int(marker.read_text())
        assert alive(child)
        assert jobs.submit(b'%PDF-other')[0] == 429
        if ending == 'cancel':
            assert jobs.cancel(identifier)[0] == 202
        elif ending == 'close':
            jobs.close()
        if ending == 'lease':
            # Inspect completion without renewing the polling lease.
            wait_for(lambda: jobs.jobs[identifier].response is not None)
        else:
            wait_for(lambda: jobs.result(identifier)[0] != 202)
        reason = {'cancel': 'cancelled', 'close': 'cancelled', 'lease': 'lease_expired', 'deadline': 'timeout'}[ending]
        assert jobs.result(identifier) == (410, {'error': 'pdf_job_' + reason})
        wait_for(lambda: not alive(child))
        assert jobs.submit(b'%PDF-next')[0] == (503 if ending == 'close' else 202)
    finally:
        jobs.close()


def test_polling_renews_lease_but_not_deadline(monkeypatch, tmp_path):
    install_worker(monkeypatch, tmp_path, 'time.sleep(60)\n')
    jobs = jobs_module.PdfJobs(sys.executable, lease_seconds=.25)
    try:
        _, accepted = jobs.submit(b'%PDF-test', timeout=1)
        identifier = accepted['job_id']
        wait_for(lambda: jobs.result(identifier)[0] != 202)
        assert jobs.result(identifier)[1]['error'] == 'pdf_job_timeout'
    finally:
        jobs.close()


def test_authenticated_http_cancel_releases_pdf_slot(server, monkeypatch, tmp_path):
    install_worker(monkeypatch, tmp_path, 'time.sleep(60)\n')
    status, _, body = request(server, 'POST', '/v1/pdf', b'%PDF-test')
    assert status == 202
    path = '/v1/pdf/' + json.loads(body)['job_id']
    assert request(server, 'DELETE', path, key='wrong')[0] == 401
    assert request(server, 'GET', path)[0] == 202
    assert request(server, 'DELETE', path)[0] == 202
    wait_for(lambda: request(server, 'GET', path)[0] != 202)
    assert json.loads(request(server, 'GET', path)[2])['error'] == 'pdf_job_cancelled'
    assert request(server, 'POST', '/v1/pdf', b'%PDF-next')[0] == 202


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


def test_33_page_comparison_ocr_uses_two_valid_requests(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from documents.analysis.adapters import LocalEngines
    from documents.analysis import benchmark_baseline
    images = [tmp_path / f'page-{i:02}.png' for i in range(33)]
    for i, path in enumerate(images):
        path.write_text('OCR page ' + str(i + 1))
    source = tmp_path / 'original.pdf'
    source.write_bytes(b'%PDF-fixture')
    monkeypatch.setattr(benchmark_baseline, 'prepare', lambda *a: (source, images, []))
    monkeypatch.setenv('A4B_PARSER_API_KEY', KEY)
    monkeypatch.setenv('A4B_PARSER_FALLBACK', 'off')
    monkeypatch.delenv('A4B_API_AUTH_REQUIRED', raising=False)
    batches = []
    original_run = server_module.run

    def run(engine, payload, *args):
        batches.append(len(payload['sources']))
        return original_run(engine, payload, *args)
    monkeypatch.setattr(server_module, 'run', run)

    class Predictor:
        def predict(self, path):
            return [SimpleNamespace(json={'res': {'rec_texts': [Path(path).read_text()], 'rec_scores': [1.]}})]

    class Engines(LocalEngines):
        def run(self, engine, **kwargs):
            if engine == 'docling':
                self.attempts.append(dict(id='docling', processor=engine, phase='primary',
                                         status='succeeded', elapsed_seconds=0))
                return dict(version='fixture', blocks=[])
            return super().run(engine, **kwargs)

    handler = server_module.make_handler(KEY, {'paddleocr': (Predictor(), 'fixture')}, 'gpu:0', 'fixture')
    service = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('A4B_PARSER_URL', f'http://127.0.0.1:{service.server_port}')
    try:
        result = benchmark_baseline.run(source, tmp_path, engines=Engines(tmp_path))
        assert batches == [32, 1]
        blocks = [b for b in result['document_analysis']['blocks'] if b['text'].startswith('OCR page ')]
        assert [b['native_location']['page'] for b in blocks] == list(range(1, 34))
        assert [b['text'] for b in blocks] == ['OCR page ' + str(i) for i in range(1, 34)]
        assert result['document_analysis']['coverage']['paddleocr_succeeded']
    finally:
        handler.pdf_jobs.close()
        service.shutdown()
        service.server_close()
        thread.join(timeout=2)


def test_http_worker_budget_is_enforced(server, monkeypatch, tmp_path):
    install_worker(monkeypatch, tmp_path, 'time.sleep(60)\n')
    for value in ('0', '-1', '1801', 'bad'):
        assert request(server, 'POST', '/v1/pdf', b'%PDF-test', **{'X-A4B-PDF-Timeout': value})[0] == 400
    status, _, body = request(server, 'POST', '/v1/pdf', b'%PDF-test', **{'X-A4B-PDF-Timeout': '1'})
    assert status == 202
    path = '/v1/pdf/' + json.loads(body)['job_id']
    wait_for(lambda: request(server, 'GET', path)[0] != 202)
    assert json.loads(request(server, 'GET', path)[2])['error'] == 'pdf_job_timeout'
    assert request(server, 'POST', '/v1/pdf', b'%PDF-next')[0] == 202


def test_server_sigterm_cleans_active_worker_session(server, monkeypatch, tmp_path):
    # `server` supplies the same synthetic model-path environment as HTTP tests.
    port_file, worker_pid = tmp_path / 'port', tmp_path / 'worker.pid'
    worker_script = install_worker(monkeypatch, tmp_path, f"Path({str(worker_pid)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n")
    code = f'''
import sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
import gpu_parser_server as parser
import pdf_jobs
pdf_jobs.WORKER = Path({str(worker_script)!r})
parser.load_models = lambda device: {{}}
parser.importlib.metadata.version = lambda package: 'fixture'
original = parser.ThreadingHTTPServer
def make_server(address, handler):
    service = original(('127.0.0.1', 0), handler)
    Path({str(port_file)!r}).write_text(str(service.server_port))
    return service
parser.ThreadingHTTPServer = make_server
parser.main()
'''
    environment = {**os.environ, 'A4B_PARSER_API_KEY': KEY}
    with (tmp_path / 'server.log').open('wb') as log:
        process = subprocess.Popen([sys.executable, '-c', code], env=environment, stdout=log, stderr=log)
        try:
            wait_for(lambda: port_file.exists() and port_file.read_text())
            port = int(port_file.read_text())
            assert request(port, 'POST', '/v1/pdf', b'%PDF-test')[0] == 202
            wait_for(lambda: worker_pid.exists() and worker_pid.read_text())
            pid = int(worker_pid.read_text())
            assert alive(pid)
            process.terminate()
            assert process.wait(timeout=4) == 0
            wait_for(lambda: not alive(pid))
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=4)
