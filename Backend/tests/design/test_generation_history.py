import json
import sqlite3

import pytest

from infrastructure.persistence.generation_history import GenerationHistory, error_info
from infrastructure.persistence.storage import Storage
from infrastructure.persistence.ids import uid
from service.worker import handle
from service.handlers import design
from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.conversation import DraftResponseError
from tests.design.test_builder import PROMPT, draft, fake_conversation, initial


@pytest.fixture(autouse=True)
def modes(monkeypatch):
    for name in ('CAPABILITY', 'CONDITION', 'REQUIREMENT', 'VERIFIER'):
        monkeypatch.setenv('A4B_' + name + '_MODE', 'off')


def call(folder, action, **fields):
    conversation = fields.pop('conversation', None)
    return handle(dict(directory=str(folder), action=action, **fields), conversation=conversation)


def session(folder):
    return call(folder, 'new_session', model='test', credential_id=uid())


def records(folder, sid):
    return call(folder, 'generation_history', id=sid)['requests']


def complete(state):
    return {'draft': draft(), 'confirmed': False}


def test_direct_session_handler_records_its_context_action(tmp_path):
    from types import SimpleNamespace
    s = session(tmp_path)
    store = Storage(tmp_path)
    try:
        result = design.session_action(SimpleNamespace(storage=store, action='message',
            data={'id':s['id'], 'revision':0, 'content':PROMPT}, api_key='', conversation=fake_conversation))
        [record] = records(tmp_path, s['id'])
        assert record['request_id'] == result['generation_request_id']
        assert record['action'] == 'message' and record['status'] == 'waiting_input'
    finally:
        store.close()


def test_each_turn_records_clarification_confirmation_and_atomic_save(tmp_path):
    s = session(tmp_path)
    first = call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT, conversation=fake_conversation)
    [waiting] = records(tmp_path, s['id'])
    assert waiting['request_id'] == first['generation_request_id']
    assert waiting['status'] == 'waiting_input' and waiting['reason_code'] == 'clarification_required'
    assert waiting['error_type'] is None and waiting['request_text'] == PROMPT
    assert not any(event['status'] == 'error' for event in waiting['events'])
    assert waiting['session_revision'] == 0 and waiting['finished_at'] >= waiting['started_at']
    ready = call(tmp_path, 'message', id=s['id'], revision=first['revision'], content='중복은 유지', conversation=fake_conversation)
    saved = call(tmp_path, 'confirm', id=s['id'], revision=ready['revision'], conversation=fake_conversation)
    history = records(tmp_path, s['id'])
    assert [row['status'] for row in history] == ['succeeded', 'waiting_confirmation', 'waiting_input']
    assert all(not any(event['status'] == 'error' for event in row['events']) for row in history)
    assert len({row['request_id'] for row in history}) == 3
    assert history[0]['workflow_id'] == saved['saved_agent_id'] and history[0]['stage'] == 'save'
    assert {'request_analysis', 'design', 'validation'} <= {event['stage'] for event in history[1]['events']}
    assert history[-1] == waiting  # A later turn never overwrites its predecessor.


@pytest.mark.parametrize('error,code', [
    (TimeoutError('provider body must never be recorded'), 'model_timeout'),
    (DraftResponseError('invalid_json', 'private raw response'), 'invalid_model_response'),
])
def test_exception_preserves_request_and_stage_then_retry_has_new_id(tmp_path, error, code):
    s = session(tmp_path)
    def broken(state):
        raise error
    with pytest.raises(type(error)):
        call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT, conversation=broken)
    [failed] = records(tmp_path, s['id'])
    assert (failed['status'], failed['stage'], failed['node'], failed['reason_code']) == ('failed', 'design', 'conversation', code)
    assert failed['request_text'] == PROMPT and failed['error_type'] == type(error).__name__
    assert str(error) not in json.dumps(failed)
    call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT, conversation=complete)
    history = records(tmp_path, s['id'])
    assert [r['status'] for r in history] == ['waiting_confirmation', 'failed']
    assert history[0]['request_id'] != failed['request_id']


@pytest.mark.parametrize('stage', ['capability', 'condition', 'requirement'])
def test_caught_analysis_model_timeouts_are_failures_not_questions(tmp_path, monkeypatch, stage):
    class TimeoutTransport:
        def invoke(self, *args, **kwargs):
            raise TimeoutError('private endpoint response')
    def graph(*args, **kwargs):
        return build_graph(*args, **kwargs, **{stage + '_transport': TimeoutTransport(), stage + '_mode': 'enforce'})
    monkeypatch.setattr(design, 'build_graph', graph)
    s = session(tmp_path)
    result = call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT, conversation=complete)
    assert result['status'] == 'validation_failed'
    [failed] = records(tmp_path, s['id'])
    assert (failed['status'], failed['stage'], failed['reason_code']) == ('failed', 'request_analysis', 'model_timeout')


def test_unsupported_tool_is_a_blocked_design_with_reason(tmp_path):
    s = session(tmp_path)
    call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT,
         conversation=lambda state: {'draft': {**draft(), 'unsupported': ['메일 발송 도구가 없습니다.']}})
    [blocked] = records(tmp_path, s['id'])
    assert (blocked['status'], blocked['stage'], blocked['reason_code']) == ('blocked', 'design', 'unsupported_tools')
    assert '메일' in blocked['reason'] and blocked['error_type'] is None


def test_validation_exhausted_after_repairs_is_blocked(tmp_path):
    s = session(tmp_path)
    result = call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT,
                  conversation=lambda state: {'draft': {**draft(), 'tool_ids': []}, 'repair_count':state.get('repair_count',0)+1})
    assert result['status'] == 'validation_failed'
    [blocked] = records(tmp_path, s['id'])
    assert (blocked['status'], blocked['stage'], blocked['reason_code']) == ('blocked', 'validation', 'verification_failed')
    assert blocked['reason']


@pytest.mark.parametrize('new_turn',[False,True])
def test_pending_failed_repair_keeps_budget_only_for_the_same_input(tmp_path,new_turn):
    s=session(tmp_path);seen=[]
    def interrupted(state):
        count=state.get('repair_count',0)
        if count:raise TimeoutError('interrupted repair')
        return {'draft':{**draft(),'tool_ids':[]},'repair_count':count+1}
    with pytest.raises(TimeoutError):
        call(tmp_path,'message',id=s['id'],revision=0,content=PROMPT,conversation=interrupted)
    def resumed(state):
        seen.append(state.get('repair_count',0))
        return complete(state)
    call(tmp_path,'message',id=s['id'],revision=0,content=PROMPT+' 새 요청입니다.' if new_turn else PROMPT,conversation=resumed)
    assert seen==[0 if new_turn else 1]


@pytest.mark.parametrize('mode,expected', [('shadow', 'waiting_confirmation'), ('enforce', 'failed')])
def test_verifier_errors_only_block_when_enforced(tmp_path, monkeypatch, mode, expected):
    from verification.workflow.contracts import VerificationResult
    def verifier(payload):
        return VerificationResult(request_id=payload.request_id, model_output=None, findings=[], route='manual_review',
                                  mode='live', model='test', latency_ms=1, error_code='context_budget_exceeded')
    monkeypatch.setattr(design, 'build_graph', lambda *a, **k: build_graph(*a, **k, verifier=verifier, verifier_mode=mode))
    s = session(tmp_path)
    call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT, conversation=complete)
    [record] = records(tmp_path, s['id'])
    assert record['status'] == expected
    assert any(e.get('reason_code') == 'context_budget_exceeded' for e in record['events'])
    if mode == 'enforce':
        assert record['stage'] == 'validation' and record['reason_code'] == 'context_budget_exceeded'


def test_save_sql_failure_survives_the_rollback(tmp_path, monkeypatch):
    s = session(tmp_path)
    ready = call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT, conversation=complete)
    from infrastructure.persistence.db import SqliteDatabase
    execute = SqliteDatabase.execute
    def broken(self, sql, params=()):
        if sql.startswith('INSERT INTO agent_versions'):
            raise sqlite3.OperationalError('private database details')
        return execute(self, sql, params)
    monkeypatch.setattr(SqliteDatabase, 'execute', broken)
    with pytest.raises(sqlite3.OperationalError):
        call(tmp_path, 'confirm', id=s['id'], revision=ready['revision'], conversation=complete)
    [failed, ready_record] = records(tmp_path, s['id'])
    assert (failed['status'], failed['stage'], failed['reason_code']) == ('failed', 'save', 'save_failed')
    store = Storage(tmp_path)
    assert not store.agents()
    store.close()


@pytest.mark.parametrize('route,code,reason', [
    ('unsupported','unsupported_tools','Slack 전송 도구가 없습니다.'),
    ('manual_review','verification_failed','원문 근거와 생성한 설정이 일치하지 않습니다.'),
])
def test_verifier_rejection_preserves_the_actual_reason(tmp_path,monkeypatch,route,code,reason):
    from verification.workflow.contracts import VerificationResult, VerificationOutput
    def verifier(payload):
        output=VerificationOutput(request_status='unsupported' if route=='unsupported' else 'ready',
                                  evidence_status='sufficient',missing_conditions=[],
                                  unsupported_actions=[reason] if route=='unsupported' else [],
                                  draft_verdict='fail',reflection_verdict='not_applicable',evidence=[],issues=[],summary=reason)
        return VerificationResult(request_id=payload.request_id,model_output=output,findings=[],route=route,
                                  mode='live',model='test',latency_ms=1)
    monkeypatch.setattr(design,'build_graph',lambda *a,**k:build_graph(*a,**k,verifier=verifier,verifier_mode='enforce'))
    s=session(tmp_path)
    call(tmp_path,'message',id=s['id'],revision=0,content=PROMPT,conversation=complete)
    [record]=records(tmp_path,s['id'])
    assert (record['status'],record['stage'],record['reason_code'])==('blocked','validation',code)
    assert reason in record['reason']


def test_timeout_cannot_be_overwritten_by_late_worker_or_late_save(tmp_path):
    s = session(tmp_path)
    store = Storage(tmp_path)
    audit = GenerationHistory.start(store, dict(id=s['id'], action='message', revision=0, content=PROMPT))
    audit.enter('conversation')
    call(tmp_path, 'interrupt_generation', id=s['id'], generation_request_id=audit.id, reason_code='worker_timeout')
    audit.finish({'status': 'saved', 'saved_agent_id': uid()})
    [failed] = records(tmp_path, s['id'])
    assert (failed['status'], failed['stage'], failed['reason_code']) == ('failed', 'design', 'worker_timeout')
    store.generation_audit = audit
    with pytest.raises(ValueError, match='이미 종료'):
        store.save_agent({**initial(), 'session_id':s['id'], 'draft':draft(), 'confirmed':True})
    assert not store.agents()
    store.close()


def test_request_ownership_and_credentials_are_protected(tmp_path, monkeypatch):
    monkeypatch.setenv('A4B_MAIN_API_KEY', 'secret-fixture-value')
    alice, bob = tmp_path/'alice', tmp_path/'bob'
    s = session(alice)
    text = PROMPT + ' secret-fixture-value Bearer private-token sk-abcdefgh1234'
    call(alice, 'message', id=s['id'], revision=0, content=text, conversation=complete)
    serialized = json.dumps(records(alice, s['id']), ensure_ascii=False)
    for secret in ('secret-fixture-value', 'private-token', 'sk-abcdefgh1234'):
        assert secret not in serialized
    with pytest.raises(ValueError):
        records(bob, s['id'])


def test_wrapped_provider_timeout_retains_its_category():
    try:
        try:
            raise TimeoutError('private endpoint')
        except TimeoutError as error:
            raise ValueError('local model failed') from error
    except ValueError as error:
        assert error_info(error, 'request_analysis')['reason_code'] == 'model_timeout'


def test_request_identity_survives_redaction_and_is_persisted_with_result(tmp_path):
    s = session(tmp_path)
    request_id = uid()
    prompt = PROMPT + ' Bearer example-test-secret'
    result = call(tmp_path, 'message', id=s['id'], revision=0, content=prompt,
                  generation_request_id=request_id, conversation=fake_conversation)
    [record] = records(tmp_path, s['id'])
    assert record['request_id'] == request_id
    assert record['request_text'] != prompt
    assert record['result_revision'] == result['revision']
    restored = call(tmp_path, 'session', id=s['id'])
    assert restored['last_generation_request_id'] == request_id
    assert restored['revision'] == record['result_revision']


def test_request_id_cannot_be_reused_for_a_different_session(tmp_path):
    first, second = session(tmp_path), session(tmp_path)
    request_id = uid()
    call(tmp_path, 'message', id=first['id'], revision=0, content=PROMPT,
         generation_request_id=request_id, conversation=fake_conversation)
    with pytest.raises(ValueError):
        call(tmp_path, 'message', id=second['id'], revision=0, content=PROMPT,
             generation_request_id=request_id, conversation=fake_conversation)
    assert call(tmp_path, 'session', id=second['id'])['revision'] == 0


def test_two_request_results_keep_their_own_revision_without_mutating_input(tmp_path):
    s = session(tmp_path)
    first_id, second_id = uid(), uid()
    first = call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT,
                 generation_request_id=first_id, conversation=fake_conversation)
    second = call(tmp_path, 'message', id=s['id'], revision=first['revision'], content='중복은 유지',
                  generation_request_id=second_id, conversation=fake_conversation)
    indexed = {item['request_id']: item for item in records(tmp_path, s['id'])}
    assert indexed[first_id]['result_revision'] == first['revision']
    assert indexed[second_id]['result_revision'] == second['revision']
    assert call(tmp_path, 'session', id=s['id'])['last_generation_request_id'] == second_id


def test_abandoned_generation_can_resume_in_the_same_session(tmp_path):
    s = session(tmp_path)
    request_id = uid()
    call(tmp_path, 'begin_generation', id=s['id'], revision=0, content=PROMPT,
         request_action='message', generation_request_id=request_id)
    result = call(tmp_path, 'recover_generation', id=s['id'], revision=0, generation_request_id=request_id)
    assert result['recovered'] is True
    [record] = records(tmp_path, s['id'])
    assert record['status'] == 'failed' and record['reason_code'] == 'generation_abandoned'
    resumed = call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT, conversation=fake_conversation)
    assert resumed['id'] == s['id'] and resumed['revision'] == 1
    assert resumed['status'] == 'needs_clarification'
    assert len([m for m in resumed['messages'] if m['role']=='user']) == 1


def test_recovery_refuses_a_live_worker_and_preserves_its_audit(tmp_path):
    from infrastructure.persistence.generation_lock import generation_session_lock
    s = session(tmp_path)
    request_id = uid()
    call(tmp_path, 'begin_generation', id=s['id'], revision=0, content=PROMPT,
         request_action='message', generation_request_id=request_id)
    store = Storage(tmp_path)
    try:
        with generation_session_lock(store, s['id']) as acquired:
            assert acquired
            result = call(tmp_path, 'recover_generation', id=s['id'], revision=0, generation_request_id=request_id)
            assert result == {'recovered':False, 'reason':'active'}
            assert records(tmp_path, s['id'])[0]['status'] == 'running'
    finally:
        store.close()


def test_recovery_checks_session_ownership_and_revision(tmp_path):
    s, other = session(tmp_path), session(tmp_path)
    request_id = uid()
    call(tmp_path, 'begin_generation', id=s['id'], revision=0, content=PROMPT,
         request_action='message', generation_request_id=request_id)
    with pytest.raises(ValueError, match='이 대화'):
        call(tmp_path, 'recover_generation', id=other['id'], revision=0, generation_request_id=request_id)
    assert call(tmp_path, 'recover_generation', id=s['id'], revision=1,
                generation_request_id=request_id) == {'recovered':False, 'reason':'changed'}
    assert records(tmp_path, s['id'])[0]['status'] == 'running'


def test_recovery_does_not_relaunch_a_cancelled_queued_request(tmp_path):
    s = session(tmp_path)
    request_id = uid()
    call(tmp_path, 'begin_generation', id=s['id'], revision=0, content=PROMPT,
         request_action='message', generation_request_id=request_id)
    call(tmp_path, 'recover_generation', id=s['id'], revision=0, generation_request_id=request_id)
    invoked = []
    with pytest.raises(ValueError, match='이미 처리'):
        call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT,
             generation_request_id=request_id, conversation=lambda state:invoked.append(True))
    assert not invoked
    assert call(tmp_path, 'session', id=s['id'])['revision'] == 0


def test_generation_holds_ownership_while_the_model_is_running(tmp_path):
    s = session(tmp_path)
    request_id = uid()
    def conversation(state):
        assert call(tmp_path, 'recover_generation', id=s['id'], revision=0,
                    generation_request_id=request_id) == {'recovered':False, 'reason':'active'}
        return fake_conversation(state)
    result = call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT,
                  generation_request_id=request_id, conversation=conversation)
    assert result['status'] == 'needs_clarification'


def test_killed_worker_releases_session_ownership_for_recovery(tmp_path):
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    s = session(tmp_path)
    request_id = uid()
    call(tmp_path, 'begin_generation', id=s['id'], revision=0, content=PROMPT,
         request_action='message', generation_request_id=request_id)
    ready = tmp_path/'owner-ready'
    script = '''
import sys,time
from pathlib import Path
from infrastructure.persistence.storage import Storage
from infrastructure.persistence.generation_lock import generation_session_lock
store=Storage(sys.argv[1])
with generation_session_lock(store,sys.argv[2]) as acquired:
    assert acquired
    Path(sys.argv[3]).touch()
    time.sleep(30)
'''
    process = subprocess.Popen([sys.executable, '-c', script, str(tmp_path), s['id'], str(ready)],
        env={**os.environ, 'PYTHONPATH':str(Path(__file__).resolve().parents[2])},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+10
        while not ready.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.01)
        assert ready.exists()
        assert call(tmp_path, 'recover_generation', id=s['id'], revision=0,
                    generation_request_id=request_id)['reason'] == 'active'
        process.kill();process.communicate(timeout=5)
        assert call(tmp_path, 'recover_generation', id=s['id'], revision=0,
                    generation_request_id=request_id)['recovered'] is True
    finally:
        if process.poll() is None:process.kill()
        process.communicate(timeout=5)


def test_published_reply_remains_completed_if_worker_fails_before_final_response(tmp_path, monkeypatch):
    s = session(tmp_path)
    request_id = uid()
    def lost_response(*args, **kwargs):raise OSError('worker exited after publication')
    monkeypatch.setattr(GenerationHistory, 'finish', lost_response)
    with pytest.raises(OSError):
        call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT,
             generation_request_id=request_id, conversation=fake_conversation)
    restored=call(tmp_path, 'session', id=s['id'])
    [record]=records(tmp_path, s['id'])
    assert restored['revision'] == record['result_revision'] == 1
    assert restored['last_generation_request_id'] == request_id
    assert record['status'] == 'waiting_input'


def test_recovery_preserves_a_completed_outcome(tmp_path):
    s = session(tmp_path)
    request_id = uid()
    result=call(tmp_path, 'message', id=s['id'], revision=0, content=PROMPT,
                generation_request_id=request_id, conversation=fake_conversation)
    assert call(tmp_path, 'recover_generation', id=s['id'], revision=result['revision'],
                generation_request_id=request_id)['recovered'] is True
    [record]=records(tmp_path, s['id'])
    assert record['status']=='waiting_input' and record['result_revision']==result['revision']


def _committed_save_without_checkpoint(tmp_path, monkeypatch):
    s=session(tmp_path)
    first=call(tmp_path,'message',id=s['id'],revision=0,content=PROMPT,conversation=fake_conversation)
    ready=call(tmp_path,'message',id=s['id'],revision=first['revision'],content='중복은 유지',conversation=fake_conversation)
    original=Storage.save_agent
    def save_then_exit(store,state):
        original(store,state)
        raise OSError('worker exited before checkpoint publication')
    monkeypatch.setattr(Storage,'save_agent',save_then_exit)
    request_id=uid()
    with pytest.raises(OSError):
        call(tmp_path,'confirm',id=s['id'],revision=ready['revision'],generation_request_id=request_id,conversation=fake_conversation)
    record=next(item for item in records(tmp_path,s['id']) if item['request_id']==request_id)
    assert record['status']=='succeeded' and record['workflow_id']
    return s,ready,request_id,record


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('renamed',[False,True])
def test_saved_workflow_is_restored_when_worker_exits_before_save_checkpoint(tmp_path, monkeypatch, legacy, renamed):
    s,ready,request_id,record=_committed_save_without_checkpoint(tmp_path,monkeypatch)
    if renamed:
        store=Storage(tmp_path)
        try:store.rename_agent(record['workflow_id'],'저장 후 이름 변경',1)
        finally:store.close()
    if legacy:
        store=Storage(tmp_path)
        try:
            audit=GenerationHistory(store,request_id);old=audit.read();old.pop('workflow_version',None)
            audit._write(old);store.db.commit()
        finally:store.close()
    assert call(tmp_path,'session',id=s['id'])['status']=='ready'
    result=call(tmp_path,'recover_generation',id=s['id'],revision=ready['revision'],generation_request_id=request_id)
    assert result['recovered'] is True
    restored=call(tmp_path,'session',id=s['id'])
    assert restored['status']=='saved'
    assert restored['saved_agent_id']==record['workflow_id']
    assert restored['last_generation_request_id']==request_id
    assert restored['revision']==ready['revision']+1
    again=call(tmp_path,'recover_generation',id=s['id'],revision=restored['revision'],generation_request_id=request_id)
    assert again['recovered'] is True
    assert call(tmp_path,'session',id=s['id'])['revision']==restored['revision']
    store=Storage(tmp_path)
    try:
        assert store.agent(record['workflow_id'])['version']==(2 if renamed else 1)
        assert restored['base_version']==1
        assert len(store.agents())==1
    finally:store.close()


def test_saved_recovery_does_not_overwrite_a_newer_conversation(tmp_path, monkeypatch):
    s,ready,request_id,record=_committed_save_without_checkpoint(tmp_path,monkeypatch)
    store=Storage(tmp_path)
    try:
        current=store.session(s['id'])
        updated=store.update_session({**current,'last_generation_request_id':uid()},current['revision'])
    finally:store.close()
    result=call(tmp_path,'recover_generation',id=s['id'],revision=updated['revision'],generation_request_id=request_id)
    assert result=={'recovered':False,'reason':'changed'}
    latest=call(tmp_path,'session',id=s['id'])
    assert latest['status']=='ready' and latest['revision']==updated['revision']
    assert latest['last_generation_request_id']!=request_id
    assert next(item for item in records(tmp_path,s['id']) if item['request_id']==request_id)['status']=='succeeded'


def test_save_recovery_revision_conflict_leaves_checkpoint_unchanged(tmp_path, monkeypatch):
    s,ready,request_id,record=_committed_save_without_checkpoint(tmp_path,monkeypatch)
    before=call(tmp_path,'session',id=s['id'])
    def conflict(*args,**kwargs):raise ValueError('다른 요청에서 대화가 변경됐습니다.')
    monkeypatch.setattr(Storage,'update_session',conflict)
    with pytest.raises(ValueError,match='다른 요청'):
        call(tmp_path,'recover_generation',id=s['id'],revision=ready['revision'],generation_request_id=request_id)
    after=call(tmp_path,'session',id=s['id'])
    assert after==before


def _recovery_table_asset(folder):
    store=Storage(folder)
    try:
        asset_id=uid()
        store.add_asset({'id':asset_id,'name':'race.csv','kind':'table','format':'csv',
                         'columns':['n'],'row_count':1,'text':'n\n2','profile':{},'preview':[{'n':2}]},[{'n':2}])
        return asset_id
    finally:store.close()


@pytest.mark.parametrize('attach_after_failure',[False,True])
def test_checkpoint_failure_can_retry_without_overwriting_a_later_attachment(tmp_path,monkeypatch,attach_after_failure):
    s,ready,request_id,record=_committed_save_without_checkpoint(tmp_path,monkeypatch)
    asset_id=_recovery_table_asset(tmp_path)
    import agent_builder.design.graph as graph_module
    builder=graph_module.build_graph
    failed=False
    def interrupted_builder(*args,**kwargs):
        graph,connection=builder(*args,**kwargs)
        class Proxy:
            def __getattr__(self,name):return getattr(graph,name)
            def update_state(self,*args,**kwargs):
                nonlocal failed
                if not failed:
                    failed=True
                    raise OSError('checkpoint publication interrupted')
                return graph.update_state(*args,**kwargs)
        return Proxy(),connection
    monkeypatch.setattr(graph_module,'build_graph',interrupted_builder)
    with pytest.raises(OSError,match='checkpoint publication'):
        call(tmp_path,'recover_generation',id=s['id'],revision=ready['revision'],generation_request_id=request_id)
    reserved=call(tmp_path,'session',id=s['id'])
    assert reserved['revision']==ready['revision']+1 and reserved['status']=='ready'
    if attach_after_failure:
        attached=call(tmp_path,'attach_asset',id=s['id'],revision=reserved['revision'],asset_id=asset_id)
        assert attached['status']=='needs_clarification'
        result=call(tmp_path,'recover_generation',id=s['id'],revision=attached['revision'],generation_request_id=request_id)
        assert result=={'recovered':False,'reason':'changed'}
        assert call(tmp_path,'session',id=s['id'])==attached
    else:
        result=call(tmp_path,'recover_generation',id=s['id'],revision=reserved['revision'],generation_request_id=request_id)
        assert result['recovered'] is True
        restored=call(tmp_path,'session',id=s['id'])
        assert restored['status']=='saved' and restored['revision']==reserved['revision']
        assert restored['saved_agent_id']==record['workflow_id']


@pytest.mark.parametrize('first_action',['recover_generation','attach_asset'])
def test_separate_workers_serialize_attachment_and_saved_recovery(tmp_path,monkeypatch,first_action):
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    s,ready,request_id,record=_committed_save_without_checkpoint(tmp_path,monkeypatch)
    asset_id=_recovery_table_asset(tmp_path)
    entered,released=tmp_path/'mutation-entered',tmp_path/'mutation-release'
    payload={'directory':str(tmp_path),'action':first_action,'id':s['id'],'revision':ready['revision'],
             'generation_request_id':request_id,'asset_id':asset_id}
    script='''
import sys,json,time
from pathlib import Path
from service.worker import handle
from infrastructure.persistence.storage import Storage
payload=json.loads(sys.argv[1])
original=Storage.update_session
blocked=False
def pause(store,*args,**kwargs):
    global blocked
    if blocked:return original(store,*args,**kwargs)
    blocked=True
    if payload['action']=='attach_asset':result=original(store,*args,**kwargs)
    Path(sys.argv[2]).touch()
    deadline=time.monotonic()+15
    while not Path(sys.argv[3]).exists():
        if time.monotonic()>deadline:raise TimeoutError('test barrier timed out')
        time.sleep(.01)
    return result if payload['action']=='attach_asset' else original(store,*args,**kwargs)
Storage.update_session=pause
print(json.dumps(handle(payload)))
'''
    process=subprocess.Popen([sys.executable,'-c',script,json.dumps(payload),str(entered),str(released)],
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2])},
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+10
        while not entered.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.01)
        assert entered.exists()
        if first_action=='recover_generation':
            with pytest.raises(ValueError,match='이전 요청'):
                call(tmp_path,'attach_asset',id=s['id'],revision=ready['revision'],asset_id=asset_id)
        else:
            assert call(tmp_path,'recover_generation',id=s['id'],revision=ready['revision'],
                        generation_request_id=request_id)=={'recovered':False,'reason':'active'}
        released.touch()
        stdout,stderr=process.communicate(timeout=10)
        assert process.returncode==0,stderr
        result=json.loads(stdout)
        latest=call(tmp_path,'session',id=s['id'])
        if first_action=='recover_generation':
            assert result['recovered'] is True and latest['status']=='saved'
            assert not latest['file_ids']
        else:
            assert latest['status']=='needs_clarification' and latest['file_ids']==[asset_id]
            assert call(tmp_path,'recover_generation',id=s['id'],revision=latest['revision'],
                        generation_request_id=request_id)=={'recovered':False,'reason':'changed'}
            assert call(tmp_path,'session',id=s['id'])==latest
    finally:
        released.touch()
        if process.poll() is None:process.kill()
        process.communicate(timeout=5)
