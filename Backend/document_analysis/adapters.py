"""Local process adapters keep PyTorch/Paddle dependencies outside the web server."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from .contracts import AnalysisError, MAX_RESULT, MAX_BYTES, sha256

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
