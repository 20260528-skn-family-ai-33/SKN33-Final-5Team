"""Synthetic Windows sharing failures; no live model/network requests."""
from contextlib import nullcontext
import importlib.util
import io
import json
import os
from unittest.mock import patch
from pathlib import Path
import sys
import threading
import urllib.request

import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/verify'))
import qwen_live_storage as storage
from qwen_live_storage import InstrumentationFatal
from qwen_live_trace import Trace

LOCAL='http://127.0.0.1:11434/v1'


def sharing_error():
    error=PermissionError(13,'synthetic-secret-must-not-be-logged')
    error.winerror=32
    return error


def test_atomic_writer_retries_reader_sharing_violation_then_preserves_valid_json(tmp_path,monkeypatch):
    path=tmp_path/'run.json';path.write_text('{"old":true}',encoding='utf-8')
    original=storage.os.replace;attempts=[]
    def replace(source,destination):
        attempts.append(1)
        assert json.loads(path.read_text(encoding='utf-8'))=={'old':True}
        if len(attempts)<3:raise sharing_error()
        original(source,destination)
    monkeypatch.setattr(storage.os,'replace',replace)
    storage.write_report(path,{'status':'running','complete':True},retry_delay=0)
    assert len(attempts)==3
    assert json.loads(path.read_text(encoding='utf-8'))=={'status':'running','complete':True}
    assert list(tmp_path.glob('*.writing'))==[]


def test_atomic_writer_permanent_failure_is_fatal_and_does_not_disclose_error_message(tmp_path,monkeypatch,capsys):
    path=tmp_path/'run.json';path.write_text('{"old":true}',encoding='utf-8');attempts=[]
    def replace(*args):attempts.append(1);raise sharing_error()
    monkeypatch.setattr(storage.os,'replace',replace)
    with pytest.raises(InstrumentationFatal) as raised:storage.write_report(path,{'new':True},retry_delay=0)
    assert not isinstance(raised.value,Exception)
    assert len(attempts)==5
    assert json.loads(path.read_text(encoding='utf-8'))=={'old':True}
    output=capsys.readouterr().err
    assert 'synthetic-secret' not in output
    assert json.loads(output)['winerror']==32
    assert json.loads(output)['replace_attempts']==5
    recovery=list(tmp_path.glob('*.writing'))
    assert len(recovery)==1
    assert json.loads(recovery[0].read_text(encoding='utf-8'))=={'new':True}
    assert json.loads(output)['recovery_complete'] is True
    assert json.loads(output)['recovery_file']==recovery[0].name


def test_stderr_failure_never_converts_fatal_to_an_application_exception(monkeypatch):
    class BrokenStderr:
        def write(self,*args):raise PermissionError('synthetic-secret')
        def flush(self):raise PermissionError('synthetic-secret')
    monkeypatch.setattr(storage.sys,'stderr',BrokenStderr())
    error=storage.instrumentation_failure('synthetic',sharing_error())
    assert isinstance(error,InstrumentationFatal) and not isinstance(error,Exception)
    assert error.diagnostic['stderr_error_type']=='PermissionError'


@pytest.mark.skipif(sys.platform!='win32',reason='Actual Windows file sharing semantics')
@pytest.mark.parametrize('release_reader',[True,False])
def test_real_windows_reader_lock_retries_or_preserves_complete_response_snapshot(tmp_path,release_reader):
    import ctypes
    from ctypes import wintypes
    path=tmp_path/'locked.json';path.write_text('{"old":true}',encoding='utf-8')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    handle=kernel.CreateFileW(str(path),0x80000000,3,None,3,0,None)
    assert handle!=ctypes.c_void_p(-1).value,ctypes.get_last_error()
    closed=threading.Event()
    def close():
        if not closed.is_set():kernel.CloseHandle(handle);closed.set()
    timer=threading.Timer(0.075,close) if release_reader else None
    if timer:timer.start()
    result={'status':'running','active_case_calls':[{'status':'returned','raw_output':{'content':'synthetic-model-response'}}]}
    try:
        if release_reader:
            storage.write_report(path,result)
            assert json.loads(path.read_text(encoding='utf-8'))==result
        else:
            with pytest.raises(InstrumentationFatal) as raised:storage.write_report(path,result,replace_attempts=2,retry_delay=0)
            assert raised.value.diagnostic['winerror'] in (5,32,33)
            assert json.loads(path.read_text(encoding='utf-8'))=={'old':True}
            recovery=tmp_path/raised.value.diagnostic['recovery_file']
            assert json.loads(recovery.read_text(encoding='utf-8'))==result
    finally:
        close()
        if timer:timer.join()


@pytest.mark.parametrize('fail_on_change',[1,2,3])
def test_callback_failure_bypasses_application_exception_fallback_and_stops_http(fail_on_change,monkeypatch):
    dispatched=[];fallback=[];callbacks=[]
    def urlopen(request,*args,**kwargs):
        dispatched.append(request.full_url)
        return io.BytesIO(b'{"model":"qwen-test","message":{"content":"{}"}}')
    monkeypatch.setattr(urllib.request,'urlopen',urlopen)
    tracer=Trace(LOCAL,allow_local_endpoint=LOCAL,models=['qwen-test'])
    def save():
        callbacks.append(1)
        if len(callbacks)==fail_on_change:raise sharing_error()
    tracer.on_change=save
    request=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=b'{"model":"qwen-test"}')
    with tracer.installed(),pytest.raises(InstrumentationFatal):
        try:urllib.request.urlopen(request)
        except Exception:fallback.append('would run another application/model path')
    assert fallback==[]
    assert len(dispatched)==(1 if fail_on_change==3 else 0)
    assert tracer.counts()['dispatched_model_requests']==len(dispatched)
    assert tracer.counts()['model_responses_received']==len(dispatched)
    if fail_on_change<3:assert tracer.calls[0]['dispatch_state']=='not_dispatched'
    else:assert tracer.calls[0]['status']=='returned'


def test_pending_snapshot_marks_dispatch_uncertainty_without_claiming_server_inference():
    snapshots=[];tracer=Trace(LOCAL,allow_local_endpoint=LOCAL,models=['qwen-test'])
    tracer.on_change=lambda:snapshots.append(json.loads(json.dumps(tracer.calls)))
    call=tracer.start('ollama_native','qwen-test');tracer.dispatch(call)
    assert snapshots[-1][0]['dispatch_state']=='dispatch_pending'
    assert snapshots[-1][0]['http_dispatch_attempts']==0
    assert tracer.counts()['dispatched_model_requests']==1
    assert tracer.counts()['model_responses_received']==0


@pytest.mark.parametrize('failure_stage',['initial','active_case','returned','final'])
def test_runner_stops_on_snapshot_failure_without_app_fallback_or_next_case(tmp_path,monkeypatch,failure_stage):
    spec=importlib.util.spec_from_file_location('storage_runner',ROOT/'scripts/verify/verify-qwen-live-evaluation.py')
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    output=tmp_path/'synthetic.json';ran=[];probe=[];saved=[];failure_raised=[]
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama');monkeypatch.setenv('A4B_MAIN_BASE_URL',LOCAL)
    monkeypatch.setenv('A4B_MAIN_MODEL','qwen-test');monkeypatch.setenv('A4B_CAPABILITY_MODEL','qwen-test')
    monkeypatch.setattr(runner,'code_manifest',lambda:{'source_sha256':'synthetic'})
    monkeypatch.setattr(runner,'environment_identity',lambda *args:{'kind':'local'})
    monkeypatch.setattr(Trace,'installed',lambda self:nullcontext(self))
    monkeypatch.setattr(sys,'argv',['verify','--phase','baseline','--live','--output',str(output),
                                  '--allow-local-endpoint',LOCAL,'--case','R01','--case','R02'])
    original_write=runner.write_report
    def write(path,report):
        calls=report.get('active_case_calls',[])
        trigger=(failure_stage=='initial' and not saved or
                 failure_stage=='active_case' and calls and calls[-1]['status']=='started' or
                 failure_stage=='returned' and calls and calls[-1]['status']=='returned' or
                 failure_stage=='final' and report['status']=='completed')
        if trigger and not failure_raised:
            failure_raised.append(True)
            raise storage.instrumentation_failure('synthetic_snapshot',sharing_error())
        saved.append(json.loads(json.dumps(report)));original_write(path,report)
    def live_probe(*args):probe.append(True);return {'synthetic':True}
    def run_case(case,model,tracer,directory):
        ran.append(case['id']);tracer.stage='conditions'
        call=tracer.start('ollama_native',model);tracer.dispatch(call);call['http_responses_received']+=1
        tracer.finish(call,status='returned',actual_model=model,raw_output={'content':'synthetic'})
        return {'id':case['id'],'status':'completed','live_model_calls':1,'model_calls':[call]}
    monkeypatch.setattr(runner,'write_report',write);monkeypatch.setattr(runner,'live_probe',live_probe)
    monkeypatch.setattr(runner,'run_case',run_case)
    # The CLI deliberately changes process-wide serving settings and proxies.
    # Keep those changes inside this fixture, including when the CLI fails.
    with patch.dict(os.environ):
        assert runner.main()==2
    if failure_stage=='final':
        assert ran==['R01','R02']
        assert saved[-1]['status']=='running'
    else:
        report=json.loads(output.read_text(encoding='utf-8'))
        assert report['status']=='blocked_instrumentation'
        assert all(row['status']=='not_run' for row in report['cases'])
        if failure_stage=='initial':assert ran==[] and probe==[]
        else:
            assert ran==['R01']
            assert report['dispatched_model_requests']==(1 if failure_stage=='returned' else 0)
            assert report['inference_calls']==(1 if failure_stage=='returned' else 0)
