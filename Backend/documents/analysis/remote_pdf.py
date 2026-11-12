"""Submit the original PDF to the Pod and restore the router result and review images."""
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
import zipfile

from .contracts import AnalysisError, MAX_BYTES, sha256, validate

MAX_BUNDLE = 64 * 1024 * 1024
PREVIEW = re.compile(r'pdf-preview-[0-9]+\.(?:png|jpe?g|webp|bmp)')


def unpack(data, work, expected_hash):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = {item.filename for item in entries}
        if (len(entries) != len(names) or sum(item.file_size for item in entries) > MAX_BUNDLE
                or any(item.compress_type != zipfile.ZIP_STORED for item in entries)
                or 'result.json' not in names or archive.getinfo('result.json').file_size > 12 * 1024 * 1024):
            raise AnalysisError('invalid_pdf_result', 'GPU PDF 파서 응답 크기 또는 파일 구성이 잘못됐습니다.')
        result = json.loads(archive.read('result.json'))
        analysis = result['document_analysis']
        validate(analysis)
        if analysis['source_format'] != 'pdf' or analysis['source_sha256'] != expected_hash:
            raise AnalysisError('invalid_pdf_result', 'GPU PDF 파서 결과가 업로드한 원본과 일치하지 않습니다.')
        previews = {block['artifact'] for block in analysis['blocks'] if block.get('artifact')}
        if previews != names - {'result.json'} or any(not PREVIEW.fullmatch(name) for name in previews):
            raise AnalysisError('invalid_pdf_result', 'GPU PDF 파서의 미리보기 파일이 올바르지 않습니다.')
        work.mkdir(parents=True, exist_ok=True)
        for name in previews:
            (work / name).write_bytes(archive.read(name))
        return result


def remote_timeout():
    raw = os.environ.get('A4B_PDF_REMOTE_TIMEOUT_S', '290')
    if not raw.isdigit() or not 10 <= int(raw) <= 1800:
        raise AnalysisError('invalid_configuration', 'A4B_PDF_REMOTE_TIMEOUT_S는 10~1800초로 설정하세요.')
    return int(raw)


def submit(opener, request, deadline):
    """Posts the PDF; while the Pod's short queue is full (429), waits and tries again."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            raise AnalysisError('timeout', 'GPU PDF 작업 제한 시간을 초과했습니다.')
        request.add_header('X-A4B-PDF-Timeout', str(int(remaining)))
        try:
            with opener.open(request, timeout=min(60, remaining)) as response:
                if response.status != 202:
                    raise AnalysisError('invalid_pdf_result', 'GPU PDF 파서가 작업 ID를 반환하지 않았습니다.')
                return str(uuid.UUID(json.loads(response.read(1024))['job_id']))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or time.monotonic() + 5 > deadline:
                raise
            exc.close()
            time.sleep(5)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def parse_remote_pdf(source, work):
    if not 0 < source.stat().st_size <= MAX_BYTES:
        raise AnalysisError('input_limit', 'PDF는 0바이트 초과·8MB 이내여야 합니다.')
    key = os.environ.get('A4B_PARSER_API_KEY', '')
    if not key:
        raise AnalysisError('parser_auth', 'A4B_PARSER_API_KEY 설정이 필요합니다.')
    url = os.environ['A4B_PARSER_URL'].rstrip('/') + '/v1/pdf'
    opener = urllib.request.build_opener(NoRedirect())
    headers = {'Authorization': 'Bearer ' + key, 'User-Agent': 'A4B/0.1'}
    if os.environ.get('A4B_DOCUMENT_BENCHMARK_ARM') == 'docling-paddle-fixed-v1':
        headers['X-A4B-PDF-Policy'] = 'docling-paddle-fixed-v1'
    # Stay inside the app's document worker timeout (A4B_DOCUMENT_TIMEOUT_MS, 300 s by default),
    # so this process reports its own error instead of being killed mid-poll.
    deadline = time.monotonic() + remote_timeout()
    job, finished = None, False
    try:
        job = submit(opener, urllib.request.Request(url, data=source.read_bytes(), headers={**headers, 'Content-Type': 'application/pdf'}), deadline)
        # Polling avoids keeping one proxy connection open throughout model loading and inference.
        # The Pod drops a job that stops being polled, so leaving early frees its GPU slot.
        request = urllib.request.Request(url + '/' + job, headers=headers)
        while time.monotonic() < deadline:
            with opener.open(request, timeout=max(.001, min(60, deadline - time.monotonic()))) as response:
                if response.status == 200 and response.headers.get_content_type() == 'application/zip':
                    data = response.read(MAX_BUNDLE + 1)
                    if len(data) > MAX_BUNDLE:
                        raise AnalysisError('result_limit', 'GPU PDF 결과가 크기 제한을 넘었습니다.')
                    result = unpack(data, work, sha256(source))
                    finished = True
                    return result
                if response.status != 202:
                    raise AnalysisError('invalid_pdf_result', 'GPU PDF 파서가 올바른 결과를 반환하지 않았습니다.')
            time.sleep(max(0, min(1, deadline - time.monotonic())))
        raise AnalysisError('timeout', 'GPU PDF 작업 제한 시간을 초과했습니다.')
    except urllib.error.HTTPError as exc:
        try:
            reason = json.loads(exc.read(1024)).get('error', '')
        except (ValueError, AttributeError):
            reason = ''
        code = reason if isinstance(reason, str) and re.fullmatch(r'[a-z_]{1,64}', reason) else 'remote_pdf_error'
        raise AnalysisError(code, f'RunPod PDF 파서가 요청을 처리하지 못했습니다 (HTTP {exc.code}, {code}).') from exc
    except AnalysisError:
        raise
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        raise AnalysisError('remote_pdf_error', 'RunPod PDF 파서 연결 또는 응답을 확인하세요.') from exc
    finally:
        if job and not finished:
            # Best effort on errors/timeouts; the server's polling lease also
            # covers a killed client or a lost submit response with no job ID.
            try:
                cancel = urllib.request.Request(url + '/' + job, headers=headers, method='DELETE')
                with opener.open(cancel, timeout=2):
                    pass
            except (OSError, ValueError):
                pass
