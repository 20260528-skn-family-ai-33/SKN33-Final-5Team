"""Fault-injected HTTP and fake clock: no GPU/service mutation or real waiting."""
import json
import subprocess
import urllib.error
from unittest.mock import patch

import pytest
from PIL import Image
from documents.analysis.adapters import LocalEngines
from documents.analysis.pipeline import run


@pytest.mark.parametrize('failure', ['504', 'deadline', 'cpu_fallback', 'null_result', 'null_scores'])
def test_optional_table_ocr_preserves_base_results_and_shared_deadline(tmp_path, monkeypatch, failure):
    monkeypatch.setenv('A4B_DOCUMENT_TIMEOUT_MS', '60000')
    monkeypatch.delenv('A4B_DOCUMENT_DEADLINE_UNIX_MS', raising=False)
    monkeypatch.setenv('A4B_PARSER_URL', 'https://parser.invalid')
    monkeypatch.setenv('A4B_PARSER_FALLBACK', 'cpu' if failure == 'cpu_fallback' else 'off')
    now = [0.0]
    events = []
    class Engines(LocalEngines):
        def run(self, engine, **kwargs):
            events.append((engine, kwargs.get('phase')))
            if engine == 'layout':
                return dict(version='test', model={}, outputs=[dict(source=str(p), result={'boxes': [
                    dict(label='text', score=.99, coordinate=[0,0,100,20]),
                    dict(label='table', score=.99, coordinate=[0,25,100,90])]
                    }) for p in kwargs['sources']])
            if engine == 'docling':
                now[0] = 35
                return dict(engine='docling', version='test', blocks=[dict(kind='table', text='Item Count\nMilk 1',
                    data={'cells': [dict(row=r, column=c, text=t) for r, row in enumerate(
                        [['Item', 'Count'], ['Milk', '1']]) for c, t in enumerate(row)]})])
            return super().run(engine, **kwargs)
    class Response:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_): return json.dumps(self.data).encode()
    def urlopen(request, timeout):
        sources = json.loads(request.data)['sources']
        if events[-1][1] == 'table_verification':
            assert ('docling', 'supplement') in events
            assert timeout == pytest.approx(5)
            now[0] += 3 if failure == 'cpu_fallback' else timeout
            if failure in ('null_result', 'null_scores'):
                result = None if failure == 'null_result' else {'rec_texts': [], 'rec_boxes': [], 'rec_scores': None}
                return Response(dict(version='test', outputs=[dict(source=p['name'], result=result) for p in sources]))
            if failure == 'deadline': raise TimeoutError('fixture')
            raise urllib.error.HTTPError(request.full_url, 504, 'fixture', {}, None)
        now[0] = 5
        return Response(dict(version='test', outputs=[dict(source=p['name'], result={
            'rec_texts': ['preserved body'], 'rec_boxes': [[0,0,80,15]], 'rec_scores': [.999]}) for p in sources]))
    def cpu(*args, **kwargs):
        assert failure == 'cpu_fallback'
        assert kwargs['timeout'] == pytest.approx(2)
        now[0] += kwargs['timeout']
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    source = tmp_path / 'receipt.png'
    Image.new('RGB', (100,100), 'white').save(source)
    with patch('documents.analysis.adapters.time.monotonic', side_effect=lambda: now[0]), \
            patch('documents.analysis.adapters.urllib.request.urlopen', side_effect=urlopen), \
            patch('documents.analysis.adapters.subprocess.run', side_effect=cpu) as local:
        engines = Engines(tmp_path / 'work')
        payload = run(source, tmp_path / 'work', engines=engines)
        assert engines.image_deadline == 40
    assert now[0] == 40
    assert payload['parser_status'] == 'partial'
    assert 'preserved body' in payload['text'] and 'Milk 1' in payload['text']
    assert any(w.startswith('table_verification:') for w in payload['warnings'])
    assert local.call_count == (1 if failure == 'cpu_fallback' else 0)
    assert events.index(('docling', 'supplement')) < events.index(('paddleocr', 'table_verification'))


def test_expired_verification_budget_skips_remote_and_local(tmp_path, monkeypatch):
    monkeypatch.setenv('A4B_DOCUMENT_TIMEOUT_MS', '60000')
    monkeypatch.delenv('A4B_DOCUMENT_DEADLINE_UNIX_MS', raising=False)
    monkeypatch.setenv('A4B_PARSER_URL', 'https://parser.invalid')
    source = tmp_path / 'image.png'
    Image.new('RGB', (10,10), 'white').save(source)
    from documents.analysis.contracts import AnalysisError
    with patch('documents.analysis.adapters.time.monotonic', return_value=0):
        engines = LocalEngines(tmp_path / 'work')
    with patch('documents.analysis.adapters.time.monotonic', return_value=40), \
            patch('documents.analysis.adapters.remote_engine') as remote, \
            patch('documents.analysis.adapters.subprocess.run') as local:
        with pytest.raises(AnalysisError, match='시간'):
            engines.run('paddleocr', sources=[source], phase='table_verification')
        remote.assert_not_called()
        local.assert_not_called()
