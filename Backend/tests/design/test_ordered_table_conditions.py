from copy import deepcopy

import pytest

from agent_builder.conditions.condition_readiness import plan_issues
from agent_builder.design.requirement_ledger import extract_plan
from tests.conditions.test_condition_readiness import workflow_draft
from tests.design.test_condition_requirement_ownership import COMBINED, packet, validated_state


FILTER_FIRST = ('먼저 amount가 15 이상인 행만 골라. 그 결과에서 customer_id가 같은 행은 '
                '첫 행만 남기고 JSON으로 저장해줘. 빈 값은 오류.')


def combined_draft(state):
    draft = workflow_draft(state, threshold=15, operator='gte')
    plan = draft['workflow']['nodes'][1]['settings']['execution_plan']
    plan['filters'][0]['field'] = 'amount'
    plan.update(duplicate_field='customer_id', duplicate_policy='first')
    draft['execution_plan'] = deepcopy(plan)
    return draft


def test_filtered_rows_feed_deduplication_and_then_export():
    state = validated_state(FILTER_FIRST, with_filter=True)
    plan = extract_plan(packet({'kind': 'export', 'format': 'json'}), state)
    tasks = {t['spec']['kind']: t for t in plan['tasks']}
    assert tasks['filter']['id'] in tasks['policy']['depends_on']
    assert tasks['policy']['id'] in tasks['export']['depends_on']
    assert plan_issues(state['condition_ledger'], combined_draft(state), state=state)


def test_single_native_node_can_remove_duplicates_before_filtering():
    state = validated_state(COMBINED, with_filter=True)
    assert not plan_issues(state['condition_ledger'], combined_draft(state), state=state)


def test_plain_confirmation_preserves_order_but_a_changed_request_is_rechecked():
    from agent_builder.design.condition_requirements import ordered_row_steps
    state = validated_state(FILTER_FIRST, with_filter=True)
    state['messages'].append(dict(role='user', content='네.'))
    assert ordered_row_steps(state) == ('filter', 'policy')
    state['messages'][-1]['content'] = '반대로 중복을 먼저 제거해줘.'
    assert ordered_row_steps(state) == ()


@pytest.mark.parametrize('combined', [False, True])
def test_explicit_deduplication_before_compound_filter_can_be_saved(tmp_path, combined):
    from agent_builder.conditions.condition_readiness import compound_filter_state
    from agent_builder.design.condition_requirements import ordered_row_steps
    from agent_builder.design.nodes.validation import validate_node
    from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest
    from infrastructure.persistence.storage import Storage
    from tests.conditions.test_condition_readiness import checked, proposal, state
    from tests.design.test_condition_requirement_ownership import TABLE
    from workflow_engine.runner import run_workflow

    request = ('customer_id 중복은 첫 행만 남긴 뒤 amount가 15 이상이고 quantity가 3 이상인 행을 '
               'JSON으로 저장해줘. 빈 값은 오류.')
    current = state(request)
    current['resources'] = [dict(TABLE, format='json', columns=['customer_id', 'amount', 'quantity'])]
    values = {'filter.field': 'amount', 'filter.operator': 'gte', 'filter.value': '15',
              'duplicate.field': 'customer_id', 'duplicate.policy': 'first',
              'missing.policy': 'error', 'output.format': 'json'}
    ledger = checked(current, proposal(current, values, ['deduplicate', 'filter']))
    assert ledger['decision'] == 'ready'
    current.update(condition_ledger=ledger, condition_enforced=True, condition_required=True)
    assert ordered_row_steps(current) == ('policy', 'filter')
    requirements = extract_plan(packet({'kind': 'export', 'format': 'json'}), current)
    current.update(requirement_ledger=requirements, requirement_required=True,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(requirements))

    draft = combined_draft(current)
    workflow = draft['workflow']
    reader, selection, writer = workflow['nodes']
    plan = selection['settings']['execution_plan']
    plan['filters'] = compound_filter_state(current)['rules']
    draft['execution_plan'] = deepcopy(plan)
    if not combined:
        dedup = deepcopy(selection)
        dedup['id'] = 'deduplicate'
        dedup['settings']['execution_plan']['filters'] = []
        selection['bindings'][0]['node_id'] = dedup['id']
        plan.update(duplicate_field=None, duplicate_policy='keep')
        workflow['nodes'] = [reader, dedup, selection, writer]
        workflow['edges'] = [dict(source=a['id'], target=b['id'], branch='next')
                             for a, b in zip(workflow['nodes'], workflow['nodes'][1:])]
    current.update(draft=draft, confirmed=True)
    validation = validate_node(current)
    assert validation['status'] == 'ready', validation['issues']
    current['draft'] = validation['draft']
    rows = [dict(customer_id='a', amount=5, quantity=3), dict(customer_id='a', amount=20, quantity=3),
            dict(customer_id='b', amount=20, quantity=4), dict(customer_id='c', amount=20, quantity=1)]
    store = Storage(tmp_path)
    try:
        store.add_asset(current['resources'][0], rows)
        saved = store.save_agent(current)
        result = run_workflow(store, saved, '실행', '', file_ids=[TABLE['id']])
        assert result['status'] == 'succeeded'
        assert result['rows'] == [rows[2]]
    finally:
        store.close()
