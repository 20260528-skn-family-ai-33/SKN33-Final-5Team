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
    assert any('정렬 조건' in issue for issue in result['issues'])
    with pytest.raises(ValueError, match='정렬 조건'):
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
