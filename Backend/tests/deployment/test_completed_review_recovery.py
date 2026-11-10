"""Completed verifier failures must recover through the public service boundary."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.save import make_save_node
from agent_builder.design.nodes.validation import validate_node
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from service.dispatch import handle
from service.handlers import design
from tests.deployment.test_qwen9b_defaults import checked_seed
from tests.verification.test_verifier_bridge import EXAMPLES
from verification.workflow.service import verify
from workflow_engine.runner import run_workflow


@pytest.fixture
def completed(tmp_path, monkeypatch):
    for mode in ('CAPABILITY', 'CONDITION', 'REQUIREMENT', 'VERIFIER'):
        monkeypatch.setenv('A4B_' + mode + '_MODE', 'off')
    store = Storage(tmp_path)
    state, raw = checked_seed(store)
    state.update(draft=raw)
    state.update(validate_node(state))
    assert state['status'] == 'ready' and not state['issues']
    existing = make_save_node(store)({**state, 'confirmed': True})['saved_agent_id']
    saved_before = deepcopy(store.agent(existing))
    session = store.new_session('test', uid())
    session['file_ids'] = [r['id'] for r in state['resources']]
    session = store.update_session(session, session['revision'])
    state.update(session_id=session['id'], model=session['model'], credential_id=session['credential_id'])

    def forged(payload):
        output = json.loads((EXAMPLES / 'model_outputs/01_ready.json').read_text())
        output['evidence'][0]['quote'] = '사용자가 말하지 않은 인용'
        return verify(payload, mode='fixture', fixture=output)

    def forbidden(*args, **kwargs):
        pytest.fail('Recovery must not call a planner or verifier')

    graph, connection = build_graph(store, conversation=forbidden, verifier=forged, verifier_mode='enforce')
    config = {'configurable': {'thread_id': session['id']}}
    graph.update_state(config, state, as_node='validate')
    failed = graph.invoke(None, config)
    assert failed['verification_route'] == 'manual_review'
    assert failed['status'] == 'validation_failed'
    assert graph.get_state(config).next == () and not graph.get_state(config).tasks
    session = store.update_session(session, session['revision'], failed)
    records = store.db.execute('SELECT * FROM builder_verifications').fetchall()
    # Use the real dispatcher/handlers/graph, replacing only inference.
    monkeypatch.setattr(design, 'build_graph', lambda *a, **kw:
                        build_graph(*a, **{**kw, 'conversation': forbidden}, verifier=forbidden))

    def call(action, **kw):
        return handle(dict(directory=str(tmp_path), action=action, id=session['id'],
                           revision=session['revision'], **kw), conversation=forbidden)

    yield store, graph, config, session, failed, call
    assert store.agent(existing) == saved_before
    assert store.db.execute('SELECT * FROM builder_verifications').fetchall() == records
    connection.close()
    store.close()


@pytest.mark.parametrize('repair_count', [0, 2])
@pytest.mark.parametrize('already_disabled', [False, True])
@pytest.mark.parametrize('read_first', [False, True])
def test_completed_failure_can_be_confirmed_without_replanning(completed, repair_count,
                                                              already_disabled, read_first):
    store, graph, config, session, failed, call = completed
    update = dict(repair_count=repair_count)
    if already_disabled:
        update.update(verification_required=False, verification_enforced=False, verification_route=None)
    graph.update_state(config, update, as_node='supervisor')
    before = graph.get_state(config)
    assert not before.next and not before.tasks
    if read_first:
        for _ in range(2):
            public = call('session')
            assert public['status'] == 'ready' and not public['issues']
            assert public['messages'] == failed['messages'] and public['verification'] == failed['verification']
            assert public['revision'] == session['revision']
        after_read = graph.get_state(config)
        assert after_read.config == before.config and after_read.values == before.values
        assert len(store.agents()) == 1, 'Reading must not save or edit any workflow'
    saved = call('confirm')
    assert saved['status'] == 'saved' and not saved['issues']
    assert saved['messages'] == failed['messages'] and saved['verification'] == failed['verification']
    assert saved['revision'] == session['revision'] + 1
    assert len(store.agents()) == 2
    run = run_workflow(store, saved['saved_agent_id'], '저장한 조건으로 실행', api_key='')
    assert run['status'] == 'succeeded' and [r['번호'] for r in run['rows']] == ['B', 'C']
    assert (store.agent(saved['saved_agent_id'])['config']['workflow']['edges']
            == failed['draft']['workflow']['edges'])


@pytest.mark.parametrize('fault', ['binding', 'semantic', 'requirement', 'capability', 'question'])
def test_completed_recovery_keeps_real_validation_and_approval_guards(completed, fault):
    store, graph, config, session, failed, call = completed
    state = deepcopy(failed)
    if fault == 'binding':
        state['draft']['workflow']['nodes'][1]['bindings'][0]['node_id'] = 'missing-source'
    elif fault == 'semantic':
        state['semantic_issues'] = ['기본 조건 검증 실패']
    elif fault == 'requirement':
        state['requirement_ledger'] = None
    elif fault == 'capability':
        state.update(capability_enforced=True, capability_assessment={'decision': 'manual_review'})
    else:
        state['draft']['questions'] = ['추가 조건을 확인해주세요.']
    graph.update_state(config, state, as_node='supervisor')
    before = graph.get_state(config)
    assert call('session')['status'] != 'ready'
    with pytest.raises(ValueError):
        call('confirm')
    after = graph.get_state(config)
    assert after.config == before.config and after.values == before.values
    assert len(store.agents()) == 1


@pytest.mark.parametrize('mode', ['shadow', 'defer', 'enforce'])
def test_completed_recovery_requires_off(completed, monkeypatch, mode):
    store, graph, config, session, failed, call = completed
    monkeypatch.setenv('A4B_VERIFIER_MODE', mode)
    before = graph.get_state(config)
    assert call('session')['status'] == 'validation_failed'
    with pytest.raises(ValueError):
        call('confirm')
    assert graph.get_state(config).config == before.config
    assert len(store.agents()) == 1


def test_completed_recovery_rejects_stale_revision_and_changed_resources(completed):
    store, graph, config, session, failed, call = completed
    before = graph.get_state(config)
    with pytest.raises(ValueError, match='대화가 변경'):
        handle(dict(directory=str(Path(store.path).parent), action='confirm',
                    id=session['id'], revision=session['revision'] - 1))
    assert graph.get_state(config).config == before.config
    changed = deepcopy(failed['resources'])
    changed[0]['version'] = 0
    graph.update_state(config, {'resources': changed}, as_node='supervisor')
    assert call('session')['status'] == 'validation_failed'
    with pytest.raises(ValueError, match='자료 검토 결과가 변경'):
        call('confirm')
    assert len(store.agents()) == 1


def test_saved_conversation_is_not_revalidated_or_saved_again(completed):
    store, graph, config, session, failed, call = completed
    saved = call('confirm')
    before = graph.get_state(config)
    versions = store.db.execute('SELECT * FROM agent_versions').fetchall()
    public = call('session')
    assert public['status'] == 'saved' and public['revision'] == saved['revision']
    repeated = handle(dict(directory=str(Path(store.path).parent), action='confirm',
                           id=session['id'], revision=saved['revision']))
    assert repeated['saved_agent_id'] == saved['saved_agent_id']
    assert repeated['revision'] == saved['revision']
    assert graph.get_state(config).config == before.config
    assert store.db.execute('SELECT * FROM agent_versions').fetchall() == versions


def test_unrelated_terminal_failure_is_not_reopened(completed):
    store, graph, config, session, failed, call = completed
    graph.update_state(config, dict(verification=None, verification_route=None,
        verification_required=False, verification_enforced=False, issues=['다른 실행 오류']), as_node='supervisor')
    assert call('session')['status'] == 'validation_failed'
    with pytest.raises(ValueError):
        call('confirm')
    assert len(store.agents()) == 1
