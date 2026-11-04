"""Auto plan mode: a confirmed filter/dedup/schedule combination becomes the draft in code (A4).

Live 2026-11-04 (RunPod): on fully confirmed single-table requests the planner duplicated or
extended the filter_sort node (B-4 failures 5/82, also on plain main), and the one-operation
template refused 29/50 B-2 requests because they combine filter and duplicate handling.
"""
from agent_builder.design.condition_templates import compile_conditions, template_node
from agent_builder.conditions.condition_readiness import plan_issues
from tests.conditions.test_condition_readiness import state, proposal, checked

TABLE = [{'id': 't', 'kind': 'table', 'columns': ['거래ID', '비용', '상태']}]
TEXT = '비용이 100만원 초과인 행만 남겨줘. 빈 값은 제외해. 중복 거래ID는 마지막 행만 남겨. 정렬은 하지 마. JSON으로 저장해.'
VALUES = {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000', 'missing.policy': 'exclude',
          'duplicate.policy': 'last', 'duplicate.field': '거래ID', 'output.format': 'json', 'sort.policy': 'none'}


def confirmed(text=TEXT, values=VALUES, operations=('filter', 'deduplicate')):
    s = state(text); s['resources'] = list(TABLE)
    s['condition_ledger'] = checked(s, proposal(s, values, list(operations)))
    return s


def test_filter_and_dedup_compile_to_one_node_matching_the_ledger():
    s = confirmed()
    assert s['condition_ledger']['decision'] == 'ready'
    draft = compile_conditions(s)['draft']
    assert [n['tool'] for n in draft['workflow']['nodes']] == ['read_table', 'filter_sort', 'write_result']
    plan = draft['workflow']['nodes'][1]['settings']['execution_plan']
    assert plan['filters'] == [{'field': '비용', 'operator': 'gt', 'value': 1000000.0, 'compare_field': None, 'value_type': 'number'}]
    assert (plan['missing_policy'], plan['duplicate_policy'], plan['duplicate_field']) == ('exclude', 'last', '거래ID')
    assert plan_issues(s['condition_ledger'], draft) == []


def test_filter_with_schedule_carries_the_schedule():
    text = '한국 시간 매주 금요일 오후 6시에 ' + TEXT
    s = confirmed(text, {**VALUES, 'schedule.frequency': 'weekly', 'schedule.hour': '18', 'schedule.minute': '0',
                         'schedule.timezone': 'Asia/Seoul', 'schedule.weekday': '4'}, ('filter', 'deduplicate', 'schedule'))
    draft = compile_conditions(s)['draft']
    assert draft['schedule'] == {'frequency': 'weekly', 'timezone': 'Asia/Seoul', 'hour': 18, 'minute': 0, 'weekday': 4}
    assert plan_issues(s['condition_ledger'], draft) == []


def test_outside_the_template_falls_back_to_the_planner_in_auto(monkeypatch):
    monkeypatch.delenv('A4B_CONDITION_PLAN_MODE', raising=False)
    s = confirmed(TEXT.replace('정렬은 하지 마.', '비용 오름차순으로 정렬해.'))
    result = template_node(s)
    assert result['condition_route'] == 'conversation' and 'status' not in result
    assert '모델 설계로 진행' in result['trace'][-1]['detail']


def test_strict_template_mode_still_stops(monkeypatch):
    monkeypatch.setenv('A4B_CONDITION_PLAN_MODE', 'template')
    s = confirmed(TEXT.replace('정렬은 하지 마.', '비용 오름차순으로 정렬해.'))
    assert template_node(s)['condition_route'] == 'stop'
