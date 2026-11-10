"""Resume persisted legacy checkpoints with today's reviewer disabled policy."""
from copy import deepcopy
import json

import pytest
from langgraph.types import Command

from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.save import make_save_node
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.nodes.verification import make_verification_node
from infrastructure.persistence.storage import Storage
from tests.deployment.test_qwen9b_defaults import checked_seed
from tests.verification.test_verifier_bridge import EXAMPLES
from verification.workflow.service import verify
from workflow_engine.runner import run_workflow


def failed_review(store, state):
    def forged(payload):
        output = json.loads((EXAMPLES / 'model_outputs/01_ready.json').read_text())
        output['evidence'][0]['quote'] = '사용자가 말하지 않은 인용'
        return verify(payload, mode='fixture', fixture=output)
    result = make_verification_node(store, verifier=forged, mode='enforce')(state)
    assert result['verification_route'] == 'manual_review' and result['issues']
    return result


@pytest.mark.parametrize('repair_count', [0, 2])
@pytest.mark.parametrize('already_disabled', [False, True])
@pytest.mark.parametrize('previous_status', ['ready', 'validation_failed'])
@pytest.mark.parametrize('resume_at', ['verification', 'supervisor', 'save'])
def test_real_failed_review_resumes_off_without_replanning(tmp_path, monkeypatch, repair_count,
                                                         already_disabled, previous_status, resume_at):
    monkeypatch.setenv('A4B_VERIFIER_MODE', 'off')
    store = Storage(tmp_path)
    state, raw = checked_seed(store)
    state.update(draft=raw, repair_count=repair_count)
    state.update(validate_node(state))
    state.update(failed_review(store, state))
    state.update(status=previous_status, route='await_approval', confirmed=resume_at == 'save')
    if already_disabled:
        state.update(verification_required=False, verification_enforced=False, verification_route=None)
    history = deepcopy(state['verification'])
    messages = deepcopy(state['messages'])
    records = store.db.execute('SELECT * FROM builder_verifications').fetchall()
    config = {'configurable': {'thread_id': state['session_id']}}
    graph, connection = build_graph(store, verifier_mode='enforce')
    predecessor = {'verification': 'validate', 'supervisor': 'verification',
                   'await_approval': 'supervisor', 'save': 'await_approval'}[resume_at]
    graph.update_state(config, state, as_node=predecessor)
    connection.close(); store.close()
    store = Storage(tmp_path)
    forbidden = lambda *_: pytest.fail('off must not replan or call the reviewer for stale errors')
    graph, connection = build_graph(store, conversation=forbidden, verifier=forbidden)
    try:
        result = graph.invoke(None, config)
        if resume_at != 'save':
            assert result['status'] == 'ready' and not result['issues']
            assert result['__interrupt__'][0].value['kind'] == 'review'
            assert not store.agents()
            result = graph.invoke(Command(resume={'type': 'confirm'}), config)
        assert result['status'] == 'saved' and not result['issues']
        assert result['verification'] == history and result['messages'] == messages
        assert store.db.execute('SELECT * FROM builder_verifications').fetchall() == records
        run = run_workflow(store, result['saved_agent_id'], '저장한 조건으로 실행', api_key='')
        assert run['status'] == 'succeeded' and [r['번호'] for r in run['rows']] == ['B', 'C']
    finally:
        connection.close(); store.close()


@pytest.mark.parametrize('fault', ['binding', 'semantic', 'requirement'])
def test_off_revalidation_keeps_native_errors(tmp_path, monkeypatch, fault):
    monkeypatch.setenv('A4B_VERIFIER_MODE', 'off')
    store = Storage(tmp_path)
    state, raw = checked_seed(store)
    state.update(draft=raw, repair_count=2)
    state.update(validate_node(state))
    state.update(failed_review(store, state))
    if fault == 'binding':
        state['draft']['workflow']['nodes'][1]['bindings'][0]['node_id'] = 'missing-source'
    elif fault == 'semantic':
        state['semantic_issues'] = ['기본 조건 검증 실패']
    else:
        state['requirement_ledger'] = None
    expected = validate_node(state)
    assert expected['issues'] and expected['status'] != 'ready'
    graph, connection = build_graph(store, conversation=lambda _: pytest.fail('No replanning'))
    config = {'configurable': {'thread_id': state['session_id']}}
    try:
        graph.update_state(config, state, as_node='verification')
        result = graph.invoke(None, config)
        assert result['status'] == 'validation_failed' and result['issues'] == expected['issues']
        assert not store.agents()
    finally:
        connection.close(); store.close()


@pytest.mark.parametrize('resume_at', ['verification', 'supervisor', 'await_approval', 'save'])
def test_off_resumes_old_checkpoints_without_review_or_data_loss(tmp_path, monkeypatch, resume_at):
    monkeypatch.setenv('A4B_VERIFIER_MODE', 'off')
    store = Storage(tmp_path)
    state, raw = checked_seed(store)
    state.update(draft=raw)
    state.update(validate_node(state))
    assert state['status'] == 'ready'
    # A previously saved version must survive resuming a different design session.
    existing = make_save_node(store)({**state, 'confirmed': True})['saved_agent_id']
    saved_before = deepcopy(store.agent(existing))
    historical = dict(route='manual_review', findings=[dict(detail='과거 보조 검토')])
    state.update(verification_required=True, verification_enforced=True,
                 verification_route='manual_review', verification=historical,
                 route='await_approval', confirmed=resume_at == 'save')
    messages_before = deepcopy(state['messages'])
    graph, connection = build_graph(store, verifier_mode='enforce')
    config = {'configurable': {'thread_id': state['session_id']}}
    predecessor = {'verification': 'validate', 'supervisor': 'verification',
                   'await_approval': 'supervisor', 'save': 'await_approval'}[resume_at]
    graph.update_state(config, state, as_node=predecessor)
    if resume_at == 'await_approval':
        interrupted = graph.invoke(None, config)
        assert interrupted['__interrupt__'][0].value['kind'] == 'review'
    connection.close()
    store.close()

    store = Storage(tmp_path)
    forbidden = lambda *_: pytest.fail('Resuming this checkpoint must not call any model')
    graph, connection = build_graph(store, conversation=forbidden, verifier=forbidden)
    try:
        assert graph.get_state(config).values['verification_required']
        result = graph.invoke(None, config)
        if resume_at != 'save':
            assert result['__interrupt__'][0].value['kind'] == 'review'
            assert len(store.agents()) == 1, 'No save before explicit confirmation'
            result = graph.invoke(Command(resume={'type': 'confirm'}), config)
        assert result['status'] == 'saved'
        assert result['verification'] == historical
        assert result['messages'] == messages_before
        assert not result['verification_required'] and not result['verification_enforced']
        assert result['verification_route'] is None
        assert store.agent(existing) == saved_before
        assert not store.db.table_exists('builder_verifications')
        run = run_workflow(store, result['saved_agent_id'], '저장한 조건으로 실행', api_key='')
        assert run['status'] == 'succeeded'
        assert [row['번호'] for row in run['rows']] == ['B', 'C']
    finally:
        connection.close()
        store.close()


def test_off_does_not_bypass_validation_on_direct_save_resume(tmp_path, monkeypatch):
    monkeypatch.setenv('A4B_VERIFIER_MODE', 'off')
    store = Storage(tmp_path)
    state, raw = checked_seed(store)
    raw['workflow']['nodes'][1]['bindings'][0]['node_id'] = 'missing-source'
    state.update(draft=raw, status='ready', confirmed=True,
                 verification_required=True, verification_route='manual_review')
    graph, connection = build_graph(store)
    config = {'configurable': {'thread_id': state['session_id']}}
    try:
        graph.update_state(config, state, as_node='await_approval')
        with pytest.raises(ValueError):
            graph.invoke(None, config)
        assert not store.agents()
    finally:
        connection.close()
        store.close()
