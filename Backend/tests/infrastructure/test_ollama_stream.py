"""OllamaPlanner streams by default so a proxy response limit (RunPod 524) does not cut generation."""
import json

import pytest

from agent_builder.design.nodes.direct_conversation import OllamaPlanner, _ollama_body


def ndjson(*chunks):
    return '\n'.join(json.dumps(c, ensure_ascii=False) for c in chunks).encode()


def test_streamed_chunks_are_joined_and_final_metrics_kept():
    raw = _ollama_body(ndjson({'message': {'content': '{"a"'}}, {'message': {'content': ':1}'}},
                              {'message': {'content': ''}, 'done': True, 'done_reason': 'stop', 'eval_count': 3}))
    assert raw['message']['content'] == '{"a":1}' and raw['done_reason'] == 'stop' and raw['eval_count'] == 3


def test_single_json_body_still_works():
    assert _ollama_body(json.dumps({'message': {'content': '{}'}, 'done_reason': 'stop'}).encode())['message']['content'] == '{}'


@pytest.mark.parametrize('setting,expected', [(None, False), ('on', True), ('off', False)])
def test_stream_flag_and_streamed_length_limit(monkeypatch, setting, expected):
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama'); monkeypatch.setenv('A4B_MAIN_BASE_URL', 'https://example.test/v1')
    if setting is None: monkeypatch.delenv('A4B_OLLAMA_STREAM', raising=False)
    else: monkeypatch.setenv('A4B_OLLAMA_STREAM', setting)
    seen = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return ndjson({'message': {'content': '{}'}}, {'done': True, 'done_reason': 'length'})
    monkeypatch.setattr('urllib.request.urlopen', lambda request, timeout: (seen.append(json.loads(request.data)), Response())[1])
    with pytest.raises(ValueError, match='출력 길이 제한'):
        OllamaPlanner('m').invoke([{'role': 'user', 'content': 'x'}])
    assert seen[0]['stream'] is expected


def test_streamed_error_chunk_is_reported(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama'); monkeypatch.setenv('A4B_MAIN_BASE_URL', 'https://example.test/v1')
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return ndjson({'message': {'content': '{'}}, {'error': 'model unloaded'})
    monkeypatch.setattr('urllib.request.urlopen', lambda request, timeout: Response())
    with pytest.raises(ValueError, match='model unloaded'):
        OllamaPlanner('m').invoke([{'role': 'user', 'content': 'x'}])


@pytest.mark.parametrize('codes,retries,ok', [([524], '2', True), ([524, 524], '2', True), ([524, 524, 524], '2', False),
                                               ([524], '0', False), ([500], '2', False)])
def test_gateway_errors_are_retried_a_bounded_number_of_times(monkeypatch, codes, retries, ok):
    import urllib.error
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama'); monkeypatch.setenv('A4B_MAIN_BASE_URL', 'https://example.test/v1')
    monkeypatch.setenv('A4B_OLLAMA_GATEWAY_RETRIES', retries)
    failures = list(codes); calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return ndjson({'message': {'content': '{}'}}, {'done': True, 'done_reason': 'stop'})
    def urlopen(request, timeout):
        calls.append(1)
        if failures:
            raise urllib.error.HTTPError(request.full_url, failures.pop(0), 'gateway', {}, None)
        return Response()
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    if ok:
        assert OllamaPlanner('m').invoke([{'role': 'user', 'content': 'x'}])['content'] == '{}'
        assert len(calls) == len(codes) + 1
    else:
        with pytest.raises(ValueError, match='로컬 모델 호출에 실패'):
            OllamaPlanner('m').invoke([{'role': 'user', 'content': 'x'}])


def test_keep_alive_is_sent_only_when_configured(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama'); monkeypatch.setenv('A4B_MAIN_BASE_URL', 'https://example.test/v1')
    seen = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return ndjson({'message': {'content': '{}'}}, {'done': True, 'done_reason': 'stop'})
    monkeypatch.setattr('urllib.request.urlopen', lambda request, timeout: (seen.append(json.loads(request.data)), Response())[1])
    monkeypatch.delenv('A4B_OLLAMA_KEEP_ALIVE', raising=False)
    OllamaPlanner('m').invoke([{'role': 'user', 'content': 'x'}])
    monkeypatch.setenv('A4B_OLLAMA_KEEP_ALIVE', '30m')
    OllamaPlanner('m').invoke([{'role': 'user', 'content': 'x'}])
    monkeypatch.setenv('A4B_OLLAMA_KEEP_ALIVE', '-1')
    OllamaPlanner('m').invoke([{'role': 'user', 'content': 'x'}])
    assert 'keep_alive' not in seen[0] and seen[1]['keep_alive'] == '30m' and seen[2]['keep_alive'] == -1
