"""Exercise worker failure envelopes through the real adapter and pipeline."""
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error
import pytest
from PIL import Image
from documents.analysis.adapters import LocalEngines
from documents.analysis.pipeline import run
from tests.documents.analysis.test_table_quality_boundaries import Replay


@pytest.mark.parametrize('route', ['local', 'fallback'])
@pytest.mark.parametrize('target', ['supplement', 'table_verification'])
@pytest.mark.parametrize('returncode', [0, 1])
@pytest.mark.parametrize('mode', ['normal', 'missing', 'null', 'false', 'zero', 'empty',
    'number', 'object', 'list', 'surrogate', 'outer_null', 'malformed', 'no_file'])
def test_worker_failure_values_preserve_basic_results(tmp_path, monkeypatch, route, target, returncode, mode):
    table=dict(rec_texts=['Item','Count','Milk','1'], rec_boxes=[
        [0,0,50,20],[100,0,150,20],[0,30,50,50],[100,30,150,50]], rec_scores=[.999]*4)
    phase=[]
    class Engines(LocalEngines):
        def run(self, engine, **kw):
            phase.append(kw.get('phase'))
            if engine in ('layout','docling'): return Replay(table).run(engine, **kw)
            return super().run(engine, **kw)
    def cpu(command, **kw):
        request=json.loads(Path(command[-2]).read_text(encoding='utf-8'))
        data=dict(version='test', outputs=[dict(source=s, result=table if phase[-1]=='table_verification'
            else dict(rec_texts=['BODY'], rec_boxes=[[0,0,30,15]], rec_scores=[.99])) for s in request['sources']])
        rc=0
        if phase[-1]==target:
            rc=returncode
            values={'normal':'engine_error','null':None,'false':False,'zero':0,'empty':'',
                    'number':12,'object':{'reason':'unavailable'},'list':['unavailable'],'surrogate':chr(0xD800)}
            if mode in values:
                data={'error':values[mode], 'version':chr(0xD800), 'model':{'score':float('nan')}}
            if mode=='outer_null': data=None
            if mode=='no_file': return SimpleNamespace(returncode=rc)
        raw='{' if phase[-1]==target and mode=='malformed' else json.dumps(data)
        Path(command[-1]).write_text(raw, encoding='utf-8')
        return SimpleNamespace(returncode=rc)
    def unavailable(*args, **kw): raise urllib.error.URLError('unavailable')
    monkeypatch.delenv('A4B_PARSER_URL', raising=False)
    if route=='fallback': monkeypatch.setenv('A4B_PARSER_URL','https://parser.invalid')
    monkeypatch.setenv('A4B_PARSER_FALLBACK','cpu')
    monkeypatch.setattr('documents.analysis.adapters.urllib.request.urlopen',unavailable)
    monkeypatch.setattr('documents.analysis.adapters.subprocess.run',cpu)
    source=tmp_path/'input.png'; Image.new('RGB',(200,200),'white').save(source)
    engines=Engines(tmp_path/'work'); payload=run(source,tmp_path/'work',engines=engines)
    assert 'BASE TABLE' in payload['text']
    if target=='table_verification': assert 'BODY' in payload['text']
    success=mode=='missing' and returncode==0
    assert payload['parser_status']==('succeeded' if success else 'partial')
    if not success:
        code='engine_error' if mode=='normal' or mode=='missing' else 'invalid_engine_result'
        reason=('image_ocr:' if target=='supplement' else 'table_verification:')+code
        assert reason in payload['warnings']
        attempts=[a for a in engines.attempts if a['phase']==target]
        assert attempts and all(a['error_code']==code and 'version' not in a and 'model' not in a for a in attempts)
        scopes=payload['document_analysis']['review_scopes']
        if target=='table_verification': assert scopes[reason]
    json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
