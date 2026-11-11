"""A written daily/weekly schedule is kept as a confirmed condition; a monthly one is never weekly.

Live 2026-11-03 (RunPod, 4 schedule requests x 3 runs x 2 paths): the single-operation proposal
labelled 17 of 19 "filter" and omitted every schedule key, so 17 of 17 READY results had no schedule.
"매월 1일 오전 9시에…" was confirmed as weekly when the capability check was skipped.
"""
import pytest
from agent_builder.conditions.condition_readiness import schedule_literals, plan_issues, _grounded_enum
from tests.conditions.test_condition_readiness import state, proposal, checked

TABLE = {'id': 't1', 'kind': 'table', 'name': '거래.csv', 'columns': ['거래ID', '비용', '상태'], 'row_count': 3,
         'version': '1', 'profile': {}}
TAIL = ' 비용이 50만원 이상인 행만 JSON으로 저장해줘. 빈 비용은 그대로 둬. 중복은 유지해.'
FILTER = {'filter.field': '비용', 'filter.operator': 'gte', 'filter.value': '500000',
          'missing.policy': 'keep', 'duplicate.policy': 'keep', 'output.format': 'json'}


def ledger(text, values=FILTER, operations=('filter',)):
    s = state(text); s['resources'] = [TABLE]
    return checked(s, proposal(s, values, list(operations)))


@pytest.mark.parametrize('text,expected', [
    ('매주 월요일 오전 8시마다', {'schedule.frequency': 'weekly', 'schedule.weekday': 0, 'schedule.hour': 8, 'schedule.minute': 0}),
    ('매일 오전 9시에', {'schedule.frequency': 'daily', 'schedule.hour': 9, 'schedule.minute': 0}),
    ('한국 시간 매주 금요일 오후 6시에', {'schedule.frequency': 'weekly', 'schedule.weekday': 4, 'schedule.hour': 18,
                               'schedule.minute': 0, 'schedule.timezone': 'Asia/Seoul'}),
    ('매일 저녁 7시 반', {'schedule.frequency': 'daily', 'schedule.hour': 19, 'schedule.minute': 30}),
    ('매주 화요일 오후 2시 10분 KST', {'schedule.frequency': 'weekly', 'schedule.weekday': 1, 'schedule.hour': 14,
                                'schedule.minute': 10, 'schedule.timezone': 'Asia/Seoul'}),
    ('매일 9시에', {'schedule.frequency': 'daily'}),  # 09:00 or 21:00 is asked
    ('매주 월요일이나 금요일 오전 8시', {'schedule.frequency': 'weekly', 'schedule.weekday': 'conflict',
                                'schedule.hour': 8, 'schedule.minute': 0}),
])
def test_written_schedule_values_are_read(text, expected):
    assert {k: v for k, v, _ in schedule_literals(text)} == expected


@pytest.mark.parametrize('text', ['매년 1월 1일 오전 9시에', 'cron으로 실행', '정렬과 예약은 필요 없어', '매일 말고 한 번만 실행해줘',
                                  '3시간마다 비용 확인', '비용이 100만원 초과인 행만 저장해줘'])
def test_no_schedule_is_invented(text):
    assert schedule_literals(text) == []


def test_schedule_omitted_by_the_model_is_restored_with_fixed_seoul_timezone():
    result = ledger('매주 월요일 오전 8시마다' + TAIL)
    assert 'schedule' in result['operations']
    slots = {k: (v['status'], v['value']) for k, v in result['slots'].items() if k.startswith('schedule')}
    assert slots['schedule.frequency'] == ('specified', 'weekly') and slots['schedule.weekday'] == ('specified', 0)
    assert slots['schedule.hour'] == ('specified', 8) and slots['schedule.minute'] == ('specified', 0)
    assert slots['schedule.timezone'] == ('specified', 'Asia/Seoul')
    assert result['slots']['schedule.timezone']['extraction'] == 'application_setting'
    assert result['slots']['schedule.timezone']['evidence_ids'] == []
    assert result['decision'] == 'ready' and result['questions'] == [] and result['unresolved'] == []
    for key in ('schedule.frequency', 'schedule.weekday', 'schedule.hour', 'schedule.minute'):
        assert result['slots'][key]['evidence_ids'] == ['user:0:0']
        assert result['slots'][key]['evidence'][0]['quote'] == '매주 월요일 오전 8시마다' + TAIL


def test_fully_written_schedule_is_ready_with_its_schedule():
    result = ledger('한국 시간 매주 금요일 오후 6시에' + TAIL)
    assert result['decision'] == 'ready' and 'schedule' in result['operations']
    assert result['slots']['schedule.hour']['value'] == 18


def test_ready_draft_without_the_schedule_is_rejected():
    result = ledger('한국 시간 매주 금요일 오후 6시에' + TAIL)
    plan = {'filters': [{'field': '비용', 'operator': 'gte', 'value': 500000.0, 'compare_field': None, 'value_type': 'number'}],
            'sort': [], 'match': 'all', 'missing_policy': 'keep', 'duplicate_policy': 'keep', 'duplicate_field': None, 'output_format': 'json'}
    draft = {'execution_plan': plan, 'schedule': None, 'workflow': {'nodes': [
        {'id': 'out', 'tool': 'write_result', 'settings': {'format': 'json'}, 'bindings': []},
        {'id': 'f', 'tool': 'filter_sort', 'settings': {'execution_plan': plan}, 'bindings': []}]}}
    assert any('예약' in p for p in plan_issues(result, draft))


def test_monthly_request_is_supported_and_never_rewritten_as_weekly():
    sched = {'schedule.frequency': 'weekly', 'schedule.hour': '9', 'schedule.minute': '0', 'schedule.weekday': None, 'schedule.timezone': None}
    result = ledger('매월 1일 오전 9시에' + TAIL, {**FILTER, **sched}, ('schedule',))
    assert result['decision'] == 'ready' and not result['unsupported_actions']
    assert result['slots']['schedule.frequency']['value'] == 'monthly'
    assert result['slots']['schedule.monthday']['value'] == 1
    assert result['slots']['schedule.timezone']['value'] == 'Asia/Seoul'
    assert result['slots']['schedule.timezone']['extraction'] == 'application_setting'
    assert result['slots']['schedule.timezone']['evidence_ids'] == []
    assert result['questions'] == [] and result['unresolved'] == []
    assert result['slots']['schedule.hour']['value'] == 9
    assert result['slots']['schedule.minute']['value'] == 0
    assert 'schedule.weekday' not in result['slots']
    for key in ('schedule.frequency', 'schedule.monthday', 'schedule.hour', 'schedule.minute'):
        assert result['slots'][key]['evidence_ids'] == ['user:0:0']
        assert result['slots'][key]['evidence'][0]['quote'] == '매월 1일 오전 9시에' + TAIL
    assert not _grounded_enum('schedule.frequency', 'weekly', '매월 1일 오전 9시에')


def test_requests_without_a_schedule_are_unchanged():
    result = ledger('비용이 50만원 이상인 행만 JSON으로 저장해줘. 빈 비용은 그대로 둬. 중복은 유지해.')
    assert 'schedule' not in result['operations'] and result['decision'] == 'ready'
