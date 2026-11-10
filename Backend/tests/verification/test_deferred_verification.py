"""defer mode stores the shadow input without a model call; the batch run fills in the same verdict."""
import json
import pytest
from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.verification import make_verification_node
from infrastructure.persistence.storage import Storage
from verification.deferred import run_pending
from tests.verification.test_verifier_bridge import seed, fixture_verifier


def rows(store):
    return [(json.loads(r[0]), r[1]) for r in store.db.execute('SELECT result_json, input_json FROM builder_verifications')]


def test_defer_records_input_without_calling_the_model(tmp_path):
    store=Storage(tmp_path);state,raw=seed(store);state['draft']=raw
    result=make_verification_node(store,verifier=lambda d:pytest.fail('defer must not call the verifier'),mode='defer')(state)
    assert result['verification_route']=='pending'
    assert not result['verification_enforced'] and not result['verification_required'] and 'issues' not in result
    assert result['trace'][-1]['status']=='completed'
    [(stored,payload)]=rows(store)
    assert stored['route']=='pending' and stored['execution_allowed'] is False
    assert json.loads(payload)['candidate_draft'] is not None
    store.close()


def test_batch_run_matches_shadow_verdict_and_skips_finished_records(tmp_path,monkeypatch):
    monkeypatch.setenv('A4B_VERIFIER_MODE','defer')
    store=Storage(tmp_path);state,raw=seed(store);state['draft']=raw
    shadow=make_verification_node(store,verifier=fixture_verifier,mode='shadow')(state)
    make_verification_node(store,mode='defer')(state)
    store.close()
    done=run_pending(tmp_path,verifier=fixture_verifier)
    assert shadow['verification_route']=='continue_review'
    assert [d['route'] for d in done]==[shadow['verification_route']]
    store=Storage(tmp_path)
    results=[r for r,_ in rows(store)]
    deferred=next(r for r in results if r.get('deferred'))
    assert deferred['route']==shadow['verification']['route'] and deferred['verified_at']
    assert all(r['route']!='pending' for r in results)
    store.close()
    assert run_pending(tmp_path,verifier=lambda d:pytest.fail('finished records are not re-verified'))==[]


def test_off_preserves_pending_records_without_model_calls(tmp_path,monkeypatch):
    store=Storage(tmp_path);state,raw=seed(store);state['draft']=raw
    make_verification_node(store,mode='defer')(state)
    before=rows(store)
    store.close()
    monkeypatch.setenv('A4B_VERIFIER_MODE','off')
    assert run_pending(tmp_path,verifier=lambda d:pytest.fail('off must pause queued reviews'))==[]
    store=Storage(tmp_path)
    assert rows(store)==before
    store.close()


def test_defer_does_not_change_the_design_route(tmp_path):
    store=Storage(tmp_path);state,raw=seed(store)
    routes={}
    for mode in ('off','defer'):
        graph,connection=build_graph(store,conversation=lambda s:dict(draft=raw,repair_count=1),verifier_mode=mode)
        result=graph.invoke({**state,'session_id':state['session_id']+mode},{'configurable':{'thread_id':state['session_id']+mode}})
        routes[mode]=(result.get('status'),result['__interrupt__'][0].value['kind'] if result.get('__interrupt__') else None)
        connection.close()
    assert routes['defer']==routes['off']
    store.close()


def test_unknown_mode_is_rejected(tmp_path):
    store=Storage(tmp_path);state,raw=seed(store)
    with pytest.raises(ValueError,match='off/shadow/defer/enforce'):make_verification_node(store,mode='later')(state)
    store.close()
