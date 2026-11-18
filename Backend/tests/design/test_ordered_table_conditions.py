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


def _ordered_compound_state(request):
    from agent_builder.conditions.condition_readiness import compound_filter_state
    from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest
    from tests.conditions.test_condition_readiness import checked, proposal, state
    from tests.design.test_condition_requirement_ownership import TABLE

    current = state(request)
    current['resources'] = [dict(TABLE, format='json', columns=['customer_id', 'amount', 'quantity'])]
    values = {'filter.field': 'amount', 'filter.operator': 'gte', 'filter.value': '15',
              'duplicate.field': 'customer_id', 'duplicate.policy': 'first',
              'missing.policy': 'error', 'output.format': 'json'}
    ledger = checked(current, proposal(current, values, ['deduplicate', 'filter']))
    assert ledger['decision'] == 'ready'
    current.update(condition_ledger=ledger, condition_enforced=True, condition_required=True)
    requirements = extract_plan(packet({'kind': 'export', 'format': 'json'}), current)
    current.update(requirement_ledger=requirements, requirement_required=True,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(requirements))

    draft = combined_draft(current)
    draft['workflow']['nodes'][1]['settings']['execution_plan']['filters'] = compound_filter_state(current)['rules']
    draft['execution_plan'] = deepcopy(draft['workflow']['nodes'][1]['settings']['execution_plan'])
    current.update(draft=draft, confirmed=True)
    return current


@pytest.mark.parametrize('combined', [False, True])
def test_explicit_deduplication_before_compound_filter_can_be_saved(tmp_path, combined):
    from agent_builder.design.condition_requirements import ordered_row_steps
    from agent_builder.design.nodes.validation import validate_node
    from infrastructure.persistence.storage import Storage
    from workflow_engine.runner import run_workflow

    current = _ordered_compound_state(
        'customer_id 중복은 첫 행만 남긴 뒤 amount가 15 이상이고 quantity가 3 이상인 행을 '
        'JSON으로 저장해줘. 빈 값은 오류.')
    assert ordered_row_steps(current) == ('policy', 'filter')
    draft = current['draft']
    workflow = draft['workflow']
    reader, selection, writer = workflow['nodes']
    plan = selection['settings']['execution_plan']
    if not combined:
        dedup = deepcopy(selection)
        dedup['id'] = 'deduplicate'
        dedup['settings']['execution_plan']['filters'] = []
        selection['bindings'][0]['node_id'] = dedup['id']
        plan.update(duplicate_field=None, duplicate_policy='keep')
        workflow['nodes'] = [reader, dedup, selection, writer]
        workflow['edges'] = [dict(source=a['id'], target=b['id'], branch='next')
                             for a, b in zip(workflow['nodes'], workflow['nodes'][1:])]
    validation = validate_node(current)
    assert validation['status'] == 'ready', validation['issues']
    current['draft'] = validation['draft']
    rows = [dict(customer_id='a', amount=5, quantity=3), dict(customer_id='a', amount=20, quantity=3),
            dict(customer_id='b', amount=20, quantity=4), dict(customer_id='c', amount=20, quantity=1)]
    store = Storage(tmp_path)
    try:
        store.add_asset(current['resources'][0], rows)
        saved = store.save_agent(current)
        result = run_workflow(store, saved, '실행', '', file_ids=[current['resources'][0]['id']])
        assert result['status'] == 'succeeded'
        assert result['rows'] == [rows[2]]
    finally:
        store.close()


def test_split_compound_filter_cannot_hide_a_required_missing_value_error(tmp_path):
    from agent_builder.design.nodes.validation import validate_node
    from infrastructure.persistence.storage import Storage
    from tests.design.test_compound_filter_pipeline import path_state, with_filter_chain
    from workflow_engine.runner import run_workflow

    current = path_state('all')
    current['requirement_required'] = False
    plans = current['draft']['workflow']['nodes'][1]['settings']['execution_plan']
    split = with_filter_chain(current, [dict(filters=[rule]) for rule in plans['filters']])
    store = Storage(tmp_path)
    asset = current['resources'][0]
    try:
        store.add_asset(asset, [dict(비용=5, 수량=None), dict(비용=15, 수량=4)])
        assert validate_node(split)['status'] != 'ready'
        with pytest.raises(ValueError, match='확정 업무 조건'):
            store.save_agent(split)
        saved = store.save_agent(current)
        result = run_workflow(store, saved, '실행', '', file_ids=[asset['id']])
        assert result['status'] == 'failed' and '빈 값' in result['error']
    finally:
        store.close()


def test_unrequested_upstream_sort_cannot_change_first_deduplicated_row(tmp_path):
    from agent_builder.design.condition_requirements import ordered_row_steps
    from agent_builder.design.nodes.validation import validate_node
    from infrastructure.persistence.storage import Storage
    from tests.design.test_compound_filter_pipeline import with_filter_chain
    from workflow_engine.runner import run_workflow

    current = _ordered_compound_state(
        'amount가 15 이상이고 quantity가 3 이상인 행을 남긴 뒤 '
        'customer_id 중복은 첫 행만 남겨서 JSON으로 저장해줘. 빈 값은 오류.')
    assert ordered_row_steps(current) == ('filter', 'policy')
    selection = dict(duplicate_policy='keep', duplicate_field=None)
    deduplication = dict(filters=[])
    ordered = with_filter_chain(current, [selection, deduplication])
    sort = [dict(field='amount', direction='descending', value_type='number')]
    reordered = with_filter_chain(current, [dict(selection, sort=sort), deduplication])
    sort_after_dedup = with_filter_chain(current, [selection, dict(deduplication, sort=sort)])
    rows = [dict(customer_id='a', amount=20, quantity=4), dict(customer_id='a', amount=30, quantity=4)]
    asset = current['resources'][0]
    store = Storage(tmp_path)
    try:
        store.add_asset(asset, rows)
        assert validate_node(reordered)['status'] != 'ready'
        with pytest.raises(ValueError, match='확정 업무 조건'):
            store.save_agent(reordered)
        for candidate in (ordered, sort_after_dedup):
            assert validate_node(candidate)['status'] == 'ready'
            saved = store.save_agent(candidate)
            result = run_workflow(store, saved, '실행', '', file_ids=[asset['id']])
            assert result['status'] == 'succeeded' and result['rows'] == rows[:1]
    finally:
        store.close()


@pytest.mark.parametrize('retention', ['first', 'last'])
def test_unrequested_deduplication_after_compound_filter_cannot_be_saved(tmp_path, retention):
    from agent_builder.design.nodes.validation import validate_node
    from infrastructure.persistence.storage import Storage
    from tests.conditions.test_clarification_choices import PARTIAL, compound_state
    from tests.design.test_compound_filter_pipeline import generate, seal, with_filter_chain
    from workflow_engine.runner import run_workflow

    current = compound_state('비용이 10원 미만이거나 수량이 3개 미만인 행을 JSON으로 저장해줘. 빈 값은 오류.')
    asset = current['resources'][0]
    asset['columns'].append('거래ID')
    current = generate(seal(current, {**PARTIAL, 'output.format': ('json', 'JSON'), 'missing.policy': ('error', '오류')}))
    plan = current['draft']['workflow']['nodes'][1]['settings']['execution_plan']
    reduced = with_filter_chain(current, [plan, dict(filters=[], duplicate_policy=retention, duplicate_field='거래ID')])

    rows = [dict(비용=5, 수량=3, 거래ID=1), dict(비용=6, 수량=4, 거래ID=1), dict(비용=15, 수량=4, 거래ID=2)]
    store = Storage(tmp_path)
    try:
        store.add_asset(asset, rows)
        assert validate_node(reduced)['status'] != 'ready'
        with pytest.raises(ValueError, match='확정 업무 조건'):
            store.save_agent(reduced)
        saved = store.save_agent(current)
        result = run_workflow(store, saved, '실행', '', file_ids=[asset['id']])
        assert result['status'] == 'succeeded' and result['rows'] == rows[:2]
    finally:
        store.close()


@pytest.mark.parametrize('source_name', ['주문표.csv', '"주문표.csv"', '참고표.csv에서 읽지 말고 주문표.csv'])
def test_explicit_compound_source_with_another_same_schema_table(tmp_path, source_name):
    from agent_builder.conditions.condition_readiness import compound_filter_state
    from agent_builder.design.nodes.validation import validate_node
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    from infrastructure.persistence.storage import Storage
    from tests.conditions.test_clarification_choices import compound_state, filter_draft
    from tests.design.test_compound_filter_pipeline import seal
    from workflow_engine.runner import run_workflow

    current = compound_state(f'{source_name}에서 비용이 10원 이상이고 수량이 3개 이상인 행을 JSON으로 저장해줘. '
                             '빈 값은 오류, 중복은 유지해.')
    selected = current['resources'][0]
    selected['name'] = '주문표.csv'
    other = dict(selected, id='another-table', name='참고표.csv')
    current['resources'].append(other)
    current = seal(current)
    draft = filter_draft(current, compound_filter_state(current)['rules'], 'all')
    draft['document_ids'] = [selected['id'], other['id']]
    draft['workflow']['nodes'][0]['settings']['file_ids'] = [selected['id']]
    draft['execution_plan'] = deepcopy(draft['workflow']['nodes'][1]['settings']['execution_plan'])
    current.update(draft=draft, confirmed=True)
    wrong_source = deepcopy(current)
    wrong_source['draft']['workflow']['nodes'][0]['settings']['file_ids'] = [other['id']]
    ambiguous_source = deepcopy(current)
    ambiguous_source['resources'][1]['name'] = selected['name']
    assert numeric_filter_issues(ambiguous_source, ambiguous_source['draft'])

    rows = [dict(비용=5, 수량=3), dict(비용=20, 수량=3)]
    store = Storage(tmp_path)
    try:
        store.add_asset(selected, rows)
        store.add_asset(other, [dict(비용=90, 수량=8)])
        assert validate_node(wrong_source)['status'] != 'ready'
        with pytest.raises(ValueError, match='확정 업무 조건'):
            store.save_agent(wrong_source)
        assert validate_node(current)['status'] == 'ready'
        saved = store.save_agent(current)
        result = run_workflow(store, saved, '실행', '', file_ids=[selected['id'], other['id']])
        assert result['status'] == 'succeeded' and result['rows'] == rows[1:]
    finally:
        store.close()
