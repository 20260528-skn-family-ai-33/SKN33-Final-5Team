"""Bounded bytes may still fail JSON decoding before metadata validation."""
import io
import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from documents.analysis import adapters
from documents.analysis.contracts import AnalysisError, MAX_RESULT
from documents.analysis.pipeline import run
from tests.documents.analysis.test_table_quality_boundaries import Replay


def raw_input(mode):
    if mode=='integer':
        limit=sys.get_int_max_str_digits()
        if not limit: pytest.skip('Interpreter integer conversion limit is disabled; do not change it')
        return ('{"version":"test","model":{"value":'+'9'*max(5000,limit+1)+'},"outputs":[]}').encode()
    if mode=='nested': return ('['*max(10000,sys.getrecursionlimit()*2)+']'*max(10000,sys.getrecursionlimit()*2)).encode()
    return {'syntax':b'{','utf8':b'\xff','null':b'null','array':b'[]','empty':b'',
            'oversize':b' '*(MAX_RESULT+1)}[mode]


@pytest.mark.parametrize('route',['local','remote','fallback','remote_cpu_enabled'])
@pytest.mark.parametrize('target',['supplement','table_verification'])
@pytest.mark.parametrize('mode',['normal','integer','nested','syntax','utf8','null','array','empty','oversize'])
def test_decode_failure_is_contained_at_input_boundary(tmp_path,monkeypatch,route,target,mode):
    table=dict(rec_texts=['Item','Count','Milk','1'],rec_boxes=[
        [0,0,50,20],[100,0,150,20],[0,30,50,50],[100,30,150,50]],rec_scores=[.999]*4)
    phases=[]; cpu_calls=[]
    def raw(names):
        if phases[-1]==target and mode!='normal':return raw_input(mode)
        return json.dumps(dict(version='test',outputs=[dict(source=s,result=table if phases[-1]=='table_verification'
            else dict(rec_texts=['BODY'],rec_boxes=[[0,0,30,15]],rec_scores=[.99])) for s in names])).encode()
    class Engines(adapters.LocalEngines):
        def run(self,engine,**kw):
            phases.append(kw.get('phase'))
            if engine in ('layout','docling'): return Replay(table).run(engine,**kw)
            return super().run(engine,**kw)
    def remote(request,timeout):
        if route=='fallback':raise urllib.error.HTTPError(request.full_url,503,'unavailable',{},None)
        return io.BytesIO(raw([s['name'] for s in json.loads(request.data)['sources']]))
    def cpu(command,**kw):
        cpu_calls.append(phases[-1])
        names=json.loads(Path(command[-2]).read_text(encoding='utf-8'))['sources']
        Path(command[-1]).write_bytes(raw(names))
        return SimpleNamespace(returncode=0)
    monkeypatch.delenv('A4B_PARSER_URL',raising=False)
    if route!='local':monkeypatch.setenv('A4B_PARSER_URL','https://parser.invalid')
    monkeypatch.setenv('A4B_PARSER_FALLBACK','off' if route=='remote' else 'cpu')
    monkeypatch.setattr(adapters.urllib.request,'urlopen',remote)
    monkeypatch.setattr(adapters.subprocess,'run',cpu)
    source=tmp_path/'input.png';Image.new('RGB',(200,200),'white').save(source)
    engine=Engines(tmp_path/'work');payload=run(source,tmp_path/'work',engines=engine)
    assert 'BASE TABLE' in payload['text']
    if target=='table_verification' or mode=='normal':assert 'BODY' in payload['text']
    assert payload['parser_status']==('succeeded' if mode=='normal' else 'partial')
    if mode!='normal':
        code='invalid_engine_result'
        reason=('image_ocr:' if target=='supplement' else 'table_verification:')+code
        assert reason in payload['warnings']
        attempt=next(a for a in engine.attempts if a['phase']==target)
        assert attempt['status']=='failed' and attempt['error_code']==code
        assert 'version' not in attempt and 'model' not in attempt
        scope=payload['document_analysis']['review_scopes'].get(reason)
        assert bool(scope)==(target=='table_verification')
    if route in ('remote','remote_cpu_enabled'):assert not cpu_calls
    if route=='fallback':assert all(a.get('remote_error')=='remote_parser_unavailable' for a in engine.attempts)
    json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')


@pytest.mark.parametrize('bad_batch',[0,1])
@pytest.mark.parametrize('mode',['integer','nested','syntax','utf8'])
def test_remote_batches_reject_entire_attempt_on_decode_error(tmp_path,monkeypatch,bad_batch,mode):
    sources=[tmp_path/'a.png',tmp_path/'b.png']
    for p in sources:p.write_bytes(b'image')
    monkeypatch.setattr(adapters,'REMOTE_MAX_SOURCES',1)
    monkeypatch.setenv('A4B_PARSER_URL','https://parser.invalid')
    monkeypatch.setenv('A4B_PARSER_FALLBACK','cpu')
    calls=[]
    def remote(request,timeout):
        names=[s['name'] for s in json.loads(request.data)['sources']]
        index=len(calls);calls.append(names)
        return io.BytesIO(raw_input(mode) if index==bad_batch else json.dumps(dict(version='test',outputs=[
            dict(source=s,result={}) for s in names])).encode())
    monkeypatch.setattr(adapters.urllib.request,'urlopen',remote)
    def unexpected(*a,**kw):raise AssertionError('Invalid remote JSON must not invoke CPU fallback')
    monkeypatch.setattr(adapters.subprocess,'run',unexpected)
    engine=adapters.LocalEngines(tmp_path/'work')
    with pytest.raises(AnalysisError) as caught:engine.run('paddleocr',sources=sources,phase='supplement')
    assert caught.value.code=='invalid_engine_result' and len(calls)==bad_batch+1
    assert engine.attempts[0]['status']=='failed' and 'version' not in engine.attempts[0]


@pytest.mark.parametrize('stage',['submit','poll'])
@pytest.mark.parametrize('mode',['integer','nested','syntax','utf8','empty','oversize'])
def test_remote_docling_decode_boundary_and_cancellation(tmp_path,monkeypatch,stage,mode):
    from documents.analysis import remote_docling
    source=tmp_path/'input.png';Image.new('RGB',(200,200),'white').save(source)
    job='12345678-1234-1234-1234-123456789abc'
    calls=[]
    def open_response(request,**kw):
        calls.append(request.get_method())
        if request.get_method()=='DELETE':return io.BytesIO(b'{}')
        raw=raw_input(mode) if stage=='submit' or request.get_method()=='GET' else json.dumps({'job_id':job}).encode()
        response=io.BytesIO(raw);response.status=202 if request.get_method()=='POST' else 200
        return response
    monkeypatch.setenv('A4B_PARSER_URL','https://parser.invalid')
    monkeypatch.setenv('A4B_PARSER_API_KEY','test')
    monkeypatch.delenv('A4B_DOCLING_LOCAL_ONLY',raising=False)
    monkeypatch.setattr(remote_docling.urllib.request,'build_opener',lambda *a:SimpleNamespace(open=open_response))
    engine=adapters.LocalEngines(tmp_path/'work')
    with pytest.raises(AnalysisError) as caught:engine.run('docling',source=source,phase='supplement')
    expected={'invalid_engine_result', 'remote_docling_error'} if mode=='nested' and stage=='submit' else {'invalid_engine_result'}
    assert caught.value.code in expected
    assert calls==(['POST'] if stage=='submit' else ['POST','GET','DELETE'])
    assert engine.attempts[0]['error_code']==caught.value.code
    json.dumps(engine.attempts,ensure_ascii=False,allow_nan=False).encode('utf-8')


@pytest.mark.parametrize('route',['remote','local','docling'])
def test_decoder_recursion_error_is_explicitly_contained(tmp_path,monkeypatch,route):
    # Python 3.14's decoder accepts deeply nested arrays; simulate the explicit
    # decoder exception used by older runtimes without changing process limits.
    source=tmp_path/'input.png';Image.new('RGB',(200,200),'white').save(source)
    def decode(raw,*a,**kw):raise RecursionError('decoder nesting limit')
    monkeypatch.setattr(adapters.json,'loads',decode)
    monkeypatch.setenv('A4B_PARSER_FALLBACK','off')
    monkeypatch.setenv('A4B_PARSER_API_KEY','test')
    monkeypatch.delenv('A4B_DOCLING_LOCAL_ONLY',raising=False)
    monkeypatch.delenv('A4B_PARSER_URL',raising=False)
    if route!='local':monkeypatch.setenv('A4B_PARSER_URL','https://parser.invalid')
    def response(*a,**kw):
        stream=io.BytesIO(b'{}');stream.status=202
        return stream
    monkeypatch.setattr(adapters.urllib.request,'urlopen',response)
    monkeypatch.setattr(adapters.urllib.request,'build_opener',lambda *a:SimpleNamespace(open=response))
    def cpu(command,**kw):
        Path(command[-1]).write_bytes(b'{}')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(adapters.subprocess,'run',cpu)
    engine=adapters.LocalEngines(tmp_path/'work')
    with pytest.raises(AnalysisError) as caught:
        if route=='docling':engine.run('docling',source=source,phase='supplement')
        else:engine.run('paddleocr',sources=[source],phase='supplement')
    assert caught.value.code=='invalid_engine_result'
    assert engine.attempts[0]['error_code']=='invalid_engine_result'
