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
