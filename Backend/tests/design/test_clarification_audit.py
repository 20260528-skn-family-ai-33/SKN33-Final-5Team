"""Synthetic transport fixtures: orchestration regression, never model accuracy."""
import json
from copy import deepcopy

import pytest
from langgraph.types import Command

from agent_builder.capabilities.capability_check import make_capability_check
from agent_builder.design.graph import build_graph
from agent_builder.design.requirement_ledger import make_requirement_check
from infrastructure.persistence.storage import Storage
from tests.conditions.test_condition_readiness import state, Fake
from tests.design.test_requirement_coverage import seed, Extraction


def unresolved_packet(text, missing):
    return {'tasks': [{'description': '결과 파일 생성', 'source_message': 0,
                       'spec': {'kind': 'export', 'format': 'json'}}], 'unresolved': missing}


@pytest.mark.parametrize('text,missing', [
    ('매일 오전 9시에 이메일 보고서를 보내줘', ['보고서의 내용이나 원본 자료', '이메일 수신 주소']),
    ('고객 데이터를 분석해서 보고서를 만들어줘', ['분석할 고객 데이터', '보고서에서 확인할 분석 기준']),
    ('지난주 매출을 분석해서 이메일로 보내줘', ['매출 데이터의 위치', '이메일 수신 주소']),
])
def test_business_missing_values_interrupt_instead_of_failing(text, missing):
    current = state(text)
    result = make_requirement_check('fixture', Fake([unresolved_packet(text, missing)]), 'enforce')(current)
    assert result['status'] == 'needs_clarification'
    assert result['requirement_route'] == 'await_input'
    assert result['requirement_ledger']['unresolved'] == missing
    assert 1 <= len(result['pending_questions']) <= 2
    assert result['draft']['workflow'] is None
    assert not result['confirmed']


def test_extraction_receives_question_context_and_previous_conditions():
    current = seed()
    current['messages'] += [{'role': 'assistant', 'content': '어느 주소로 보낼까요?'},
                            {'role': 'user', 'content': '방금 말한 이메일 주소로 보내줘'}]
    current['pending_questions'] = [{'id': 'a' * 24, 'source': 'requirement',
                                    'key': 'requirement.recipient', 'text': '어느 주소로 보낼까요?'}]
    class Capture(Extraction):
        def invoke(self, messages):
            self.payload = json.loads(messages[1]['content'])
            return super().invoke(messages)
    transport = Capture(current['requirement_ledger'])
    make_requirement_check('fixture', transport, 'enforce')(current)
    assert transport.payload['conversation'] == current['messages']
    assert transport.payload['pending_questions'] == current['pending_questions']
    assert transport.payload['previous_requirements'] == current['requirement_ledger']


def test_requirement_model_error_does_not_expose_transport_payload():
    result = make_requirement_check('fixture', Fake([ValueError('token=synthetic-secret request=private')]), 'enforce')(state())
    assert result['status'] == 'validation_failed'
    assert 'synthetic-secret' not in json.dumps(result)
    assert 'private' not in json.dumps(result)


def test_missing_task_details_do_not_require_inventing_a_task():
    result = make_requirement_check('fixture', Fake([{'tasks': [], 'unresolved': ['분석할 기준은 무엇인가요?']}]), 'enforce')(state())
    assert result['status'] == 'needs_clarification'
    assert result['requirement_ledger']['tasks'] == []
    empty = make_requirement_check('fixture', Fake([{'tasks': [], 'unresolved': []}] * 2), 'enforce')(state())
    assert empty['status'] == 'validation_failed'


def test_pending_requirement_cannot_become_ready_from_cache():
    current = state('보고서를 만들어줘')
    check = make_requirement_check('fixture', Fake([unresolved_packet('', ['출력 형식'])]), 'enforce')
    first = check(current)
    second = check({**current, **first})
    assert second['requirement_route'] == 'await_input'
    assert second['pending_questions'][0]['id'] == first['pending_questions'][0]['id']
    from agent_builder.capabilities.capability_gate import require_ready
    with pytest.raises(ValueError, match='필수 업무 조건'):
        require_ready({**current, **first})


def test_server_timezone_reaches_persisted_graph_clock(tmp_path, monkeypatch):
    from datetime import datetime
    from tests.design.test_meaning_graph import graph_for
    from tests.design.test_request_meaning import Extractor, meaning, mail_arguments
    monkeypatch.setattr('agent_builder.design.temporal_context._utc_now',
                        lambda: datetime.fromisoformat('2026-11-10T01:00:00+00:00'))
    store = Storage(tmp_path)
    store.request_timezone = 'America/New_York'
    current = state('Gmail에서 어제 온 메일을 요약해줘')
    graph, conn = graph_for(store, Extractor(meaning(count=None, evidence=None, period='yesterday')))
    config = {'configurable': {'thread_id': current['session_id']}}
    try:
        result = graph.invoke(current, config)
        assert result['user_timezone'] == 'America/New_York'
        day = result['temporal_context']['relative_days']['yesterday']
        assert day['date'] == '2026-11-08'  # Seoul would resolve November 9
        assert f"after:{day['start_epoch']}" in mail_arguments(result)['query']
        assert graph.get_state(config).values['user_timezone'] == 'America/New_York'
    finally:
        conn.close(); store.close()


def test_last_week_uses_local_calendar_boundaries_and_dst():
    from agent_builder.design.temporal_context import temporal_context
    current = {**state('지난주 매출을 분석해줘'), 'user_timezone': 'America/New_York'}
    clock = temporal_context(current, now='2026-11-02T12:00:00Z')
    week = clock['relative_periods']['last_week']
    assert week['start'] == '2026-10-26T00:00:00-04:00'
    assert week['end'] == '2026-11-02T00:00:00-05:00'
    assert week['end_epoch'] - week['start_epoch'] == 169 * 3600
    old = deepcopy(clock)
    old['version'] = 1
    old.pop('relative_periods')
    for anchor in old['user_message_anchors']:
        anchor.pop('relative_periods', None)
    restored = temporal_context({**current, 'temporal_context': old}, now='2026-12-02T12:00:00Z')
    assert restored['reference_time'] == clock['reference_time']
    assert restored['relative_periods'] == clock['relative_periods']


def test_capability_question_controls_match_two_visible_questions():
    current = state('고객 데이터를 분석해서 보고서를 만들어줘')
    packet = {'decision': 'clarify', 'tool_ids': ['read_table', 'write_result'],
              'missing_conditions': ['자료 위치', '분석 기준', '출력 형식', '전달 대상'],
              'missing_actions': [], 'reason': '업무 조건 필요',
              'user_quotes': [current['messages'][0]['content']]}
    result = make_capability_check('fixture', Fake([packet]), 'enforce')(current)
    assert len(result['pending_questions']) == 2
    assert result['draft']['questions'] == [q['text'] for q in result['pending_questions']]
    assert len(result['capability_assessment']['missing_conditions']) == 4


def test_capability_uses_configured_provider_instead_of_forcing_ollama(monkeypatch):
    current = state('고객 데이터를 분석해서 보고서를 만들어줘')
    packet = {'decision': 'clarify', 'tool_ids': ['read_table'], 'missing_conditions': ['분석 기준'],
              'missing_actions': [], 'reason': '분석 기준 필요', 'user_quotes': [current['messages'][0]['content']]}
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'vllm')
    monkeypatch.setattr('agent_builder.design.requirement_ledger.requirement_transport', lambda *args: Fake([packet]))
    result = make_capability_check('fixture', mode='enforce')(current)
    assert result['capability_assessment']['decision'] == 'clarify'


def test_requirement_interrupt_restart_answer_and_save(tmp_path):
    current = seed()
    plan = deepcopy(current['requirement_ledger'])
    for key in ('requirement_ledger', 'requirement_fingerprint', 'requirement_digest'):
        current.pop(key)
    first_plan = deepcopy(plan)
    # The request already says JSON, so the open item must be one the user has not settled
    # (an already-answered format is screened out and never asked again).
    first_plan['unresolved'] = ['결과 파일 이름']
    called = []
    def planner(s):
        called.append(s)
        return {'draft': current['draft'], 'confirmed': False, 'saved_agent_id': None}
    def build(store, transport):
        return build_graph(store, conversation=planner, capability_mode='off', condition_mode='off',
                           requirement_mode='enforce', requirement_transport=transport, verifier_mode='off')
    store = Storage(tmp_path)
    graph, conn = build(store, Extraction(first_plan))
    config = {'configurable': {'thread_id': current['session_id']}}
    try:
        result = graph.invoke(current, config)
        assert result['status'] == 'needs_clarification'
        assert result['__interrupt__'][0].value['kind'] == 'clarification'
        assert not called and not store.agents()
        question_id = result['pending_questions'][0]['id']
    finally:
        conn.close(); store.close()
    store = Storage(tmp_path)
    graph, conn = build(store, Extraction(plan))
    try:
        result = graph.invoke(Command(resume={'type': 'message', 'content': 'result.json으로 해줘', 'question_id': question_id}), config)
        assert result['status'] == 'ready'
        assert result['pending_questions'] == []
        assert result['requirement_ledger']['unresolved'] == []
        assert called[-1]['messages'][-1]['content'] == 'result.json으로 해줘'
        assert result['draft']['execution_plan']['filters'][0]['value'] == 20
        assert result['__interrupt__'][0].value['kind'] == 'review'
        saved = graph.invoke(Command(resume={'type': 'confirm'}), config)
        assert saved['status'] == 'saved' and len(store.agents()) == 1
        store.add_asset({'id': 'synthetic-table', 'name': 'synthetic.json', 'kind': 'table', 'columns': ['qty']},
                        [{'qty': 1}, {'qty': 3}, {'qty': 2}])
        from service.worker import handle
        executed = handle({'directory': str(tmp_path), 'action': 'test', 'id': saved['saved_agent_id'],
                           'content': '', 'file_ids': ['synthetic-table']})
        assert executed['status'] == 'succeeded'
        assert executed['rows'] == [{'qty': 3, 'amount': 30}, {'qty': 2, 'amount': 20}]
    finally:
        conn.close(); store.close()
