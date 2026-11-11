"""Offline destination/provenance tests. No live model or external connector calls."""
from copy import deepcopy
from contextlib import nullcontext
import importlib.util
import io
import json
import os
from unittest.mock import patch
from pathlib import Path
import sys
import urllib.request

import httpx
import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/verify'))
from qwen_live_policy import endpoint_policy, assert_model_url, assert_same_environment, require_complete_baseline
from qwen_live_trace import Trace

LOCAL='http://127.0.0.1:11434/v1'
REMOTE='https://example.proxy.runpod.net/v1'


def load_script(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts/verify'/name)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_local_requires_the_exact_explicit_opt_in():
    with pytest.raises(ValueError):endpoint_policy(LOCAL)
    with pytest.raises(ValueError):endpoint_policy(LOCAL,'http://127.0.0.1:12345/v1')
    assert endpoint_policy(LOCAL,LOCAL)['kind']=='local'
    assert endpoint_policy('http://[::1]:11434/v1','http://[::1]:11434/v1')['host']=='::1'
    assert endpoint_policy(REMOTE)['kind']=='runpod'


@pytest.mark.parametrize('url',[
    'http://localhost:11434/v1','http://192.168.1.8:11434/v1','http://127.0.0.2:11434/v1',
    'http://127.0.0.1:11434/v1?key=x','http://secret@127.0.0.1:11434/v1',
    'http://127.0.0.1:11434/v1#x','http://127.0.0.1:11434/other',
    'http://example.proxy.runpod.net/v1','https://example.proxy.runpod.net.evil.invalid/v1',
])
def test_endpoint_policy_does_not_expand_local_authorization(url):
    with pytest.raises(ValueError):endpoint_policy(url,url)


@pytest.mark.parametrize('url',[
    'http://127.0.0.1:11435/api/chat','https://127.0.0.1:11434/api/chat',
    'http://127.0.0.1:11434/api/pull','http://127.0.0.1:11434/api/chat?redirect=x',
    'http://external.invalid:11434/api/chat','http://user@127.0.0.1:11434/api/chat',
])
def test_model_url_guard_restricts_origin_port_and_api_path(url):
    with pytest.raises(OSError):assert_model_url(url,endpoint_policy(LOCAL,LOCAL))


def test_trace_blocks_both_transports_before_network_and_redirect_escape(monkeypatch):
    network=[]
    def urlopen(request,*args,**kwargs):
        network.append(request.full_url)
        return io.BytesIO(b'{"model":"qwen-test:latest","message":{"content":"{}"}}')
    monkeypatch.setattr(urllib.request,'urlopen',urlopen)
    transport=httpx.MockTransport(lambda request: httpx.Response(302,headers={'location':'http://external.invalid/api/chat'}))
    tracer=Trace(LOCAL,allow_local_endpoint=LOCAL,models=['qwen-test'])
    with tracer.installed(),httpx.Client(transport=transport,follow_redirects=True) as client:
        with pytest.raises(OSError):urllib.request.urlopen('http://external.invalid/api/chat')
        with pytest.raises(OSError):client.get(LOCAL+'/chat/completions')
        with pytest.raises(OSError):urllib.request.HTTPRedirectHandler().redirect_request(None,None,302,'',{},LOCAL+'/models')
        request=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=b'{"model":"qwen-test"}')
        assert json.load(urllib.request.urlopen(request))['model']=='qwen-test:latest'
    assert network==['http://127.0.0.1:11434/api/chat']
    assert tracer.calls[0]['status']=='returned'


def test_trace_rejects_unselected_or_misidentified_models():
    tracer=Trace(LOCAL,allow_local_endpoint=LOCAL,models=['qwen-test'])
    with pytest.raises(ValueError):tracer.start('ollama_native','different-model')
    call=tracer.start('ollama_native','qwen-test')
    with pytest.raises(ValueError):tracer.finish(call,status='returned',actual_model='qwen-other',raw_output={'content':'{}'})
    assert call['status']=='failed'
    assert call['actual_model']=='qwen-other'


@pytest.mark.parametrize('asynchronous',[False,True])
def test_httpx_never_follows_even_same_origin_model_api_redirects(asynchronous,monkeypatch):
    requests=[]
    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(302,headers={'location':'http://127.0.0.1:11434/api/tags'})
    transport=httpx.MockTransport(respond)
    tracer=Trace(LOCAL,allow_local_endpoint=LOCAL,models=['qwen-test'])
    async def send(client,request):return respond(request)
    # Exercise the async guard without Windows event-loop socket initialization.
    monkeypatch.setattr(httpx.AsyncClient,'_send_single_request',send)
    with tracer.installed(),pytest.raises(OSError,match='redirect'):
        if asynchronous:
            coroutine=httpx.AsyncClient._send_single_request(None,httpx.Request('GET',LOCAL+'/models'))
            try:coroutine.send(None)
            finally:coroutine.close()
        else:
            with httpx.Client(transport=transport,follow_redirects=True) as client:client.get(LOCAL+'/models')
    assert requests==[LOCAL+'/models']


def test_environment_comparison_rejects_legacy_remote_weights_or_dependency_changes():
    local={'kind':'local','endpoint':LOCAL,'dependencies':{'langgraph':'1'},'models':{'qwen-test':{'digest':'abc'}}}
    assert_same_environment(local,deepcopy(local))
    for other in (None,{**local,'kind':'runpod','endpoint':REMOTE},
                  {**local,'dependencies':{'langgraph':'2'}},{**local,'models':{'qwen-test':{'digest':'xyz'}}}):
        with pytest.raises(ValueError):assert_same_environment(local,other)


def full_baseline():
    corpus=json.loads((ROOT/'docs/qwen-live-evaluation-cases.json').read_text(encoding='utf-8'))
    baseline={'phase':'baseline','gold_sha256':corpus['gold_sha256'],'status':'completed',
              'cases':[{'id':c['id'],'status':'failed' if i==0 else 'completed','live_model_calls':1} for i,c in enumerate(corpus['cases'])]}
    return corpus,baseline


def test_after_accepts_completed_measurement_including_real_failures():
    corpus,baseline=full_baseline()
    require_complete_baseline(baseline,corpus)


@pytest.mark.parametrize('mutation',['paused','missing','duplicate','not_run','zero_live'])
def test_after_rejects_incomplete_or_duplicate_baseline(mutation):
    corpus,baseline=full_baseline()
    if mutation=='paused':baseline['status']='paused'
    elif mutation=='missing':baseline['cases'].pop()
    elif mutation=='duplicate':baseline['cases'][-1]=baseline['cases'][0]
    elif mutation=='not_run':baseline['cases'][0]['status']='not_run'
    else:
        for row in baseline['cases']:row['live_model_calls']=0
    with pytest.raises(ValueError):require_complete_baseline(baseline,corpus)


def test_merge_rejects_local_legacy_mix_even_with_matching_model_names():
    corpus,run=full_baseline()
    run.update(code_manifest={'source_sha256':'same'},effective_process_settings={'A4B_MAIN_MODEL':'qwen-test'})
    shard=deepcopy(run);shard['cases']=[]
    shard['evaluation_environment']={'kind':'local','endpoint':LOCAL}
    with pytest.raises(ValueError,match='environment'):
        load_script('merge-qwen-live-runs.py').merge([run,shard],corpus)


def test_live_probe_checks_capability_and_environment_before_inference(monkeypatch):
    runner=load_script('verify-qwen-live-evaluation.py')
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL',LOCAL)
    monkeypatch.setenv('A4B_MAIN_MODEL','qwen-main')
    monkeypatch.setenv('A4B_CAPABILITY_MODEL','qwen-cap')
    requests=[]
    def urlopen(request,**kwargs):
        requests.append(request.full_url)
        if request.full_url.endswith('/models'):body={'data':[{'id':'qwen-main:latest'}]}
        else:raise AssertionError('No inference or later probe should occur with missing capability model')
        return io.BytesIO(json.dumps(body).encode())
    monkeypatch.setattr(urllib.request,'urlopen',urlopen)
    tracer=Trace(LOCAL,allow_local_endpoint=LOCAL,models=['qwen-main','qwen-cap'])
    with pytest.raises(ValueError,match='absent'):runner.live_probe(tracer,{'kind':'local'})
    assert requests==[LOCAL+'/models']
    assert tracer.calls==[]


def test_live_probe_rejects_environment_difference_before_inference(monkeypatch):
    runner=load_script('verify-qwen-live-evaluation.py')
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama');monkeypatch.setenv('A4B_MAIN_BASE_URL',LOCAL)
    monkeypatch.setenv('A4B_MAIN_MODEL','qwen-main');monkeypatch.setenv('A4B_CAPABILITY_MODEL','qwen-main')
    def urlopen(request,**kwargs):
        path=request.full_url.rsplit('/',1)[-1]
        body={'models':{'data':[{'id':'qwen-main:latest'}]},
              'tags':{'models':[{'name':'qwen-main:latest','digest':'new-digest','size':123}]},
              'version':{'version':'test-server'},'show':{'parameters':'num_ctx 32768','model_info':{'qwen.context_length':32768}}}[path]
        return io.BytesIO(json.dumps(body).encode())
    monkeypatch.setattr(urllib.request,'urlopen',urlopen)
    import agent_builder.design.requirement_ledger as ledger
    monkeypatch.setattr(ledger,'requirement_transport',lambda *args:pytest.fail('Inference must not start'))
    tracer=Trace(LOCAL,allow_local_endpoint=LOCAL,models=['qwen-main'])
    with pytest.raises(ValueError,match='environment'):
        runner.live_probe(tracer,{'kind':'local'},{'kind':'local','models':{'qwen-main':{'digest':'old-digest'}}})
    assert tracer.calls==[]


@pytest.mark.parametrize('interrupt_at',['case','probe'])
def test_progress_and_interruption_preserve_completed_and_inflight_evidence(tmp_path,monkeypatch,interrupt_at):
    runner=load_script('verify-qwen-live-evaluation.py');output=tmp_path/'synthetic-only.json'
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama');monkeypatch.setenv('A4B_MAIN_BASE_URL',LOCAL)
    monkeypatch.setenv('A4B_MAIN_MODEL','qwen-main');monkeypatch.setenv('A4B_CAPABILITY_MODEL','qwen-main')
    monkeypatch.setattr(runner,'code_manifest',lambda:{'source_sha256':'synthetic-test'})
    monkeypatch.setattr(runner,'environment_identity',lambda *args:{'kind':'local','endpoint':LOCAL})
    monkeypatch.setattr(Trace,'installed',lambda self:nullcontext(self))
    monkeypatch.setattr(sys,'argv',['verify','--phase','baseline','--live','--output',str(output),
                                  '--allow-local-endpoint',LOCAL,'--case','R01','--case','R02','--case','C01'])
    def probe(tracer,*args):
        if interrupt_at=='probe':
            tracer.start('ollama_native','qwen-main')
            raise KeyboardInterrupt()
        return {'structured_probe':'synthetic_test_stub'}
    def run(case,model,tracer,directory):
        snapshot=json.loads(output.read_text(encoding='utf-8'))
        assert snapshot['status']=='running' and snapshot['active_case_id']==case['id']
        assert case['id'] in snapshot['not_run_case_ids']
        tracer.stage='conditions';call=tracer.start('ollama_native',model)
        assert json.loads(output.read_text(encoding='utf-8'))['active_case_calls'][-1]['status']=='started'
        if case['id']=='R02':raise KeyboardInterrupt()
        tracer.dispatch(call);call['http_responses_received']+=1
        tracer.finish(call,status='returned',actual_model=model,raw_output={'content':'synthetic-only'})
        assert json.loads(output.read_text(encoding='utf-8'))['active_case_calls'][-1]['status']=='returned'
        return {'id':case['id'],'status':'completed','live_model_calls':1,'model_calls':[call],
                'expected_route':'READY','actual_route':'READY','elapsed_seconds':0.01}
    monkeypatch.setattr(runner,'live_probe',probe);monkeypatch.setattr(runner,'run_case',run)
    # The runner changes serving flags and removes proxies even on interruption.
    with patch.dict(os.environ):
        runner.main()
    report=json.loads(output.read_text(encoding='utf-8'))
    assert report['status']=='paused'
    assert len(report['cases'])==3
    if interrupt_at=='case':
        assert report['cases'][0]['status']=='completed'
        assert all(row['status']=='not_run' for row in report['cases'][1:])
        assert report['interrupted_case_id']=='R02'
        assert report['active_case_calls'][0]['status']=='started'
        assert report['inference_calls']==1
        assert report['model_call_records']==2 and report['dispatched_model_requests']==1
    else:
        assert all(row['status']=='not_run' for row in report['cases'])
        assert report['interrupted_case_id'] is None
        assert report['probe_calls'][0]['status']=='started'
        assert report['inference_calls']==0
        assert report['model_call_records']==1 and report['dispatched_model_requests']==0
