"""Regression coverage for assigned issues #49 and #50."""
from copy import deepcopy

import pytest

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.conditions.condition_check import audit_conditions
from agent_builder.design.condition_templates import compile_conditions
from agent_builder.design.nodes.validation import validate_node
from tests.conditions.test_condition_readiness import state, proposal, checked, workflow_draft


def filter_state():
    s = state()
    s['condition_ledger'] = checked(s, proposal(s))
    s['draft'] = workflow_draft(s)
    return s


@pytest.mark.parametrize('sort', ['비용이 큰 순서로 정렬', '비용이 작은 순서로 정렬', '비용 내림차순', '비용을 정렬해줘'])
def test_issue49_sort_cannot_disappear_when_requirement_audit_is_off(sort):
    s = filter_state()
    # Freeze an otherwise valid template, then add an explicit sort requirement.
    d = compile_conditions(s)['draft']
    s['messages'].append({'role': 'user', 'content': sort})
    s.update(draft=d, requirement_required=False)
    with pytest.raises(ValueError, match='정렬'):
        compile_conditions(s)
    result = validate_node(s)
    assert result['status'] != 'ready'
    assert any('정렬' in issue for issue in result['issues'])
    with pytest.raises(ValueError, match='정렬'):
        require_save_ready({**s, 'status': 'ready', 'confirmed': True})


def test_issue49_actual_sort_is_accepted():
    s = filter_state()
    s['messages'].append({'role': 'user', 'content': '비용이 큰 순서로 정렬'})
    s['draft']['workflow']['nodes'][1]['settings']['execution_plan']['sort'] = [
        {'field': '비용', 'direction': 'descending', 'value_type': 'number'}]
    assert validate_node(s)['status'] == 'ready'


def test_issue49_sort_cancellation_keeps_input_order():
    from agent_builder.design.sorting import sorting_requested

    s = state('비용 내림차순으로 정렬해줘.')
    s['messages'].append({'role': 'user', 'content': '정렬하지 마. 입력 순서를 유지해.'})
    assert not sorting_requested(s)


def scheduled_state():
    s = state('한국 시간 매일 오전 9시에 비용 100만원 초과인 행만 JSON으로 저장해줘. 빈 값 오류, 중복 유지.')
    s['resources'] = [{'id': 'table', 'name': '비용.csv', 'kind': 'table', 'columns': ['비용'], 'profile': {}}]
    values = {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000',
              'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json',
              'schedule.frequency': 'daily', 'schedule.hour': '9', 'schedule.minute': '0',
              'schedule.timezone': 'Asia/Seoul'}
    s.update(condition_enforced=True, condition_ledger=checked(s, proposal(s, values, ['filter', 'schedule'])))
    s['draft'] = workflow_draft(s)
    s['draft'].update(schedule=None, report_destination=None, document_ids=['table'])
    s['draft']['workflow']['nodes'][0]['settings']['file_ids'] = ['table']
    return s


@pytest.mark.parametrize('existing_schedule', [False, True])
def test_issue50_fills_destination_even_when_schedule_already_matches(existing_schedule):
    s = scheduled_state()
    if existing_schedule:
        s['draft']['schedule'] = {'frequency': 'daily', 'timezone': 'Asia/Seoul', 'hour': 9, 'minute': 0, 'weekday': None}
    before = deepcopy(s)
    update = audit_conditions(s)
    assert s == before
    assert update['draft']['report_destination'] == 'reports'
    assert update['draft']['schedule']['hour'] == 9
    assert not update['trace'][-1]['issues']
    assert validate_node({**s, **update})['status'] == 'ready'
    assert 'draft' not in audit_conditions({**s, **update})


@pytest.mark.parametrize('enforced, timezone', [(False, True), (True, False)])
def test_issue50_does_not_fill_unconfirmed_schedule(enforced, timezone):
    s = scheduled_state()
    s['condition_enforced'] = enforced
    if not timezone:
        s['condition_ledger']['slots']['schedule.timezone'].update(status='missing', value=None)
        s['condition_ledger']['decision'] = 'clarify'
    assert 'draft' not in audit_conditions(s)


def test_issue50_corrected_schedule_is_persisted(tmp_path):
    from infrastructure.persistence.storage import Storage
    from workflow_engine.scheduling.schedules import Schedules

    s = scheduled_state()
    s.update(audit_conditions(s))
    s.update(validate_node(s), confirmed=True)
    store = Storage(tmp_path)
    try:
        store.add_asset(s['resources'][0], [{'비용': 1500000}])
        identifier = store.save_agent(s)
        config = store.agent(identifier)['config']
        assert config['report_destination'] == 'reports'
        assert config['schedule']['timezone'] == 'Asia/Seoul'
        assert Schedules(store).source(identifier)['asset_id'] == 'table'
    finally:
        store.close()


@pytest.mark.parametrize('direction', ['ascending', 'descending'])
def test_sort_direction_is_checked_without_condition_ledger(direction):
    s = filter_state()
    s['resources'] = [{'id': 't', 'kind': 'table', 'name': '비용.csv', 'columns': ['비용']}]
    s['condition_ledger'] = None
    s['messages'].append({'role': 'user', 'content': '비용이 큰 순서로 정렬해줘.'})
    plan = s['draft']['workflow']['nodes'][1]['settings']['execution_plan']
    plan['sort'] = [{'field': '비용', 'direction': direction, 'value_type': 'number'}]
    result = validate_node(s)
    assert (result['status'] == 'ready') == (direction == 'descending')
    if direction == 'ascending':
        with pytest.raises(ValueError, match='정렬'):
            require_save_ready({**s, 'status': 'ready', 'confirmed': True})


def test_aggregate_sort_cannot_be_dropped_without_condition_ledger():
    s = filter_state()
    s['condition_ledger'] = None
    s['resources'] = [{'id': 't', 'kind': 'table', 'name': '비용.csv', 'columns': ['부서', '비용']}]
    s['messages'] = [{'role': 'user', 'content': '부서별 비용 합계를 큰 순서로 정렬해줘.'}]
    assert any('정렬' in issue for issue in validate_node(s)['issues'])
    with pytest.raises(ValueError, match='정렬'):
        require_save_ready({**s, 'status': 'ready', 'confirmed': True})


@pytest.mark.parametrize('text, expected', [
    ('정렬은 취소해줘', {}),
    ('날짜는 정렬하지 말고 비용이 큰 순서로 정렬해줘', {'비용': 'descending'}),
    ('정렬번호가 100 초과인 행만 남겨줘', {}),
])
def test_sort_directives_and_column_names_are_distinct(text, expected):
    from agent_builder.design.sorting import sorting_contract

    s = state(text)
    s['resources'] = [{'columns': ['날짜', '비용', '정렬번호']}]
    assert sorting_contract(s)['orders'] == expected
    if text == '정렬번호가 100 초과인 행만 남겨줘':
        assert not sorting_contract(s)['unresolved']


def test_sort_cancel_blocks_the_old_sorted_plan():
    s = filter_state()
    s['resources'] = [{'columns': ['비용'], 'id': 't', 'kind': 'table', 'name': '비용.csv'}]
    s['messages'] += [{'role': 'user', 'content': '비용 내림차순으로 정렬해줘'},
                      {'role': 'user', 'content': '정렬은 취소해줘'}]
    assert validate_node(s)['status'] == 'ready'
    s['draft']['workflow']['nodes'][1]['settings']['execution_plan']['sort'] = [
        {'field': '비용', 'direction': 'descending', 'value_type': 'number'}]
    assert validate_node(s)['status'] != 'ready'


@pytest.mark.parametrize('text', ['예약 없이 이번 한 번만 저장해줘',
                                 '결과 보관 위치는 아직 미정이니 먼저 물어봐줘',
                                 '서버 보관함에는 저장하지 마'])
def test_schedule_revision_is_not_automatically_confirmed(text):
    s = scheduled_state()
    s['messages'].append({'role': 'user', 'content': text})
    update = audit_conditions(s)
    assert update.get('draft', s['draft'])['schedule'] is None
    result = validate_node({**s, **update})
    assert result['status'] != 'ready'
    with pytest.raises(ValueError, match='예약|저장 위치'):
        require_save_ready({**s, 'status': 'ready', 'confirmed': True})


@pytest.mark.parametrize('version', ['v7', 'v8'])
def test_sort_request_has_a_satisfiable_generation_schema(monkeypatch, version):
    from agent_builder.design.nodes.direct_conversation import make_direct_conversation
    from agent_builder.design.planner_constraints import table_plan_scope
    from tests.design.test_direct_conversation import Fake

    s = filter_state()
    s.update(condition_enforced=True, issues=[], repair_count=0, node_library=[], required_node_refs=[])
    s['messages'].append({'role': 'user', 'content': '비용이 큰 순서로 정렬해줘.'})
    monkeypatch.setenv('A4B_PLANNER_PROMPT', version)
    monkeypatch.setenv('A4B_TOOL_ADVICE_MODE', 'off')
    fake = Fake(s['draft'])
    assert not table_plan_scope(s)
    make_direct_conversation('', 'fixture', model_factory=lambda: fake)(s)
    assert fake.schema['$defs']['ExecutionPlan']['properties']['sort'].get('maxItems', 1) > 0
    assert fake.schema['properties']['questions'].get('maxItems', 1) > 0


def test_valid_descending_order_is_saved_and_executed(tmp_path):
    from infrastructure.persistence.storage import Storage
    from workflow_engine.runner import run_workflow

    s = scheduled_state()
    s['messages'].append({'role': 'user', 'content': '비용이 큰 순서로 정렬해줘'})
    s['draft']['workflow']['nodes'][1]['settings']['execution_plan']['sort'] = [
        {'field': '비용', 'direction': 'descending', 'value_type': 'number'}]
    s.update(audit_conditions(s))
    s.update(validate_node(s), confirmed=True)
    assert s['status'] == 'ready'
    store = Storage(tmp_path)
    try:
        store.add_asset(s['resources'][0], [{'비용': 2000000}, {'비용': 3000000}])
        identifier = store.save_agent(s)
        result = run_workflow(store, identifier, '실행', '', file_ids=['table'])
        assert result['status'] == 'succeeded'
        assert [row['비용'] for row in result['rows']] == [3000000, 2000000]
    finally:
        store.close()


def test_cancel_one_sort_preserves_the_other_requested_sort():
    from agent_builder.design.sorting import sorting_contract

    s = state('날짜 내림차순으로 정렬해줘')
    s['resources'] = [{'columns': ['날짜', '비용']}]
    s['messages'].append({'role': 'user', 'content': '날짜는 정렬하지 말고 비용이 큰 순서로 정렬해줘'})
    assert sorting_contract(s)['orders'] == {'비용': 'descending'}
    assert not sorting_contract(s)['unresolved']


def test_schedule_retention_is_not_a_cancellation():
    from agent_builder.conditions.schedule_policy import schedule_request_issues

    s = scheduled_state()
    s['messages'].append({'role': 'user', 'content': '예약은 취소하지 마'})
    assert not schedule_request_issues(s)


def test_canonical_operations_do_not_bypass_sort_audit():
    from agent_builder.design.sorting import sort_request_issues

    s = state('비용이 큰 순서로 정렬해줘')
    s['resources'] = [{'columns': ['비용']}]
    d = {'workflow': {'nodes': [
        {'tool': 'aggregate_table', 'settings': {'operation': 'aggregate'}},
        {'tool': 'filter_sort', 'settings': {'operation': 'sort',
            'arguments_json': '{"keys":[{"column":"비용","descending":false}]}'}}]}}
    assert sort_request_issues(s, d)
    d['workflow']['nodes'][1]['settings']['arguments_json'] = '{"keys":[{"column":"비용","descending":true}]}'
    assert not sort_request_issues(s, d)
    d['workflow']['nodes'].pop()
    assert sort_request_issues(s, d)


@pytest.mark.parametrize('canonical', [False, True])
@pytest.mark.parametrize('reversed_keys', [False, True])
def test_multiple_sort_keys_preserve_requested_priority(canonical, reversed_keys):
    import json
    from agent_builder.design.sorting import sort_request_issues
    from workflow_engine.executors.legacy_tools.filter_sort import filter_sort_node

    s = filter_state()
    s['resources'] = [{'id': 't', 'kind': 'table', 'name': '비용.csv', 'columns': ['비용', '날짜']}]
    s['messages'].append({'role': 'user', 'content':
        '비용 내림차순으로 먼저 정렬해줘. 날짜 오름차순으로 동률을 정렬해줘.'})
    keys = [{'field': '비용', 'direction': 'descending', 'value_type': 'number'},
            {'field': '날짜', 'direction': 'ascending', 'value_type': 'text'}]
    if reversed_keys:
        keys.reverse()
    sorter = s['draft']['workflow']['nodes'][1]
    sorter['settings']['execution_plan']['sort'] = keys
    if canonical:
        sorter['settings'].update(operation='sort', arguments_json=json.dumps({
            'keys': [{'column': key['field'], 'descending': key['direction'] == 'descending'} for key in keys]}))
        # Input port and operation validation are covered separately. The same
        # audit must compare priority before accepting either representation.
        assert bool(sort_request_issues(s, s['draft'])) == reversed_keys
    else:
        result = validate_node(s)
        assert (result['status'] != 'ready') == reversed_keys
    if reversed_keys:
        with pytest.raises(ValueError, match='우선순위'):
            require_save_ready({**s, 'status': 'ready', 'confirmed': True})
    else:
        require_save_ready({**s, 'status': 'ready', 'confirmed': True})

    context = {'rows': [{'비용': 2000000, '날짜': '2026-11-03'},
                        {'비용': 3000000, '날짜': '2026-11-04'},
                        {'비용': 3000000, '날짜': '2026-11-02'}],
               'columns': ['비용', '날짜'], 'trace': []}
    filter_sort_node(context, [], keys, missing_policy='error', duplicate_policy='keep')
    if not reversed_keys:
        assert [(row['비용'], row['날짜']) for row in context['rows']] == [
            (3000000, '2026-11-02'), (3000000, '2026-11-04'), (2000000, '2026-11-03')]


def test_unattached_sort_is_not_silently_approved():
    s = filter_state()
    s.update(resources=[], condition_ledger=None)
    s['messages'] = [{'role': 'user', 'content': '비용이 큰 순서로 정렬해서 JSON으로 저장해줘'}]
    assert validate_node(s)['status'] != 'ready'
    with pytest.raises(ValueError, match='정렬'):
        require_save_ready({**s, 'status': 'ready', 'confirmed': True})


def test_filter_field_does_not_confuse_sort_field():
    s = filter_state()
    s['resources'] = [{'id': 't', 'kind': 'table', 'name': '비용.csv', 'columns': ['비용', '날짜']}]
    s['messages'].append({'role': 'user', 'content': '비용이 100만원 초과인 행을 날짜 오름차순으로 정렬해줘'})
    s['draft']['workflow']['nodes'][1]['settings']['execution_plan']['sort'] = [
        {'field': '날짜', 'direction': 'ascending', 'value_type': 'text'}]
    assert validate_node(s)['status'] == 'ready'


def test_input_order_reply_cancels_previous_sort():
    s = filter_state()
    s['messages'] += [{'role': 'user', 'content': '비용 내림차순으로 정렬해줘'},
                      {'role': 'user', 'content': '입력 순서를 유지해줘'}]
    assert validate_node(s)['status'] == 'ready'
    s['draft']['workflow']['nodes'][1]['settings']['execution_plan']['sort'] = [
        {'field': '비용', 'direction': 'descending', 'value_type': 'number'}]
    assert validate_node(s)['status'] != 'ready'


@pytest.mark.parametrize('reply', ['보고서 제목은 매일 비용 보고서로 해줘', '매주 보고서라는 이름으로 바꿔줘'])
def test_metadata_does_not_reactivate_cancelled_schedule(reply):
    s = scheduled_state()
    s['messages'] += [{'role': 'user', 'content': '예약 없이 이번 한 번만 저장해줘'},
                      {'role': 'user', 'content': reply}]
    update = audit_conditions(s)
    assert update.get('draft', s['draft'])['schedule'] is None
    assert validate_node({**s, **update})['status'] != 'ready'
    with pytest.raises(ValueError, match='예약'):
        require_save_ready({**s, 'status': 'ready', 'confirmed': True})


@pytest.mark.parametrize('reply', ['저장 위치는 서버 보관함으로 할게', '서버 보관함에 저장해줘', 'reports로 확정해줘'])
def test_destination_selection_resolves_pending_question(reply):
    s = scheduled_state()
    s['messages'] += [{'role': 'user', 'content': '결과 보관 위치는 아직 미정이니 먼저 물어봐줘'},
                      {'role': 'user', 'content': reply}]
    s.update(audit_conditions(s))
    assert s['draft']['report_destination'] == 'reports'
    assert validate_node(s)['status'] == 'ready'


@pytest.mark.parametrize('reverse', [False, True])
def test_followup_sort_priority_replaces_previous_order(reverse):
    from agent_builder.design.sorting import sorting_contract
    s = filter_state()
    s['resources'] = [{'id': 't', 'kind': 'table', 'name': '비용.csv', 'columns': ['비용', '날짜']}]
    s['messages'] += [
        {'role': 'user', 'content': '비용 내림차순으로 먼저 정렬해줘. 날짜 오름차순으로 동률을 정렬해줘.'},
        {'role': 'user', 'content': '날짜 오름차순으로 먼저 정렬해줘. 비용 내림차순으로 동률을 정렬해줘.'}]
    assert list(sorting_contract(s)['orders']) == ['날짜', '비용']
    keys = [{'field': '날짜', 'direction': 'ascending', 'value_type': 'text'},
            {'field': '비용', 'direction': 'descending', 'value_type': 'number'}]
    s['draft']['workflow']['nodes'][1]['settings']['execution_plan']['sort'] = keys[::-1] if reverse else keys
    assert (validate_node(s)['status'] == 'ready') == (not reverse)
    if reverse:
        with pytest.raises(ValueError, match='정렬'):
            require_save_ready({**s, 'status': 'ready', 'confirmed': True})
    else:
        require_save_ready({**s, 'status': 'ready', 'confirmed': True})


def test_explicit_schedule_renewal_is_allowed():
    s = scheduled_state()
    s['messages'] += [{'role': 'user', 'content': '예약 없이 이번 한 번만 저장해줘'},
                      {'role': 'user', 'content': '매일 오전 9시에 다시 예약해줘'}]
    s.update(audit_conditions(s))
    assert s['draft']['schedule']['hour'] == 9
    assert validate_node(s)['status'] == 'ready'


def test_revised_sort_priority_is_saved_and_executed(tmp_path):
    from infrastructure.persistence.storage import Storage
    from workflow_engine.runner import run_workflow
    s = scheduled_state()
    s['resources'][0]['columns'] = ['비용', '날짜']
    s['messages'] += [
        {'role': 'user', 'content': '비용 내림차순으로 먼저 정렬해줘. 날짜 오름차순으로 동률을 정렬해줘.'},
        {'role': 'user', 'content': '날짜 오름차순으로 먼저 정렬해줘. 비용 내림차순으로 동률을 정렬해줘.'}]
    s['draft']['workflow']['nodes'][1]['settings']['execution_plan']['sort'] = [
        {'field': '날짜', 'direction': 'ascending', 'value_type': 'text'},
        {'field': '비용', 'direction': 'descending', 'value_type': 'number'}]
    s.update(audit_conditions(s))
    s.update(validate_node(s), confirmed=True)
    assert s['status'] == 'ready'
    store = Storage(tmp_path)
    try:
        store.add_asset(s['resources'][0], [
            {'비용': 3000000, '날짜': '11/04'}, {'비용': 2000000, '날짜': '11/03'},
            {'비용': 4000000, '날짜': '11/03'}])
        identifier = store.save_agent(s)
        result = run_workflow(store, identifier, '실행', '', file_ids=['table'])
        assert result['status'] == 'succeeded'
        assert [row['비용'] for row in result['rows']] == [4000000, 2000000, 3000000]
    finally:
        store.close()
