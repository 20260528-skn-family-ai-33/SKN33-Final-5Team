"""A fully confirmed schedule is written into the draft by code, not left to the planner.

Live 2026-11-03 (RunPod): "한국 시간 매주 금요일 오후 6시에 …" had every schedule slot confirmed,
yet the planner left draft.schedule empty in 2 of 3 runs and the turn ended validation_failed.
"""
from agent_builder.conditions.condition_check import audit_conditions
from agent_builder.conditions.condition_readiness import plan_issues
from tests.conditions.test_condition_readiness import state, proposal, checked

TABLE = {'id': 't1', 'kind': 'table', 'name': '거래.csv', 'columns': ['거래ID', '비용', '상태'], 'row_count': 3,
         'version': '1', 'profile': {}}
FILTER = {'filter.field': '비용', 'filter.operator': 'gte', 'filter.value': '500000',
          'missing.policy': 'keep', 'duplicate.policy': 'keep', 'output.format': 'json'}
PLAN = {'filters': [{'field': '비용', 'operator': 'gte', 'value': 500000.0, 'compare_field': None, 'value_type': 'number'}],
        'sort': [], 'match': 'all', 'missing_policy': 'keep', 'duplicate_policy': 'keep', 'duplicate_field': None, 'output_format': 'json'}


def audited(text, schedule=None, enforced=True):
    s = state(text + ' 비용이 50만원 이상인 행만 JSON으로 저장해줘. 빈 비용은 그대로 둬. 중복은 유지해.')
    s['resources'] = [TABLE]
    ledger = checked(s, proposal(s, FILTER, ['filter']))
    draft = {'execution_plan': PLAN, 'schedule': schedule, 'questions': [], 'unsupported': [],
             'workflow': {'nodes': [{'id': 'f', 'tool': 'filter_sort', 'settings': {'execution_plan': PLAN}, 'bindings': []},
                                    {'id': 'out', 'tool': 'write_result', 'settings': {'format': 'json'}, 'bindings': []}]}}
    s.update(condition_ledger=ledger, condition_enforced=enforced, draft=draft)
    return s, ledger, audit_conditions(s)


def test_missing_schedule_is_filled_from_confirmed_slots():
    s, ledger, update = audited('한국 시간 매주 금요일 오후 6시에')
    assert ledger['decision'] == 'ready'
    assert update['draft']['schedule'] == {'frequency': 'weekly', 'timezone': 'Asia/Seoul', 'hour': 18, 'minute': 0, 'weekday': 4}
    assert not update['trace'][-1]['issues'] and not plan_issues(ledger, update['draft'])


def test_wrong_schedule_from_the_planner_is_replaced():
    wrong = {'frequency': 'daily', 'timezone': 'UTC', 'hour': 9, 'minute': 0, 'weekday': None}
    _, ledger, update = audited('한국 시간 매주 금요일 오후 6시에', wrong)
    assert update['draft']['schedule']['frequency'] == 'weekly' and update['draft']['schedule']['hour'] == 18


def test_daily_schedule_has_no_weekday():
    _, ledger, update = audited('한국 시간 매일 오전 9시에')
    assert update['draft']['schedule'] == {'frequency': 'daily', 'timezone': 'Asia/Seoul', 'hour': 9, 'minute': 0, 'weekday': None}


def test_unconfirmed_schedule_is_not_filled():
    _, ledger, update = audited('매주 금요일 오후 6시에')  # timezone not written -> asked, never filled
    assert ledger['decision'] == 'clarify' and update == {}


def test_no_fill_without_enforced_conditions_or_without_schedule():
    _, _, update = audited('한국 시간 매주 금요일 오후 6시에', enforced=False)
    assert 'draft' not in update
    s = state('비용이 50만원 이상인 행만 JSON으로 저장해줘. 빈 비용은 그대로 둬. 중복은 유지해.'); s['resources'] = [TABLE]
    ledger = checked(s, proposal(s, FILTER, ['filter']))
    s.update(condition_ledger=ledger, condition_enforced=True, draft={'execution_plan': PLAN, 'schedule': None, 'questions': [], 'unsupported': [],
             'workflow': {'nodes': [{'id': 'f', 'tool': 'filter_sort', 'settings': {'execution_plan': PLAN}, 'bindings': []},
                                    {'id': 'out', 'tool': 'write_result', 'settings': {'format': 'json'}, 'bindings': []}]}})
    assert 'draft' not in audit_conditions(s)
