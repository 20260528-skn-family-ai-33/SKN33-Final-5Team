"""Local process adapters keep PyTorch/Paddle dependencies outside the web server."""
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from .contracts import AnalysisError, MAX_RESULT, MAX_BYTES, sha256

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REMOTE_ENGINES = ('layout', 'paddleocr')
REMOTE_TIMEOUT = 90  # below the RunPod proxy's ~100 s request limit
REMOTE_UNAVAILABLE = 'remote_parser_unavailable'
REMOTE_TOO_LARGE = 'remote_parser_payload_too_large'
# Match the decoded image and HTTP body limits in scripts/runpod/gpu_parser_server.py.
REMOTE_MAX_IMAGE = 12 * 1024 * 1024
REMOTE_MAX_BODY = 48 * 1024 * 1024


def remote_request_body(names):
    prefix, suffix = b'{"sources":[', b']}'
    size = len(prefix) + len(suffix)
    encoded_sources = []
    for name in names:
        with Path(name).open('rb') as image:
            data = image.read(REMOTE_MAX_IMAGE + 1)
        if len(data) > REMOTE_MAX_IMAGE:
            raise AnalysisError(REMOTE_TOO_LARGE, '변환된 이미지가 GPU 파서의 이미지 크기 제한을 넘었습니다.')
        source = json.dumps({'name': name, 'data': base64.b64encode(data).decode('ascii')}).encode('utf-8')
        size += len(source) + (1 if encoded_sources else 0)  # Include each comma between sources.
        if size > REMOTE_MAX_BODY:
            raise AnalysisError(REMOTE_TOO_LARGE, '인코딩된 요청이 GPU 파서의 본문 크기 제한을 넘었습니다.')
        encoded_sources.append(source)
    return prefix + b','.join(encoded_sources) + suffix


def remote_engine(url, engine, sources):
    """Layout/OCR on the GPU parser server (scripts/runpod/gpu_parser_server.py), same contract as engine_worker."""
    names = [str(p) for p in sources]
    body = remote_request_body(names)
    # The RunPod proxy answers 403 to urllib's default "Python-urllib" agent.
    request = urllib.request.Request(url.rstrip('/') + '/v1/' + engine, data=body, method='POST', headers={
        'Content-Type': 'application/json', 'User-Agent': 'A4B/0.1',
        'Authorization': 'Bearer ' + os.environ.get('A4B_PARSER_API_KEY', '')})
    try:
        with urllib.request.urlopen(request, timeout=REMOTE_TIMEOUT) as response:
            raw = response.read(MAX_RESULT + 1)
    except urllib.error.HTTPError as exc:
        # 5xx is a server fault; the RunPod proxy answers 404 while the pod is stopped. Both are outages.
        # A proxy may enforce a lower body limit than the parser itself.
        if exc.code == 413:
            raise AnalysisError(REMOTE_TOO_LARGE, 'GPU 파서 요청이 서버의 크기 제한을 넘었습니다(HTTP 413).') from exc
        if exc.code >= 500 or exc.code == 404:
            raise AnalysisError(REMOTE_UNAVAILABLE, 'GPU 파서 서버를 사용할 수 없습니다(HTTP ' + str(exc.code) + ').') from exc
        raise AnalysisError('remote_parser_rejected_' + str(exc.code),
                            'GPU 파서 서버가 요청을 거부했습니다(HTTP ' + str(exc.code) + '). A4B_PARSER_URL·A4B_PARSER_API_KEY를 확인하세요.') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AnalysisError(REMOTE_UNAVAILABLE, 'GPU 파서 서버에 연결하지 못했습니다.') from exc
    if len(raw) > MAX_RESULT:
        raise AnalysisError('invalid_engine_result', '엔진 응답이 크기 제한을 넘었습니다.')
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AnalysisError('invalid_engine_result', '엔진 JSON 응답을 읽을 수 없습니다.') from exc
    if not isinstance(data, dict) or not isinstance(data.get('version'), str) or not isinstance(data.get('outputs'), list) \
            or not all(isinstance(o, dict) and o.get('source') in names for o in data['outputs']):
        raise AnalysisError('invalid_engine_result', '엔진 응답 계약이 올바르지 않습니다.')
    return data


class LocalEngines:
    def __init__(self, work, timeout=240):
        self.work = Path(work)
        self.timeout = timeout
        self.attempts = []

    def run(self, engine, *, source=None, sources=None, scope=None, phase='primary'):
        started = time.perf_counter()
        identifier = 'attempt-' + str(len(self.attempts) + 1)
        folder = self.work / identifier
        folder.mkdir(parents=True)
        configured = os.environ.get({'mineru': 'A4B_MINERU_PYTHON', 'docling': 'EASYFLOW_DOCLING_PYTHON',
                                     'layout': 'A4B_PADDLE_PYTHON', 'paddleocr': 'A4B_PADDLE_PYTHON'}[engine])
        candidate = PROJECT_ROOT / 'Backend' / ('.venv-' + ('paddle' if engine == 'layout' or engine == 'paddleocr' else engine)) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        executable = configured or (str(candidate) if candidate.is_file() else sys.executable)
        request = dict(engine=engine, source=str(source) if source else None,
                       sources=[str(p) for p in sources or []], raw=str(folder / 'raw.json'))
        request_file, output = folder / 'request.json', folder / 'response.json'
        request_file.write_text(json.dumps(request), encoding='utf-8')
        attempt = dict(id=identifier, phase=phase, processor=engine, scope=scope,
                       input_sha256=[sha256(p) for p in ([source] if source else sources or [])],
                       status='failed', artifact=str(folder.relative_to(self.work)), elapsed_seconds=0)
        self.attempts.append(attempt)
        try:
            remote = os.environ.get('A4B_PARSER_URL') if engine in REMOTE_ENGINES else None
            if remote:
                attempt['route'] = 'gpu_remote'
                try:
                    data = remote_engine(remote, engine, sources or [])
                    (folder / 'raw.json').write_text(json.dumps(data['outputs'], ensure_ascii=False), encoding='utf-8')
                    attempt.update(status='succeeded', version=data['version'], model=data.get('model', {}))
                    return data
                except AnalysisError as exc:
                    if exc.code not in (REMOTE_UNAVAILABLE, REMOTE_TOO_LARGE) or os.environ.get('A4B_PARSER_FALLBACK', 'cpu') == 'off':
                        raise
                    attempt['remote_error'] = exc.code
            attempt['route'] = 'cpu_local'
            with (folder / 'engine.log').open('wb') as log:
                completed = subprocess.run([executable, str(Path(__file__).with_name('engine_worker.py')), str(request_file), str(output)],
                                           stdout=log, stderr=log, timeout=self.timeout, check=False,
                                           cwd=PROJECT_ROOT,
                                           env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
            if not output.is_file() or output.stat().st_size > MAX_RESULT:
                raise AnalysisError('invalid_engine_result', '엔진 응답이 없거나 크기 제한을 넘었습니다.')
            try:
                data = json.loads(output.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise AnalysisError('invalid_engine_result', '엔진 JSON 응답을 읽을 수 없습니다.') from exc
            if not isinstance(data, dict) or not data.get('error') and not isinstance(data.get('version'), str):
                raise AnalysisError('invalid_engine_result', '엔진 응답 계약이 올바르지 않습니다.')
            if completed.returncode or data.get('error'):
                raise AnalysisError(data.get('error', 'engine_error'), engine + ' 처리 실패. 실행 환경·로컬 모델과 엔진 로그를 확인하세요.')
            attempt.update(status='partial' if data.get('partial') else 'succeeded',
                           version=data['version'], model=data.get('model', {}))
            return data
        except subprocess.TimeoutExpired as exc:
            attempt['error_code'] = 'timeout'
            raise AnalysisError('timeout', engine + ' 처리 시간이 초과됐습니다.') from exc
        except OSError as exc:
            attempt['error_code'] = 'dependency_missing'
            raise AnalysisError('dependency_missing', engine + ' Python 실행 환경을 확인하세요.') from exc
        except AnalysisError as exc:
            attempt['error_code'] = exc.code
            raise
        finally:
            attempt['elapsed_seconds'] = time.perf_counter() - started


def convert(source, fmt, work, timeout=60):
    """Copies only. No shell templates, no caller-supplied commands or URL inputs."""
    source, work = Path(source), Path(work)
    folder = work / 'converted'
    folder.mkdir(parents=True, exist_ok=True)
    if fmt in {'doc', 'ppt', 'xls'}:
        target_fmt = {'doc': 'docx', 'ppt': 'pptx', 'xls': 'xlsx'}[fmt]
        executable = os.environ.get('A4B_LIBREOFFICE') or shutil.which('soffice')
        if not executable:
            raise AnalysisError('conversion_dependency_missing', '구형 Office 변환에 LibreOffice가 필요합니다.')
        profile = (work / 'libreoffice-profile').resolve().as_uri()
        command = [executable, '-env:UserInstallation=' + profile, '--headless', '--convert-to', target_fmt, '--outdir', str(folder), str(source)]
    else:
        target_fmt = 'pdf'
        executable = os.environ.get('A4B_RHWP') or shutil.which('rhwp')
        if not executable:
            raise AnalysisError('conversion_dependency_missing', 'HWP/HWPX 변환에 rhwp가 필요합니다.')
        command = [executable, 'export-pdf', str(source), '-o', str(folder / (source.stem + '.pdf'))]
    with (folder / 'conversion.log').open('wb') as log:
        try:
            completed = subprocess.run(command, stdout=log, stderr=log, timeout=timeout, check=False, cwd=PROJECT_ROOT)
        except subprocess.TimeoutExpired as exc:
            raise AnalysisError('conversion_timeout', '파일 변환 시간이 초과됐습니다.') from exc
    output = folder / (source.stem + '.' + target_fmt)
    if completed.returncode or not output.is_file() or not output.stat().st_size:
        raise AnalysisError('conversion_failed', '변환 결과를 확인할 수 없습니다. 변환 로그를 확인하세요.')
    if output.stat().st_size > MAX_BYTES:
        raise AnalysisError('conversion_limit', '변환 파일이 8MB 제한을 넘었습니다.')
    if sha256(source) == sha256(output):
        raise AnalysisError('conversion_failed', '파일 형식 변환이 수행되지 않았습니다.')
    return output, target_fmt, dict(source_sha256=sha256(source), converted_sha256=sha256(output),
                                    artifact=str(output.relative_to(work)), processor='rhwp' if target_fmt == 'pdf' else 'libreoffice')
