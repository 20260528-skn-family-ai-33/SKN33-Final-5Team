"""Use existing table fixtures; no new model calls or ML evaluation data."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.nodes.verification import make_verification_node
from agent_builder.design.verifier_columns import reconcile_column_findings
from infrastructure.persistence.storage import Storage
from tests.verification.test_verifier_bridge import seed, fixture_verifier, PROMPT
from tests.workflow_engine.test_workflow import node, bind, edge
from verification.workflow.contracts import VerificationInput
from verification.workflow.service import verify


def derived(store, *, aggregate=False):
    state,raw=seed(store)
    state['messages'][0]['content']=PROMPT+' 보증금에 1을 곱한 환산금액 열을 만들어 그 열로 선별하고 숫자 내림차순으로 정렬해줘.'
    read,filtered,out=raw['workflow']['nodes']
    calc=node('calc','calculate_columns',[bind('table','read','table')],
              table_operation='compute',calculation_formula='보증금 * 1',output_column='환산금액')
    filtered['bindings']=[bind('table','calc','table')]
    filtered['settings']['execution_plan']['filters'][0]['field']='환산금액'
    filtered['settings']['execution_plan']['sort']=[dict(field='환산금액',direction='descending',value_type='number')]
    steps=[read,calc,filtered,out]
    if aggregate:
        total=node('total','aggregate_table',[bind('table','calc','table')],
                   fields=[],aggregation='sum',sum_field='환산금액')
        out['bindings']=[bind('table','total','table')]
        steps=[read,calc,total,out]
    raw['workflow']={'entry':'read','nodes':steps,
                    'edges':[edge(a['id'],b['id']) for a,b in zip(steps,steps[1:])]}
    raw['tool_ids']=list(dict.fromkeys(n['tool'] for n in steps))
    state['draft']=raw
    return state,raw


@pytest.mark.parametrize('aggregate',[False,True])
@pytest.mark.parametrize('parser_status',['ready','succeeded'])
def test_native_derived_columns_are_rechecked_and_original_findings_are_audited(tmp_path,aggregate,parser_status):
    store=Storage(tmp_path)
    try:
        state,raw=derived(store,aggregate=aggregate);before=deepcopy(state)
        state['resources'][0]['parser_status']=parser_status
        before=deepcopy(state)
        result=make_verification_node(store,verifier=fixture_verifier,mode='enforce')(state)
        assert result['verification_route']=='continue_review'
        review=result['verification']['column_lineage_review']
        assert review['original_route']=='repair_plan' and review['rechecked']
        assert all(x['finding'] in review['original_findings'] for x in review['rechecked'])
        assert result['verification']['execution_allowed'] is False
        assert state==before
        saved=json.loads(store.db.execute('SELECT result_json FROM builder_verifications').fetchone()[0])
        assert saved['column_lineage_review']==review
    finally:store.close()


@pytest.mark.parametrize('mutation',['missing_column','wrong_formula','removed_calculation','unknown_operation'])
def test_real_missing_columns_and_unknown_paths_are_not_waived(tmp_path,mutation):
    store=Storage(tmp_path)
    try:
        state,raw=derived(store)
        calc=raw['workflow']['nodes'][1];filtered=raw['workflow']['nodes'][2]
        if mutation=='missing_column':filtered['settings']['execution_plan']['sort'][0]['field']='없는열'
        if mutation=='wrong_formula':calc['settings']['calculation_formula']='없는열 * 1'
        if mutation=='removed_calculation':
            raw['workflow']['nodes'].remove(calc)
            filtered['bindings']=[bind('table','read','table')]
            raw['workflow']['edges']=[edge('read','filter'),edge('filter','save')]
        if mutation=='unknown_operation':
            calc['settings']['operation']='compute'
            calc['settings']['arguments_json']=json.dumps(dict(formula='보증금 * 1',output_column='환산금액'))
        result=make_verification_node(store,verifier=fixture_verifier,mode='enforce')(state)
        assert result['verification_route']!='continue_review'
        assert 'column_lineage_review' not in result['verification']
    finally:store.close()


def test_invalid_model_citations_still_require_review_even_when_columns_are_correct(tmp_path):
    store=Storage(tmp_path)
    try:
        state,raw=derived(store)
        def forged(payload):
            result=fixture_verifier(payload)
            output=result.model_output.model_dump()
            output['evidence'][0]['quote']='사용자가 말하지 않은 인용'
            return verify(payload,mode='fixture',fixture=output)
        result=make_verification_node(store,verifier=forged,mode='enforce')(state)
        assert result['verification_route']=='manual_review'
        assert any(f['code']=='invalid_quote' for f in result['verification']['findings'])
    finally:store.close()


def test_no_model_output_or_transport_error_cannot_become_success(tmp_path):
    store=Storage(tmp_path)
    try:
        state,raw=derived(store)
        make_verification_node(store,mode='defer')(state)
        payload=VerificationInput.model_validate_json(store.db.execute('SELECT input_json FROM builder_verifications').fetchone()[0])
        result=dict(route='manual_review',model_output=None,error_code='timeout',findings=[],execution_allowed=False)
        assert reconcile_column_findings(payload,raw,state['resources'],result)==result
    finally:store.close()


def test_deferred_recheck_uses_the_recorded_snapshot_and_matches_shadow(tmp_path,monkeypatch):
    monkeypatch.setenv('A4B_VERIFIER_MODE','defer')
    from verification.deferred import run_pending
    store=Storage(tmp_path)
    try:
        state,raw=derived(store)
        shadow=make_verification_node(store,verifier=fixture_verifier,mode='shadow')(state)
        make_verification_node(store,mode='defer')(state)
        # Batch proof must come from the recorded input, not current asset state.
        store.db.execute('DELETE FROM builder_assets');store.db.commit()
        done=run_pending(tmp_path,verifier=fixture_verifier)
        assert [r['route'] for r in done]==[shadow['verification_route']]==['continue_review']
        values=[json.loads(r[0]) for r in store.db.execute('SELECT result_json FROM builder_verifications')]
        batch=next(r for r in values if r.get('deferred'))
        assert batch['column_lineage_review']==shadow['verification']['column_lineage_review']
        assert batch['execution_allowed'] is False
    finally:store.close()
