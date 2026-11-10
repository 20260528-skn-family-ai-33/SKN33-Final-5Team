"""Hourly/monthly conditions remain grounded and do not become daily/weekly."""
import pytest

from agent_builder.conditions.condition_readiness import schedule_literals, source_catalog, validate_conditions
from agent_builder.capabilities.capability_judgement import _native_schedule_label


def state(text):
    return dict(session_id='recurrence-conditions', messages=[dict(role='user', content=text)],
                resources=[dict(id='table', kind='table', name='sample.csv', columns=[], profile={})])


def ledger(text):
    current = state(text)
    return validate_conditions(dict(operations=['schedule'], conditions=[], unsupported_actions=[]), current, source_catalog(current)[0])


@pytest.mark.parametrize('text,expected', [
    ('한국 시간 매시간 정각에 실행', {'schedule.frequency':'hourly', 'schedule.minute':0, 'schedule.timezone':'Asia/Seoul'}),
    ('매시간 15분 KST', {'schedule.frequency':'hourly', 'schedule.minute':15, 'schedule.timezone':'Asia/Seoul'}),
    ('매시 30분 UTC', {'schedule.frequency':'hourly', 'schedule.minute':30, 'schedule.timezone':'UTC'}),
    ('한국 시간 매월 31일 오전 9시', {'schedule.frequency':'monthly', 'schedule.monthday':31, 'schedule.month_end':False, 'schedule.hour':9, 'schedule.minute':0, 'schedule.timezone':'Asia/Seoul'}),
    ('한국 시간 매달 말일 오후 6시', {'schedule.frequency':'monthly', 'schedule.month_end':True, 'schedule.hour':18, 'schedule.minute':0, 'schedule.timezone':'Asia/Seoul'}),
    ('매월 마지막 날 오후 1시 UTC', {'schedule.frequency':'monthly', 'schedule.month_end':True, 'schedule.hour':13, 'schedule.minute':0, 'schedule.timezone':'UTC'}),
])
def test_literal_frequency_slots_match_written_hourly_and_monthly_requests(text, expected):
    assert {key:value for key,value,_ in schedule_literals(text)} == expected
    result = ledger(text)
    assert result['decision'] == 'ready' and not result['unsupported_actions']
    assert {key:slot['value'] for key,slot in result['slots'].items() if slot['status']=='specified'} == expected


def test_hourly_asks_for_minute_but_never_for_an_hour():
    result = ledger('한국 시간 매시간 실행해줘')
    assert 'schedule.minute' in result['unresolved']
    assert 'schedule.hour' not in result['unresolved']


def test_monthly_asks_for_a_day_and_does_not_invent_month_end():
    result = ledger('한국 시간 매월 오전9시에 실행해줘')
    assert 'schedule.monthday' in result['unresolved']
    assert result['decision'] == 'clarify'


def test_a_negated_monthly_mention_does_not_override_daily():
    result = ledger('매월은 필요 없어. 한국 시간 매일 오전9시에 실행해줘')
    assert result['decision'] == 'ready'
    assert result['slots']['schedule.frequency']['value'] == 'daily'


@pytest.mark.parametrize('text', ['한국 시간 매년 1월1일 오전9시', 'cron으로 매일 실행'])
def test_yearly_and_cron_remain_outside_supported_recurrences(text):
    assert not schedule_literals(text)
    assert ledger(text)['unsupported_actions']


@pytest.mark.parametrize('label', ['매시간 정각 예약', 'hourly schedule', '매월 15일 오전9시 예약', '매달 말일 예약', 'monthly schedule'])
def test_supported_app_recurrence_labels_are_not_missing_tool_claims(label):
    assert _native_schedule_label(label)


@pytest.mark.parametrize('label', ['매월 Slack 발송', '매시간 외부자료 수집', '매년 예약', 'cron schedule'])
def test_other_actions_are_not_silently_treated_as_native_schedules(label):
    assert not _native_schedule_label(label)


@pytest.mark.parametrize('text', [
    '한국 시간 매시간 정각에 실행', '한국 시간 매일 오전9시에 실행',
    '한국 시간 매주 월요일 오전9시에 실행', '한국 시간 매월31일 오전9시에 실행',
    '한국 시간 매월말일 오전9시에 실행',
])
def test_native_schedule_requirement_checks_actual_metadata_without_a_fake_tool(text):
    from copy import deepcopy
    from agent_builder.design.requirement_ledger import grounded_schedule, ledger_fingerprint, plan_digest, validate_plan
    from agent_builder.design.requirement_coverage import coverage_report
    current = state(text)
    spec = grounded_schedule(current)
    plan = dict(tasks=[dict(id='r1', source_message=0, quote=text, description='예약 실행', depends_on=[],
                            spec=dict(kind='schedule', schedule=spec))], unresolved=[])
    plan = validate_plan(plan, current)
    full = {**current, 'requirement_required':True, 'requirement_ledger':plan,
            'requirement_fingerprint':ledger_fingerprint(current), 'requirement_digest':plan_digest(plan)}
    draft = dict(schedule=spec, report_destination='reports',
                 workflow=dict(entry='done', nodes=[dict(id='done', tool='finish', settings=dict(operation=None), bindings=[])], edges=[]))
    assert not coverage_report(full, draft)['issues']
    changed = deepcopy(draft)
    changed['schedule']['minute'] = (spec['minute'] + 1) % 60
    assert coverage_report(full, changed)['issues']


def test_unwritten_schedule_values_cannot_be_invented_by_requirement_extraction():
    from agent_builder.design.requirement_ledger import grounded_schedule
    with pytest.raises(ValueError, match='충분하지'):
        grounded_schedule(state('매월 오전9시에 실행해줘'))


def test_changing_a_monthly_day_to_month_end_removes_the_obsolete_fixed_day():
    from agent_builder.design.requirement_ledger import grounded_schedule
    current = state('한국 시간 매월15일 오전9시에 실행해줘')
    current['messages'].append(dict(role='user',content='말일로 바꿔줘'))
    spec = grounded_schedule(current)
    assert spec['month_end'] is True and spec['monthday'] is None
    assert spec['hour'] == 9 and spec['timezone'] == 'Asia/Seoul'


def test_an_explicit_iana_zone_with_an_offset_is_preserved():
    values = {key:value for key,value,_ in schedule_literals('매시간 정각 Etc/GMT+9')}
    assert values['schedule.timezone'] == 'Etc/GMT+9'
