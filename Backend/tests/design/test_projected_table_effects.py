"""Replay reviewed Requirement candidates against the runtime's condition and save contracts."""
import pytest

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.design.condition_templates import compile_conditions
from agent_builder.design.requirement_ledger import make_requirement_check
from tests.conditions.test_condition_readiness import Fake, checked, proposal, state


ASSET = {'id': 'synthetic-depot', 'kind': 'table', 'name': '창고정산.json', 'columns': ['credit']}
FILTER_REQUEST = '창고정산 표에서 credit이 0 이하인 원본 행만 현재 순서로 CSV로 내려받고 싶어. 빈 셀은 오류로 처리하고 중복 행은 유지해.'
SCHEDULE_REQUEST = '연결한 창고정산 표 한 장을 매주 금요일 한국 시간 오후 5시 20분에 CSV로 저장해줘.'
NEW_YORK_REQUEST = '연결한 창고정산 표 한 장을 매주 금요일 뉴욕 현지 시각 오후 5시 20분에 CSV로 저장해줘. 날짜마다 변하는 서머타임은 뉴욕 시간대를 따라야 해.'


def replay(request, action, description, operation):
    current = state(request)
    current.update(resources=[ASSET], condition_required=True, condition_enforced=True)
    values = ({'filter.field': 'credit', 'filter.operator': 'lte', 'filter.value': '0',
               'duplicate.policy': 'keep', 'missing.policy': 'error', 'output.format': 'csv'} if operation == 'filter'
              else {'output.format': 'csv'})
    current['condition_ledger'] = checked(current, proposal(current, values, [operation]))
    assert current['condition_ledger']['decision'] == 'ready'
    raw = {'tasks': [{'source_message': 0, 'spec': {'kind': 'other', 'action': action},
                      'description': description}], 'unresolved': []}
    current.update(make_requirement_check('fixture', Fake([raw]), 'enforce')(current))
    current.update(compile_conditions(current), confirmed=True)
    return current


@pytest.mark.parametrize('request_text,action,description,operation', [
    (FILTER_REQUEST, 'export_csv_from_table', 'credit이 0 이하인 행 필터링 및 CSV 내보내기', 'filter'),
    (SCHEDULE_REQUEST, 'schedule_export', '매주 금요일 한국 시간 오후 5시 20분에 창고정산 표를 CSV로 저장', 'schedule'),
    (FILTER_REQUEST, 'filter-and-write-csv', 'credit ≤ 0인 행만 CSV로 저장', 'filter'),
    (SCHEDULE_REQUEST, 'schedule_save_csv', '매주 금요일 한국 시간 오후 5시 20분에 창고정산 표를 CSV로 저장', 'schedule'),
])
def test_verified_input_trigger_predicate_and_format_cover_the_model_candidate(request_text, action, description, operation):
    current = replay(request_text, action, description, operation)
    tasks = current['requirement_ledger']['tasks']
    assert [task['spec']['kind'] for task in tasks] == ([operation, 'policy', 'export'] if operation == 'filter' else [operation, 'export'])
    assert tasks[-1]['depends_on'] == [tasks[0]['id']]
    require_save_ready(current)


@pytest.mark.parametrize('request_text,action,description,operation', [
    (FILTER_REQUEST, 'export_csv_from_table', 'credit이 0 미만인 행 필터링 및 CSV 내보내기', 'filter'),
    (FILTER_REQUEST, 'export_csv_from_table', '원본 CSV 내보내기 후 credit이 0 이하인 행 필터링', 'filter'),
    (FILTER_REQUEST, 'export_csv_and_notify', 'credit이 0 이하인 행 필터링 및 CSV 내보내기', 'filter'),
    (FILTER_REQUEST, 'write_json_from_table', 'credit이 0 이하인 행 필터링 및 CSV 내보내기', 'filter'),
    (FILTER_REQUEST, 'write_csv_and_filter', 'credit이 0 이하인 행 필터링 및 CSV 내보내기', 'filter'),
    (FILTER_REQUEST, 'export_csv_from_table', 'credit이 0 이하인 행 필터링 및 CSV 내보내기 후 발송', 'filter'),
    (SCHEDULE_REQUEST, 'schedule_export', '매주 금요일 한국 시간 오후 5시 21분에 창고정산 표를 CSV로 저장', 'schedule'),
    (SCHEDULE_REQUEST, 'schedule_export', '매주 금요일 뉴욕 시간 오후 5시 20분에 창고정산 표를 CSV로 저장', 'schedule'),
    (NEW_YORK_REQUEST, 'schedule_export', '매주 금요일 한국 시간 오후 5시 20분에 창고정산 표를 CSV로 저장', 'schedule'),
    (NEW_YORK_REQUEST, 'schedule_export', '매주 금요일 뉴욕 시간 오후 5시 20분에 창고정산 표를 CSV로 저장', 'schedule'),
    (SCHEDULE_REQUEST, 'schedule_export', '매주 금요일 한국 시간 오후 5시 20분에 재고 표를 CSV로 저장', 'schedule'),
])
def test_different_execution_effect_is_kept(request_text, action, description, operation):
    current = replay(request_text, action, description, operation)
    assert any(t['spec']['kind'] == 'other' for t in current['requirement_ledger']['tasks'])
    with pytest.raises(ValueError):
        require_save_ready(current)


@pytest.mark.parametrize('action,description,covered', [
    ('filter_and_deduplicate_then_export', 'amount 가 15 이상인 행을 필터링하고 customer_id 가 같은 행 중 첫 번째만 유지한 뒤 JSON 로 저장한다.', True),
    ('filter_and_deduplicate_then_export', 'amount 가 15 이상인 행을 필터링하고 customer_id 가 같은 행 중 마지막만 유지한 뒤 JSON 로 저장한다.', False),
    ('filter_then_deduplicate_then_export', 'amount 가 15 이상인 행을 필터링하고 region 가 같은 행 중 첫 번째만 유지한 뒤 JSON 로 저장한다.', False),
    ('deduplicate_and_filter_then_export', 'amount 가 15 이상인 행을 필터링하고 customer_id 가 같은 행 중 첫 번째만 유지한 뒤 JSON 로 저장한다.', False),
    ('filter_and_deduplicate_then_export', 'customer_id 가 같은 행 중 첫 번째만 유지하고 amount 가 15 이상인 행을 필터링한 뒤 JSON 로 저장한다.', False),
    ('filter_and_deduplicate_then_export', 'amount 가 15 이상인 행을 필터링하고 customer_id 가 같은 행 중 첫 번째만 유지한 뒤 팀에 알리고 JSON 로 저장한다.', False),
])
def test_packed_table_operations_need_the_same_input_order_retention_and_output(action, description, covered):
    from agent_builder.design.requirement_ledger import extract_plan
    from tests.design.test_condition_requirement_ownership import validated_state

    request = '먼저 amount가 15 이상인 행만 골라. 그 결과에서 customer_id가 같은 행은 첫 행만 남기고 JSON으로 저장해줘. 빈 값은 오류.'
    current = validated_state(request, with_filter=True)
    candidate = dict(source_message=0, spec={'kind': 'other', 'action': action}, description=description)
    tasks = extract_plan({'tasks': [candidate], 'unresolved': []}, current)['tasks']
    assert ('other' not in [t['spec']['kind'] for t in tasks]) == covered
    if covered:
        assert [t['spec']['kind'] for t in tasks] == ['filter', 'policy', 'export']
        assert tasks[1]['depends_on'] == [tasks[0]['id']]
        assert tasks[2]['depends_on'] == [tasks[1]['id']]
