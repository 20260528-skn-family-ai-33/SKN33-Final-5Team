"""Meaning extraction drives the real graph; these fixtures do not test an LLM."""
from datetime import datetime
import json

import pytest
from langgraph.types import Command

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.design.gmail_summary import gmail_summary_reply
from agent_builder.design.graph import build_graph
from agent_builder.design.request_meaning import sealed_meaning
from infrastructure.persistence.storage import Storage
from tests.conditions.test_condition_readiness import state
from tests.design.test_request_meaning import Extractor, meaning, mail_arguments


class Forbidden:
    def invoke(self, *args, **kwargs):
        raise AssertionError('A grounded meaning must compile without another design model call.')


def graph_for(store, transport):
    forbidden = Forbidden()
    return build_graph(store, conversation=lambda _: forbidden.invoke(),
                       meaning_transport=transport,
                       capability_mode='enforce', capability_transport=forbidden,
                       condition_mode='enforce', condition_transport=forbidden,
                       requirement_mode='enforce', requirement_transport=forbidden,
                       verifier_mode='off')


@pytest.mark.parametrize(('text', 'evidence'), [
    ('내 gmail 에서 최근 5개 메일을 요약 정리해줘', '5개'),
    ('제 Gmail에서 최신 메일 5개를 요약해 주세요.', '5개'),
    ('메일 다섯 건만 최신순으로 골라 Gmail에서 핵심 내용을 정리해 줘.', '다섯 건'),
    ('Gmail 메일 중 최신 5통의 내용을 간단히 요약 부탁해.', '5통'),
])
def test_equivalent_requests_reach_same_review_and_persist_on_confirmation(tmp_path, text, evidence):
    store = Storage(tmp_path)
    current = state(text)
    transport = Extractor(meaning(evidence=evidence))
    graph, connection = graph_for(store, transport)
    config = {'configurable': {'thread_id': current['session_id']}}
    try:
        result = graph.invoke(current, config)
        assert len(transport.messages) == 2  # extraction, independent fidelity review
        assert result['status'] == 'ready' and result['issues'] == []
        assert result['__interrupt__'][0].value['kind'] == 'review'
        baseline = gmail_summary_reply(state('gmail 에서 최근 5개 메일을 요약 정리해줘'))
        assert result['draft']['workflow'] == baseline['draft']['workflow']
        assert result['draft']['questions'] == [] and not store.agents()
        assert result['requirement_coverage']['tasks'][0]['status'] == 'covered'
        assert sealed_meaning(result) is not None
        with pytest.raises(ValueError, match='사용자 확인'):
            require_save_ready(result)
        # The seal and per-turn clock survive an actual persisted checkpoint.
        connection.close()
        store.close()
        store = Storage(tmp_path)
        graph, connection = graph_for(store, Forbidden())
        snapshot = graph.get_state(config).values
        assert snapshot['temporal_context'] == result['temporal_context']
        assert snapshot['request_meaning_review'] == result['request_meaning_review']
        assert sealed_meaning(snapshot) == sealed_meaning(result)
        saved = graph.invoke(Command(resume={'type': 'confirm'}), config)
        assert saved['status'] == 'saved'
        assert store.agent(saved['saved_agent_id'])['config']['workflow'] == result['draft']['workflow']
    finally:
        connection.close()
        store.close()


@pytest.mark.parametrize('text', [
    'gmail 에서 어제 온 메일 요약 정리해줘',
    '어제 받은 메일을 내 Gmail에서 찾아서 요약해 주세요.',
])
def test_yesterday_does_not_ask_for_a_date_and_saves_fixed_bounds(tmp_path, monkeypatch, text):
    monkeypatch.setattr('agent_builder.design.temporal_context._utc_now',
                        lambda: datetime.fromisoformat('2026-11-09T16:00:00+00:00'))
    store = Storage(tmp_path)
    current = {**state(text), 'user_timezone': 'Asia/Seoul'}
    transport = Extractor(meaning(count=None, evidence=None, period='yesterday'))
    graph, connection = graph_for(store, transport)
    config = {'configurable': {'thread_id': current['session_id']}}
    try:
        result = graph.invoke(current, config)
        assert result['status'] == 'ready' and result['draft']['questions'] == []
        day = result['temporal_context']['user_message_anchors'][0]['relative_days']['yesterday']
        expected = f"after:{day['start_epoch']} before:{day['end_epoch']} -in:sent -in:drafts"
        assert day['date'] == '2026-11-09'
        assert mail_arguments(result) == {'max_results': 100, 'query': expected}
        assert '2026-11-09' in result['messages'][-1]['content']
        monkeypatch.setattr('agent_builder.design.temporal_context._utc_now',
                            lambda: datetime.fromisoformat('2026-11-10T16:00:00+00:00'))
        saved = graph.invoke(Command(resume={'type': 'confirm'}), config)
        assert saved['status'] == 'saved'
        assert mail_arguments(saved)['query'] == expected
        assert saved['temporal_context'] == result['temporal_context']
    finally:
        connection.close()
        store.close()


def test_next_day_count_followup_preserves_original_day_and_reseals_meaning(tmp_path, monkeypatch):
    monkeypatch.setattr('agent_builder.design.temporal_context._utc_now',
                        lambda: datetime.fromisoformat('2026-11-09T16:00:00+00:00'))
    first_meaning = meaning(count=None, evidence=None, period='yesterday')
    changed_meaning = meaning(period='yesterday')
    changed_meaning['tasks'][0]['selection']['limit_source_message'] = 1
    transport = Extractor(first_meaning, changed_meaning)
    store = Storage(tmp_path)
    current = {**state('내 Gmail에서 어제 온 메일을 요약해줘'), 'user_timezone': 'Asia/Seoul'}
    graph, connection = graph_for(store, transport)
    config = {'configurable': {'thread_id': current['session_id']}}
    try:
        first = graph.invoke(current, config)
        assert first['status'] == 'ready'
        original_query = mail_arguments(first)['query']
        original_anchor = first['temporal_context']['user_message_anchors'][0]
        monkeypatch.setattr('agent_builder.design.temporal_context._utc_now',
                            lambda: datetime.fromisoformat('2026-11-10T16:00:00+00:00'))
        second = graph.invoke(Command(resume={'type': 'message', 'content': '최대 5개만 요약해줘'}), config)
        assert second['status'] == 'ready' and second['draft']['questions'] == []
        assert len(transport.messages) == 4  # both user turns are extracted and reviewed
        assert mail_arguments(second) == {'max_results': 5, 'query': original_query}
        assert second['temporal_context']['user_message_anchors'][0] == original_anchor
        assert second['temporal_context']['user_message_anchors'][1]['relative_days']['today']['date'] == '2026-11-11'
        assert second['request_meaning_fingerprint'] != first['request_meaning_fingerprint']
        assert sealed_meaning(second) is not None
        assert json.loads(transport.messages[2][1]['content'])['temporal_context'] == second['temporal_context']
        saved = graph.invoke(Command(resume={'type': 'confirm'}), config)
        assert saved['status'] == 'saved' and mail_arguments(saved)['query'] == original_query
    finally:
        connection.close()
        store.close()


def test_rejected_meaning_preserves_request_and_uses_ordinary_planner(tmp_path):
    prompt = '내 Gmail에서 최근 5개 중 중요한 메일만 요약해줘'
    review = {'faithful': False, 'issues': ['중요한 메일의 선별 조건이 추출에서 누락됐습니다.']}
    transport = Extractor(meaning(), reviews=[review, review])
    seen = []
    def planner(current):
        seen.append(current)
        assert sealed_meaning(current) is None
        assert current['messages'][0]['content'] == prompt
        draft = gmail_summary_reply(state('gmail에서 최근 5개 메일을 요약해줘'))['draft']
        draft.update(workflow=None, tool_ids=[], purpose=prompt,
                     requirements=[{'description': '중요 메일 요약', 'source_id': 'user', 'quote': prompt}],
                     questions=['중요한 메일을 구분할 기준은 무엇인가요?'])
        return {'draft': draft, 'confirmed': False}
    store = Storage(tmp_path)
    current = state(prompt)
    graph, connection = build_graph(store, conversation=planner, meaning_transport=transport,
                                     capability_mode='off', condition_mode='off',
                                     requirement_mode='off', verifier_mode='off')
    config = {'configurable': {'thread_id': current['session_id']}}
    try:
        result = graph.invoke(current, config)
        assert len(transport.messages) == 4  # two rejected extraction/review attempts
        assert len(seen) == 1
        assert result['status'] == 'needs_clarification'
        assert result['__interrupt__'][0].value['kind'] == 'clarification'
        assert result['draft']['questions'] == ['중요한 메일을 구분할 기준은 무엇인가요?']
        assert result['draft']['workflow'] is None and sealed_meaning(result) is None
        assert not any(entry['node'] == 'gmail_summary' for entry in result['trace'])
        assert not store.agents() and result['saved_agent_id'] is None
    finally:
        connection.close()
        store.close()
