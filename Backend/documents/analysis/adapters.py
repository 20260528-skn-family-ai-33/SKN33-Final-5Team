"""Local process adapters keep PyTorch/Paddle dependencies outside the web server."""
import base64
import json
import math
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
REMOTE_MAX_SOURCES = 32


def image_supplement_deadline(timeout):
    """One document-wide budget, including earlier inspection/layout/OCR time.

    The web worker supplies its actual deadline. CLI callers fall back to the
    same configured timeout. Reserve time for cancellation and writing partials.
    """
    raw = os.environ.get('A4B_DOCUMENT_TIMEOUT_MS') or os.environ.get('A4B_PDF_TIMEOUT_MS', '')
    try:
        configured = float(raw)
    except ValueError:
        configured = 300000
    if not math.isfinite(configured) or not 0 < configured <= 1800000:
        configured = 300000
    remaining = configured / 1000
    try:
        end = float(os.environ.get('A4B_DOCUMENT_DEADLINE_UNIX_MS', ''))
        if math.isfinite(end):
            remaining = min(remaining, end / 1000 - time.time())
    except ValueError:
        pass
    return time.monotonic() + max(0, min(timeout, 120, remaining - 20))


def remote_request_batches(names):
    """Bound both page count and the actual JSON/base64 body, preserving page names."""
    prefix, suffix = b'{"sources":[', b']}'
    overhead = len(prefix) + len(suffix)
    size, batch_names, encoded_sources = overhead, [], []
    for name in names:
        with Path(name).open('rb') as image:
            data = image.read(REMOTE_MAX_IMAGE + 1)
        if len(data) > REMOTE_MAX_IMAGE:
            raise AnalysisError(REMOTE_TOO_LARGE, '변환된 이미지가 GPU 파서의 이미지 크기 제한을 넘었습니다.')
        source = json.dumps({'name': name, 'data': base64.b64encode(data).decode('ascii')}).encode('utf-8')
        if overhead + len(source) > REMOTE_MAX_BODY:
            raise AnalysisError(REMOTE_TOO_LARGE, '이미지 한 장의 인코딩 요청이 GPU 파서의 본문 크기 제한을 넘었습니다.')
        extra = len(source) + (1 if encoded_sources else 0)
        if encoded_sources and (len(encoded_sources) == REMOTE_MAX_SOURCES or size + extra > REMOTE_MAX_BODY):
            yield batch_names, prefix + b','.join(encoded_sources) + suffix
            size, batch_names, encoded_sources = overhead, [], []
            extra = len(source)
        batch_names.append(name)
        encoded_sources.append(source)
        size += extra
    if encoded_sources:
        yield batch_names, prefix + b','.join(encoded_sources) + suffix


def remote_request_body(names):
    """Encode a single request; callers handling multiple batches use remote_engine."""
    batches = iter(remote_request_batches(names))
    first = next(batches, ([], b'{"sources":[]}'))
    if next(batches, None) is not None:
        raise AnalysisError(REMOTE_TOO_LARGE, '이미지 목록을 여러 GPU 요청으로 나누어야 합니다.')
    return first[1]


def remote_engine(url, engine, sources, *, deadline=None):
    """Layout/OCR on the GPU parser server (scripts/runpod/gpu_parser_server.py), same contract as engine_worker."""
    names = [str(p) for p in sources]
    deadline = min(deadline, time.monotonic() + REMOTE_TIMEOUT) if deadline is not None else time.monotonic() + REMOTE_TIMEOUT
    result, received = None, 0
    for batch_names, body in remote_request_batches(names):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AnalysisError(REMOTE_UNAVAILABLE, 'GPU 파서 요청 제한 시간을 초과했습니다.')
        data, size = _remote_batch(url, engine, batch_names, body, remaining)
        received += size
        if received > MAX_RESULT:
            raise AnalysisError('invalid_engine_result', '합산 엔진 응답이 크기 제한을 넘었습니다.')
        if result is None:
            result = data
        else:
            if data['version'] != result['version'] or data.get('model') != result.get('model'):
                raise AnalysisError('invalid_engine_result', '처리 도중 GPU 엔진 버전이 변경됐습니다.')
            result['outputs'].extend(data['outputs'])
            if data.get('invalid_output_sources'):
                result['invalid_output_sources'] = True
    if result is None:
        raise AnalysisError('invalid_engine_result', 'GPU 파서에 보낼 이미지가 없습니다.')
    return result


def _remote_batch(url, engine, names, body, timeout):
    # The RunPod proxy answers 403 to urllib's default "Python-urllib" agent.
    request = urllib.request.Request(url.rstrip('/') + '/v1/' + engine, data=body, method='POST', headers={
        'Content-Type': 'application/json', 'User-Agent': 'A4B/0.1',
        'Authorization': 'Bearer ' + os.environ.get('A4B_PARSER_API_KEY', '')})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
    if not isinstance(data, dict) or not isinstance(data.get('version'), str) or not isinstance(data.get('outputs'), list):
        raise AnalysisError('invalid_engine_result', '엔진 응답 계약이 올바르지 않습니다.')
    valid = [o for o in data['outputs'] if isinstance(o, dict)
             and isinstance(o.get('source'), str) and o['source'] in names]
    if len(valid) != len(data['outputs']):
        if engine != 'paddleocr':
            raise AnalysisError('invalid_engine_result', '엔진 응답 계약이 올바르지 않습니다.')
        # Preserve attributable OCR items, but never hide unknown provenance.
        # Body parsing marks this globally; table verification rejects it.
        data['outputs'] = valid
        data['invalid_output_sources'] = True
    return data, len(raw)


class LocalEngines:
    def __init__(self, work, timeout=240):
        self.work = Path(work)
        self.timeout = timeout
        self.attempts = []
        self.image_deadline = image_supplement_deadline(timeout)
        self.image_unavailable = None

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
            verification = phase == 'table_verification'
            if verification and self.image_deadline <= time.monotonic():
                raise AnalysisError('timeout', '표 OCR 검증에 사용할 문서 시간이 남아 있지 않습니다.')
            if (engine == 'docling' and source and Path(source).suffix.lower() == '.png'
                    and os.environ.get('A4B_PARSER_URL')
                    and os.environ.get('A4B_DOCLING_LOCAL_ONLY') != '1'):
                from .remote_docling import parse_remote_image
                attempt['route'] = 'gpu_remote'
                if self.image_unavailable:
                    raise AnalysisError(self.image_unavailable, '이 문서의 나머지 표 보완은 검토가 필요합니다.')
                remaining = self.image_deadline - time.monotonic()
                if remaining <= 0:
                    raise AnalysisError('timeout', '문서 전체의 이미지 표 보완 시간 예산을 소진했습니다.')
                try:
                    data = parse_remote_image(Path(source), timeout=remaining)
                except AnalysisError as exc:
                    if exc.code in ('busy', 'timeout'):
                        self.image_unavailable = exc.code
                    raise
                (folder / 'response.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                attempt.update(status='partial' if data.get('partial') else 'succeeded',
                               version=data['version'], model=data.get('model', {}))
                return data
            remote = os.environ.get('A4B_PARSER_URL') if engine in REMOTE_ENGINES else None
            if remote:
                attempt['route'] = 'gpu_remote'
                try:
                    data = (remote_engine(remote, engine, sources or [], deadline=self.image_deadline) if verification
                            else remote_engine(remote, engine, sources or []))
                    (folder / 'raw.json').write_text(json.dumps(data['outputs'], ensure_ascii=True), encoding='utf-8')
                    attempt.update(status='succeeded', version=data['version'], model=data.get('model', {}))
                    return data
                except AnalysisError as exc:
                    if exc.code not in (REMOTE_UNAVAILABLE, REMOTE_TOO_LARGE) or os.environ.get('A4B_PARSER_FALLBACK', 'cpu') == 'off':
                        raise
                    attempt['remote_error'] = exc.code
            attempt['route'] = 'cpu_local'
            local_timeout = min(self.timeout, self.image_deadline - time.monotonic()) if verification else self.timeout
            if local_timeout <= 0:
                raise AnalysisError('timeout', '표 OCR 검증에 사용할 문서 시간이 남아 있지 않습니다.')
            with (folder / 'engine.log').open('wb') as log:
                completed = subprocess.run([executable, str(Path(__file__).with_name('engine_worker.py')), str(request_file), str(output)],
                                           stdout=log, stderr=log, timeout=local_timeout, check=False,
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
