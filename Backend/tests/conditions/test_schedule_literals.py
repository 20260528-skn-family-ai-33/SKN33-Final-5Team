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


@pytest.mark.parametrize('text', ['매월 1일 오전 9시에', '매달 말일 저장', '정렬과 예약은 필요 없어', '매일 말고 한 번만 실행해줘',
                                  '3시간마다 비용 확인', '비용이 100만원 초과인 행만 저장해줘'])
def test_no_schedule_is_invented(text):
    assert schedule_literals(text) == []


def test_schedule_omitted_by_the_model_is_restored_and_only_the_unwritten_value_is_asked():
    result = ledger('매주 월요일 오전 8시마다' + TAIL)
    assert 'schedule' in result['operations']
    slots = {k: (v['status'], v['value']) for k, v in result['slots'].items() if k.startswith('schedule')}
    assert slots['schedule.frequency'] == ('specified', 'weekly') and slots['schedule.weekday'] == ('specified', 0)
    assert slots['schedule.hour'] == ('specified', 8) and slots['schedule.minute'] == ('specified', 0)
    assert result['decision'] == 'clarify' and [q['key'] for q in result['questions']] == ['schedule.timezone']


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


def test_monthly_request_is_unsupported_and_never_weekly():
    sched = {'schedule.frequency': 'weekly', 'schedule.hour': '9', 'schedule.minute': '0', 'schedule.weekday': None, 'schedule.timezone': None}
    result = ledger('매월 1일 오전 9시에' + TAIL, {**FILTER, **sched}, ('schedule',))
    assert result['decision'] == 'unsupported' and '월간 예약' in result['unsupported_actions']
    assert result['slots']['schedule.frequency']['status'] != 'specified'
    assert not _grounded_enum('schedule.frequency', 'weekly', '매월 1일 오전 9시에')


def test_requests_without_a_schedule_are_unchanged():
    result = ledger('비용이 50만원 이상인 행만 JSON으로 저장해줘. 빈 비용은 그대로 둬. 중복은 유지해.')
    assert 'schedule' not in result['operations'] and result['decision'] == 'ready'


@pytest.mark.parametrize('edit, hour', [
    ('오후 6시로 바꿔줘', 18),
    ('오후 6시로 변경해주세요. 나머지는 그대로.', 18),
    ('6시로 바꿔줘', None),
    ('오후 6시로 변경 바랍니다.', 18),
    ('오전 9시에서 오후 6시로 옮겨줘.', 18),
    ('예약 시각을 저녁 식사 후로 바꿔줘.', None),
])
def test_followup_changes_hour_and_preserves_daily_frequency(edit, hour):
    s = state('한국 시간 매일 오전 9시에' + TAIL)
    s['resources'] = [TABLE]
    s['condition_ledger'] = checked(s, proposal(s, FILTER))
    s['messages'].append({'role': 'user', 'content': edit})
    result = checked(s, proposal(s, {}))
    assert result['decision'] == ('ready' if hour is not None else 'clarify')
    assert result['slots']['schedule.hour']['value'] == hour
    assert result['slots']['schedule.frequency']['value'] == 'daily'


def test_utc_plus_nine_is_a_fixed_offset():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    result = ledger('UTC+9 기준 매일 오전 9시에' + TAIL)
    assert result['decision'] == 'ready'
    zone = ZoneInfo(result['slots']['schedule.timezone']['value'])
    assert datetime(2026, 11, 4, 9, tzinfo=zone).utcoffset() == timedelta(hours=9)


@pytest.mark.parametrize('action', ['월간 예약', '매월 예약 실행', '월간 자동 실행'])
def test_prohibiting_monthly_schedule_does_not_block_filter(action):
    s = state('비용이 10원 이상인 행만 CSV로 저장해줘. 매월 예약은 하지 마')
    raw = proposal(s, {**FILTER, 'filter.value': '10', 'output.format': 'csv',
                       'missing.policy': None, 'duplicate.policy': None})
    raw['conditions'] = [c for c in raw['conditions'] if c['value'] is not None]
    raw['unsupported_actions'] = [action]
    result = checked(s, raw)
    assert result['decision'] == 'ready'
    assert result['operations'] == ['filter']



def test_malformed_utc_offset_is_not_partially_accepted():
    result = ledger('UTC+9:5 기준 매일 오전 9시에' + TAIL)
    assert result['decision'] == 'clarify'
    assert result['slots']['schedule.timezone']['value'] is None


@pytest.mark.parametrize('edit, zone', [
    ('시간대를 UTC+9로 바꿔줘.', 'Etc/GMT-9'),
    ('한국 시간으로 바꿔줘.', 'Asia/Seoul'),
    ('시간대를 UTC+9:5로 바꿔줘.', None),
    ('시간대를 모르는 지역으로 바꿔줘.', None),
])
def test_timezone_followup_preserves_hour(edit, zone):
    s = state('UTC 기준 매일 오전 9시에' + TAIL)
    s['resources'] = [TABLE]
    s['condition_ledger'] = checked(s, proposal(s, FILTER))
    s['messages'].append({'role': 'user', 'content': edit})
    result = checked(s, proposal(s, {}))
    assert result['decision'] == ('ready' if zone else 'clarify')
    assert result['slots']['schedule.timezone']['value'] == zone
    assert result['slots']['schedule.hour']['value'] == 9
