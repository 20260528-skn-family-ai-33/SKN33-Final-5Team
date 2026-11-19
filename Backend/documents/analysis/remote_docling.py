"""Run PNG table supplements on the Pod without holding a proxy connection open."""
import json
import os
import time
import http.client
import urllib.error
import urllib.request
import uuid

from .contracts import AnalysisError, MAX_BYTES, MAX_RESULT, sha256
from .remote_pdf import NoRedirect

QUEUE_WAIT_SECONDS = 10
RETRY_SECONDS = 5  # match PDF admission retries; polling an accepted job stays at 1 s


def parse_remote_image(source, timeout=240):
    deadline = time.monotonic() + timeout
    queue_deadline = min(deadline, time.monotonic() + QUEUE_WAIT_SECONDS)
    with source.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if not 0 < len(data) <= MAX_BYTES or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise AnalysisError('input_limit', 'Docling 표 보완 입력은 8MB 이내의 PNG여야 합니다.')
    key = os.environ.get('A4B_PARSER_API_KEY', '')
    if not key:
        raise AnalysisError('parser_auth', 'A4B_PARSER_API_KEY 설정이 필요합니다.')
    url = os.environ['A4B_PARSER_URL'].rstrip('/') + '/v1/docling-image'
    headers = {'Authorization': 'Bearer ' + key, 'User-Agent': 'A4B/0.1'}
    opener = urllib.request.build_opener(NoRedirect())
    job, finished = None, False

    def send(request, until=deadline):
        remaining = until - time.monotonic()
        if remaining <= 0:
            raise AnalysisError('timeout', 'RunPod Docling 표 보완 제한 시간을 초과했습니다.')
        with opener.open(request, timeout=min(60, remaining)) as response:
            raw = response.read(MAX_RESULT + 1)
            if len(raw) > MAX_RESULT:
                raise AnalysisError('invalid_engine_result', 'Docling 응답 크기가 제한을 넘었습니다.')
            return response.status, json.loads(raw)

    try:
        while job is None:
            try:
                status, result = send(urllib.request.Request(url, data=data, headers={
                    **headers, 'Content-Type': 'image/png',
                    'X-A4B-Docling-Timeout': str(max(1, int(deadline - time.monotonic())))}), until=queue_deadline)
            except urllib.error.HTTPError as exc:
                if exc.code != 429:
                    raise
                exc.close()
                if time.monotonic() + RETRY_SECONDS >= queue_deadline:
                    raise AnalysisError('busy', 'RunPod이 다른 문서를 처리 중입니다. 표 보완은 재검토가 필요합니다.') from exc
                time.sleep(RETRY_SECONDS)
                continue
            if status != 202:
                raise AnalysisError('invalid_engine_result', 'Docling 작업 ID가 반환되지 않았습니다.')
            job = str(uuid.UUID(result['job_id']))
        while True:
            try:
                status, result = send(urllib.request.Request(url + '/' + job, headers=headers))
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504):
                    raise
                exc.close()
                status = 202
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead):
                status = 202
            if status == 200:
                if (not isinstance(result, dict) or result.get('engine') != 'docling'
                        or result.get('source_sha256') != sha256(source)
                        or not isinstance(result.get('version'), str)
                        or not isinstance(result.get('blocks'), list)
                        or not all(isinstance(b, dict) for b in result['blocks'])
                        or not isinstance(result.get('model'), dict)
                        or result['model'].get('device') != 'cuda'):
                    raise AnalysisError('invalid_engine_result', 'Docling 결과가 요청한 이미지 또는 GPU 계약과 다릅니다.')
                finished = True
                return result
            if status != 202:
                raise AnalysisError('invalid_engine_result', 'Docling 작업 상태가 올바르지 않습니다.')
            time.sleep(max(0, min(1, deadline - time.monotonic())))
    except urllib.error.HTTPError as exc:
        raise AnalysisError('remote_docling_error', f'RunPod 이미지 표 보완 요청이 실패했습니다 (HTTP {exc.code}).') from exc
    except AnalysisError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise AnalysisError('remote_docling_error', 'RunPod 이미지 표 보완 연결 또는 응답을 확인하세요.') from exc
    finally:
        if job and not finished:
            try:
                with opener.open(urllib.request.Request(url + '/' + job, method='DELETE', headers=headers), timeout=2):
                    pass
            except (OSError, ValueError):
                pass
