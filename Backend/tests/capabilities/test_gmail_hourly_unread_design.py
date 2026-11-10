"""The bounded hourly unread recipe preserves native and verifier gates."""
from copy import deepcopy
import json

import pytest

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.capabilities.capability_judgement import assess_capability
from agent_builder.conditions.condition_check import make_condition_check
from agent_builder.conditions.condition_readiness import schedule_literals
from agent_builder.design.gmail_summary import (
    HOURLY_UNREAD_RECIPE, SAVED_SCHEDULE_RECIPE, UNREAD_QUERY,
    gmail_summary_plan, gmail_summary_reply, gmail_summary_request, requested_schedule,
)
from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import validate_plan
from infrastructure.persistence.storage import Storage
from verification.workflow.contracts import VerificationResult


REQUEST = '매 정시마다 gmail 에 온 메일을 확인하고 안읽은것들 요약 정리해줘'
SPEC = dict(frequency='hourly', timezone='Asia/Seoul', hour=None, minute=0,
            weekday=None, monthday=None, month_end=False)


def state(text=REQUEST):
    return dict(session_id='gmail-hourly-unread-design', messages=[dict(role='user', content=text)],
                resources=[], trace=[], node_library=[], required_node_refs=[])


class Forbidden:
    def invoke(self, *args, **kwargs):
        raise AssertionError('The registered recipe must not call a design model.')


@pytest.mark.parametrize('text', [
    REQUEST,
    '한국 시간 기준 매시간 정각에 Gmail에서 안 읽은 메일을 요약해주세요.',
    '매시 정시에 KST 지메일에서 받은 메일 중 읽지 않은 것들만 본문을 요약해서 정리해줘',
])
def test_closed_request_has_catalog_evidence_without_design_model_calls(text):
    current = state(text)
    request = gmail_summary_request(current)
    assert requested_schedule(text) == SPEC
    assert request['schedule'] == SPEC and request['query'] == UNREAD_QUERY and request['count'] == 100
    assessment = assess_capability(current, Forbidden())
    assert assessment['registered_workflow'] == HOURLY_UNREAD_RECIPE and assessment['model_calls'] == 0
    assert not assessment['allows_execution'] and not assessment['allows_generation']
    checked = make_condition_check(mode='enforce', transport=Forbidden())(current)
    assert not checked['condition_enforced'] and checked['condition_route'] == 'conversation'
    literals = {key:value for key,value,_ in schedule_literals(text)}
    assert literals['schedule.frequency'] == 'hourly' and literals['schedule.minute'] == 0


@pytest.mark.parametrize('text', [
    REQUEST + ' 그리고 읽음으로 표시해줘', REQUEST + ' 그리고 보관해줘',
    REQUEST + ' 그리고 메일로 보내줘', REQUEST + ' 그리고 Slack으로 보내줘',
    REQUEST + ' 그리고 마감일을 추출해줘', REQUEST + ' 엑셀로 저장해줘',
    REQUEST.replace('매 정시마다', '매시간 15분마다'),
    REQUEST.replace('매 정시마다', '매월 1일 오전 9시에'),
    REQUEST.replace('매 정시마다', '매일 오전 10시에'),
    REQUEST.replace('안읽은것들', '중요한 안읽은것들'),
    REQUEST.replace('안읽은것들', '모든 안읽은것들'),
    REQUEST.replace('gmail', 'outlook'), '미국 시간 ' + REQUEST,
])
def test_other_schedules_filters_mutations_delivery_and_extra_work_use_the_planner(text):
    assert gmail_summary_request(state(text)) is None
    assert gmail_summary_reply(state(text)) is None


def test_hourly_graph_explains_unread_scope_and_passes_native_validation_and_coverage():
    current = state()
    result = gmail_summary_reply(current)
    draft = result['draft']
    mail, summary = draft['workflow']['nodes']
    assert draft['schedule'] == SPEC and draft['report_destination'] == 'reports'
    assert draft['tool_ids'] == ['email.list', 'compose_answer'] and not draft['document_ids']
    assert mail['settings']['operation'] == 'read_messages'
    assert json.loads(mail['settings']['arguments_json']) == dict(query=UNREAD_QUERY, max_results=100)
    assert summary['bindings'] == [dict(input='documents', node_id='mail', output='documents')]
    assert draft['execution_plan']['missing_policy'] == draft['execution_plan']['duplicate_policy'] == 'keep'
    assert '한국 시간 매시간 정각' in draft['explanation']
    assert '보낸 메일과 임시보관함은 제외' in draft['explanation']
    assert '읽음 상태를 바꾸지 않아' in draft['explanation'] and '다음 실행에도 포함' in draft['explanation']
    assert '최대 100건' in draft['explanation'] and '일부 메일은 포함되지 않을 수' in draft['explanation']
    assert '별도로 켭니다' in draft['explanation'] and '전날' not in json.dumps(draft, ensure_ascii=False)
    full = {**current, **result}
    assert not validate_node(full)['issues'] and not coverage_report(full, draft)['issues']
    assert result['capability_assessment']['registered_workflow'] == HOURLY_UNREAD_RECIPE
    assert result['trace'][-1]['registered_workflow'] == HOURLY_UNREAD_RECIPE
    assert '안 읽은' in result['trace'][-1]['detail'] and '매시간 정각' in result['trace'][-1]['detail']
    with pytest.raises(ValueError, match='사용자 확인'):
        require_save_ready(full)


@pytest.mark.parametrize('count', [1, 100, 500])
def test_request_and_saved_count_edits_keep_unread_query_and_hourly_schedule(count):
    current = state(REQUEST.replace('요약 정리해줘', f'최대 {count}건 요약 정리해줘'))
    result = gmail_summary_reply(current)
    assert json.loads(result['draft']['workflow']['nodes'][0]['settings']['arguments_json']) == dict(query=UNREAD_QUERY, max_results=count)
    original = gmail_summary_reply(state())['draft']
    original['name'] = '팀의 매시 메일 요약'
    edited = {**state(f'최대 {count}건으로 바꿔줘'), 'base_agent_id':'saved', 'draft':original}
    updated = gmail_summary_reply(edited)
    draft = updated['draft']
    assert draft['name'] == original['name'] and draft['schedule'] == SPEC
    assert json.loads(draft['workflow']['nodes'][0]['settings']['arguments_json']) == dict(query=UNREAD_QUERY, max_results=count)
    assert str(count) in draft['workflow']['nodes'][1]['settings']['instructions']
    assert updated['trace'][-1]['registered_workflow'] == HOURLY_UNREAD_RECIPE
    assert '전날' not in draft['explanation']
    assert not coverage_report({**edited, **updated}, draft)['issues']
    assert not validate_node({**edited, **updated})['issues']


@pytest.mark.parametrize('count', [0, 501])
def test_counts_outside_contract_are_not_silently_clamped(count):
    assert gmail_summary_request(state(REQUEST.replace('요약 정리해줘', f'최대 {count}건 요약해줘'))) is None
    original = gmail_summary_reply(state())['draft']
    edited = {**state(f'최대 {count}건으로 바꿔줘'), 'base_agent_id':'saved', 'draft':original}
    assert gmail_summary_reply(edited) is None


@pytest.mark.parametrize('change', ['query', 'count', 'minute', 'schedule', 'destination', 'binding', 'summary'])
def test_coverage_rejects_missing_or_changed_unread_schedule_and_summary(change):
    current = state()
    result = gmail_summary_reply(current)
    altered = deepcopy(result['draft'])
    mail, summary = altered['workflow']['nodes']
    if change == 'query':
        mail['settings']['arguments_json'] = json.dumps(dict(query='is:unread', max_results=100))
    elif change == 'count':
        mail['settings']['arguments_json'] = json.dumps(dict(query=UNREAD_QUERY, max_results=10))
    elif change == 'minute':
        altered['schedule']['minute'] = 15
    elif change == 'schedule':
        altered['schedule'] = None
    elif change == 'destination':
        altered['report_destination'] = None
    elif change == 'binding':
        summary['bindings'][0]['output'] = 'result'
    else:
        altered['workflow']['nodes'].pop()
    assert coverage_report({**current, **result}, altered)['issues']


def test_requirement_query_is_grounded_and_prior_work_or_custom_summary_is_not_erased():
    current = state()
    plan = gmail_summary_plan(current)
    assert validate_plan(plan, current)['tasks'][0]['spec']['query'] == UNREAD_QUERY
    plan['tasks'][0]['spec']['query'] = '{{gmail.previous_day}}'
    with pytest.raises(ValueError, match='조회 조건'):
        validate_plan(plan, current)
    current['messages'].insert(0, dict(role='user', content='매출을 집계해줘'))
    assert gmail_summary_reply(current) is None
    original = gmail_summary_reply(state())['draft']
    original['workflow']['nodes'][1]['settings']['instructions'] = '메일에서 계약 해지 조건과 마감일만 추출해줘'
    assert gmail_summary_reply({**state('최대 200건으로 바꿔줘'), 'base_agent_id':'saved', 'draft':original}) is None


def test_existing_unread_schedule_count_edit_is_a_preservation_recipe_in_audit():
    original = gmail_summary_reply(state())['draft']
    original['schedule']['minute'] = 15
    edited = {**state('최대 200건으로 바꿔줘'), 'base_agent_id':'saved', 'draft':original}
    result = gmail_summary_reply(edited)
    assert result['draft']['schedule'] == original['schedule']
    assert '매시간 15분' in result['draft']['explanation']
    assert result['capability_assessment']['registered_workflow'] == SAVED_SCHEDULE_RECIPE
    assert result['trace'][-1]['registered_workflow'] == SAVED_SCHEDULE_RECIPE
    assert not coverage_report({**edited, **result}, result['draft'])['issues']


@pytest.mark.parametrize('route', ['continue_review', 'manual_review'])
def test_enforced_graph_skips_design_models_but_still_calls_and_obeys_the_verifier(tmp_path, monkeypatch, route):
    monkeypatch.setenv('A4B_STORAGE', 'sqlite')
    calls = []
    def verifier(payload):
        calls.append(payload)
        return VerificationResult(request_id=payload.request_id, model_output=None, findings=[], route=route,
                                  mode='fixture', model='test-verifier', latency_ms=1)
    store = Storage(tmp_path)
    forbidden = Forbidden()
    graph, connection = build_graph(store, conversation=lambda _:forbidden.invoke(),
        capability_mode='enforce', capability_transport=forbidden,
        condition_mode='enforce', condition_transport=forbidden,
        requirement_mode='enforce', requirement_transport=forbidden,
        verifier=verifier, verifier_mode='enforce')
    current = state()
    try:
        result = graph.invoke(current, {'configurable':{'thread_id':current['session_id']}})
        assert len(calls) == 1 and result['verification_enforced']
        assert result['verification_route'] == route and not result['confirmed'] and not store.agents()
        assert len(result['draft']['workflow']['nodes']) == 2
        assert result['requirement_coverage']['tasks'][0]['status'] == 'covered'
        if route == 'continue_review':
            assert result['status'] == 'ready' and result['__interrupt__'][0].value['kind'] == 'review'
        else:
            assert result['issues']
            with pytest.raises(ValueError):
                require_save_ready({**result, 'confirmed':True})
    finally:
        connection.close()
        store.close()
