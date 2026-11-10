"""The daily Gmail recipe keeps its time window through request validation."""
from copy import deepcopy
import json

import pytest

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.capabilities.capability_judgement import assess_capability
from agent_builder.conditions.condition_check import make_condition_check
from agent_builder.design.gmail_summary import (
    PREVIOUS_DAY_QUERY, gmail_summary_plan, gmail_summary_reply,
    gmail_summary_request, requested_count, requested_schedule,
)
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import validate_plan


REQUEST = '매일 오전 9시에 전날 온 Gmail 메일을 요약해줘'
SPEC = dict(frequency='daily', timezone='Asia/Seoul', hour=9, minute=0, weekday=None, monthday=None, month_end=False)


def state(text=REQUEST):
    return dict(session_id='gmail-schedule-design', messages=[dict(role='user', content=text)],
                resources=[], trace=[], node_library=[], required_node_refs=[])


class Forbidden:
    def invoke(self, *args, **kwargs):
        raise AssertionError('A registered Gmail recipe must not need a design model call.')


@pytest.mark.parametrize('text', [
    REQUEST,
    '한국 시간 기준 매일 오전 9시에 Gmail에서 전날 받은 메일 내용을 요약해주세요.',
    '매일 오전9시 KST 전날 지메일 메일들을 요약해서 정리해줘',
    '매일 오전 9시에 그 전날 온 Gmail 메일 요약',
])
def test_daily_previous_day_request_is_bounded_and_uses_registered_capability(text):
    current = state(text)
    assert requested_schedule(text) == SPEC
    assert requested_count(text) is None
    request = gmail_summary_request(current)
    assert request['schedule'] == SPEC
    assert request['count'] == 100 and request['query'] == PREVIOUS_DAY_QUERY
    assessment = assess_capability(current, Forbidden())
    assert assessment['decision'] == 'existing_tools'
    assert assessment['registered_workflow'] == 'gmail_previous_day_daily_summary.v1'
    assert not assessment['allows_execution'] and not assessment['allows_generation']
    assert assessment['model_calls'] == 0


@pytest.mark.parametrize('text', [
    REQUEST + ' 그리고 메일로 보내줘', REQUEST + ' 그리고 Slack으로 보내줘',
    REQUEST.replace('오전 9시', '오후 9시'), REQUEST.replace('오전 9시', '9시'),
    REQUEST.replace('매일', '매주 월요일'), REQUEST.replace('전날', '지난주'),
    REQUEST.replace('전날 온', '전날 온 모든'), REQUEST.replace('Gmail', 'Outlook'),
    REQUEST.replace('전날 온', '전날 온 안 읽은'),
    '미국 시간 ' + REQUEST, REQUEST.replace('오전 9시', '오전 9시 30분'),
])
def test_extra_tasks_filters_or_unsupported_times_stay_with_the_planner(text):
    assert gmail_summary_request(state(text)) is None
    assert gmail_summary_reply(state(text)) is None


def test_daily_recipe_does_not_erase_earlier_work_or_take_attached_files():
    current = state()
    current['messages'].insert(0, dict(role='user', content='월간 매출도 집계해줘'))
    assert gmail_summary_request(current) is None
    current = state()
    current['resources'] = [dict(id='table', kind='table')]
    assert gmail_summary_request(current) is None


def test_daily_recipe_bypasses_only_the_table_condition_checker():
    result = make_condition_check(mode='enforce', transport=Forbidden())(state())
    assert not result['condition_enforced']
    assert result['condition_route'] == 'conversation'
    assert result['condition_ledger'] is None


def test_daily_draft_has_relative_query_saved_result_and_no_automatic_activation():
    current = state()
    result = gmail_summary_reply(current)
    draft = result['draft']
    assert draft['schedule'] == SPEC
    assert draft['report_destination'] == 'reports'
    assert draft['document_ids'] == []
    assert draft['tool_ids'] == ['email.list', 'compose_answer']
    assert '최대 100건' in draft['explanation']
    assert '일부 메일은 포함되지 않을 수' in draft['explanation']
    assert '별도로 켭니다' in draft['explanation']
    mail, summary = draft['workflow']['nodes']
    assert json.loads(mail['settings']['arguments_json']) == dict(query=PREVIOUS_DAY_QUERY, max_results=100)
    assert summary['bindings'] == [dict(input='documents', node_id='mail', output='documents')]
    assert not result['confirmed']
    full = {**current, **result}
    assert not coverage_report(full, draft)['issues']
    assert not validate_node(full)['issues']
    with pytest.raises(ValueError, match='사용자 확인'):
        require_save_ready(full)
    require_save_ready({**full, 'confirmed': True})


@pytest.mark.parametrize('change', ['query', 'count', 'time', 'timezone', 'schedule', 'destination', 'binding', 'instructions'])
def test_daily_requirement_rejects_dropped_or_changed_period_schedule_and_content(change):
    current = state()
    result = gmail_summary_reply(current)
    draft = deepcopy(result['draft'])
    mail, summary = draft['workflow']['nodes']
    if change == 'query':
        mail['settings']['arguments_json'] = json.dumps(dict(max_results=100, query='after:2026/11/09 before:2026/11/10'))
    elif change == 'count':
        mail['settings']['arguments_json'] = json.dumps(dict(max_results=1, query=PREVIOUS_DAY_QUERY))
    elif change == 'time':
        draft['schedule']['hour'] = 21
    elif change == 'timezone':
        draft['schedule']['timezone'] = 'America/Los_Angeles'
    elif change == 'schedule':
        draft['schedule'] = None
    elif change == 'destination':
        draft['report_destination'] = None
    elif change == 'binding':
        summary['bindings'][0]['output'] = 'result'
    else:
        summary['settings']['instructions'] = '메일 제목만 그대로 보여줘'
    assert coverage_report({**current, **result}, draft)['issues']


def test_daily_plan_cannot_claim_a_different_time_or_recent_mail_requirement():
    current = state()
    plan = gmail_summary_plan(current)
    assert validate_plan(plan, current)['tasks'][0]['spec']['kind'] == 'gmail_scheduled_summary'
    changed = deepcopy(plan)
    changed['tasks'][0]['spec']['schedule']['hour'] = 10
    with pytest.raises(ValueError, match='예약 시각'):
        validate_plan(changed, current)
    changed = deepcopy(plan)
    spec = changed['tasks'][0]['spec']
    spec['kind'] = 'gmail_summary'
    spec.pop('schedule')
    spec.pop('query')
    with pytest.raises(ValueError, match='예약 요구사항'):
        validate_plan(changed, current)


def test_existing_recent_mail_recipe_keeps_its_query_and_schedule_contract():
    current = state('Gmail에서 최근 5개 메일을 요약해줘')
    result = gmail_summary_reply(current)
    assert result['draft']['schedule'] is None
    assert result['draft']['report_destination'] is None
    assert json.loads(result['draft']['workflow']['nodes'][0]['settings']['arguments_json']) == dict(max_results=5)
    assert result['requirement_ledger']['tasks'][0]['spec']['kind'] == 'gmail_summary'
    assert not coverage_report({**current, **result}, result['draft'])['issues']


@pytest.mark.parametrize('count', [1, 100, 200, 500])
def test_daily_request_may_choose_a_bounded_mail_count(count):
    current = state(f'매일 오전 9시에 전날 온 Gmail 메일을 최대 {count}건 요약해줘')
    result = gmail_summary_reply(current)
    assert result is not None
    draft = result['draft']
    assert json.loads(draft['workflow']['nodes'][0]['settings']['arguments_json'])['max_results'] == count
    assert result['requirement_ledger']['tasks'][0]['spec']['max_results'] == count
    assert not coverage_report({**current, **result}, draft)['issues']


@pytest.mark.parametrize('count', [1, 11, 100, 500])
def test_recent_mail_recipe_supports_the_registered_500_mail_limit(count):
    assert requested_count(f'Gmail에서 최근 {count}개 메일을 요약해줘') == count


@pytest.mark.parametrize('prompt', [
    '매일 오전9시에 전날 Gmail 메일을 최대 501건 요약해줘',
    '매일 오전9시에 전날 Gmail 메일을 최대 0건 요약해줘',
    'Gmail에서 최근 501개 메일을 요약해줘',
])
def test_overlarge_or_zero_limits_are_not_silently_clamped(prompt):
    assert gmail_summary_request(state(prompt)) is None


@pytest.mark.parametrize('change', ['최대 200건으로 바꿔줘', '메일 조회 한도를 500건으로 변경해주세요'])
def test_saved_gmail_count_change_preserves_period_schedule_and_name(change):
    current = state()
    original = gmail_summary_reply(current)['draft']
    original['name'] = '팀 메일 아침 보고'
    edited = {**state(change), 'base_agent_id': 'existing', 'draft': original}
    result = gmail_summary_reply(edited)
    assert result is not None
    assert result['draft']['name'] == original['name']
    assert result['draft']['schedule'] == original['schedule']
    count = 200 if '200' in change else 500
    assert json.loads(result['draft']['workflow']['nodes'][0]['settings']['arguments_json']) == dict(query=PREVIOUS_DAY_QUERY, max_results=count)
    assert str(count) in result['draft']['workflow']['nodes'][1]['settings']['instructions']
    assert not coverage_report({**edited, **result}, result['draft'])['issues']


def test_count_edits_cannot_replace_a_workflow_with_extra_independent_work():
    original = gmail_summary_reply(state())['draft']
    original['tool_ids'].append('finish')
    current = {**state('최대 200건으로 바꿔줘'), 'base_agent_id': 'existing', 'draft': original}
    assert gmail_summary_reply(current) is None


@pytest.mark.parametrize('recurrence', [
    dict(frequency='hourly',hour=None,minute=15),
    dict(frequency='monthly',monthday=None,month_end=True),
])
def test_count_only_edit_does_not_relabel_a_saved_hourly_or_monthly_schedule_as_daily(recurrence):
    original = gmail_summary_reply(state())['draft']
    original['schedule'].update(recurrence)
    edited = {**state('최대 200건으로 바꿔줘'), 'base_agent_id':'existing', 'draft':original}
    result = gmail_summary_reply(edited)
    assert result['draft']['schedule'] == original['schedule']
    label = '매시간 15분' if recurrence['frequency']=='hourly' else '매월 말일'
    assert label in result['draft']['explanation']
    assert '매일 09:00' not in result['draft']['explanation']
    assert not coverage_report({**edited, **result}, result['draft'])['issues']


def test_non_file_schedule_does_not_require_a_table_or_a_file_export_step():
    current = state('한국 시간 매일 오전9시에 상태 확인 완료 문구를 기록해줘')
    draft = gmail_summary_reply(state())['draft']
    draft.update(name='매일 상태 확인', purpose=current['messages'][0]['content'], instructions='상태 확인 완료 문구를 기록합니다.',
                 tool_ids=['finish'], requirements=[dict(description='매일 상태 확인', source_id='user', quote=current['messages'][0]['content'])])
    finish = {**draft['workflow']['nodes'][1], 'id': 'done', 'tool': 'finish', 'bindings': []}
    finish['settings']['instructions'] = '상태 확인 완료'
    draft['workflow'] = dict(entry='done', nodes=[finish], edges=[])
    assert not validate_node({**current, 'draft': draft})['issues']


def test_registered_non_table_work_is_not_asked_for_a_table_by_condition_checker():
    current = state('한국 시간 매일 오전9시에 상태 확인 완료 문구를 기록해줘')
    current['capability_assessment'] = dict(decision='existing_tools', tool_ids=['finish'])
    result = make_condition_check(mode='enforce', transport=Forbidden())(current)
    assert not result['condition_enforced']
    assert result['condition_ledger'] is None and not result['pending_questions']


def test_real_missing_file_and_interactive_inputs_still_block_scheduled_design():
    current = state()
    draft = gmail_summary_reply(current)['draft']
    draft['tool_ids'] = ['read_document', 'compose_answer']
    draft['workflow']['nodes'][0].update(tool='read_document')
    draft['workflow']['nodes'][0]['settings'].update(operation=None, arguments_json=None)
    report = validate_node({**current, 'draft': draft})
    assert any('문서' in issue or '자료' in issue for issue in report['issues'])
    draft['workflow']['nodes'][0].update(tool='ask_user')
    draft['tool_ids'][0] = 'ask_user'
    report = validate_node({**current, 'draft': draft})
    assert any('응답 대기' in issue for issue in report['issues'])


def test_confirmed_recipe_saves_a_paused_relative_schedule_without_a_file(tmp_path, monkeypatch):
    from infrastructure.persistence.storage import Storage
    from workflow_engine.scheduling.schedules import Schedules
    monkeypatch.setenv('A4B_STORAGE', 'sqlite')
    monkeypatch.delenv('EASYFLOW_PUBLIC_URL', raising=False)
    current = state()
    result = gmail_summary_reply(current)
    confirmed = {**current, **result, 'confirmed': True, 'model': 'test-model', 'credential_id': 'test-credential'}
    store = Storage(tmp_path)
    try:
        identifier = store.save_agent(confirmed)
        saved = store.agent(identifier)
        schedule = Schedules(store).get(identifier)
        assert saved['config']['schedule'] == SPEC
        assert schedule['spec'] == SPEC and not schedule['active']
        assert Schedules(store).source(identifier) is None
        assert json.loads(saved['config']['workflow']['nodes'][0]['settings']['arguments_json']) == dict(query=PREVIOUS_DAY_QUERY, max_results=100)
    finally:
        store.close()
