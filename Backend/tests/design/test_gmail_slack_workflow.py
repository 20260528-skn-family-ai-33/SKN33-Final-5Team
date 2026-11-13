"""A Gmail summary can be clarified, checked, saved and delivered to Slack."""
from copy import deepcopy
import json

import pytest
from langgraph.types import Command

from agent_builder.design.gmail_slack import gmail_slack_candidate, gmail_slack_reply, gmail_slack_request
from agent_builder.design.gmail_summary import UNREAD_QUERY
from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_coverage import coverage_report
from infrastructure.persistence.storage import Storage


PROMPT = 'Gmail에서 어제 받은 메일을 요약해서 Slack으로 보내줘'
SCHEDULED = '매일 오전 9시에 Gmail에서 어제 받은 메일을 최대 25건 요약해서 Slack으로 보내줘'
UNREAD = 'gmail에서 안읽은 메일 3건 요약 정리해서 slack에 올려줘'
CHANNEL = 'C0CH769U6BX'


class Forbidden:
    def invoke(self, *args, **kwargs):
        raise AssertionError('The bounded Gmail-to-Slack flow should not call a design model.')


def state(prompt=PROMPT):
    return dict(session_id='gmail-slack-design', messages=[dict(role='user', content=prompt)],
                resources=[], trace=[], node_library=[], required_node_refs=[],
                model='gpt-4.1-mini', credential_id='local-model')


def graph_for(store):
    forbidden = Forbidden()
    return build_graph(store, conversation=lambda _: forbidden.invoke(),
                       meaning_transport=forbidden,
                       capability_mode='enforce', capability_transport=forbidden,
                       condition_mode='enforce', condition_transport=forbidden,
                       requirement_mode='enforce', requirement_transport=forbidden,
                       verifier_mode='off')


@pytest.mark.parametrize('prompt', [PROMPT, SCHEDULED, UNREAD])
def test_channel_clarification_then_real_saved_workflow(tmp_path, prompt):
    store = Storage(tmp_path)
    graph, connection = graph_for(store)
    config = {'configurable': {'thread_id': 'gmail-slack-' + str(len(prompt))}}
    try:
        first = graph.invoke(state(prompt), config)
        assert first['status'] == 'needs_clarification'
        assert first['capability_assessment']['decision'] == 'clarify'
        assert first['capability_assessment']['missing_actions'] == []
        assert first['__interrupt__'][0].value['kind'] == 'clarification'
        assert 'Slack 채널 ID' in first['messages'][-1]['content']
        assert first['pending_questions'][0]['key'] == 'slack.channel_id'
        if prompt == UNREAD:
            assert len(first['pending_questions']) == 1
            assert '기간' not in first['messages'][-1]['content']
        assert not store.agents()

        reviewed = graph.invoke(Command(resume={'type': 'message', 'content': CHANNEL}), config)
        assert reviewed['status'] == 'ready'
        assert reviewed['__interrupt__'][0].value['kind'] == 'review'
        draft = reviewed['draft']
        assert [node['tool'] for node in draft['workflow']['nodes']] == [
            'email.list', 'compose_answer', 'slack.post_message']
        reader, summary, sender = draft['workflow']['nodes']
        assert summary['bindings'] == [dict(input='documents', node_id='mail', output='documents')]
        assert sender['bindings'] == [dict(input='text', node_id='summary', output='text')]
        assert json.loads(sender['settings']['arguments_json']) == {'channel_id': CHANNEL}
        args = json.loads(reader['settings']['arguments_json'])
        assert args['max_results'] == (25 if prompt == SCHEDULED else 3 if prompt == UNREAD else 100)
        if prompt == SCHEDULED:
            assert args['query'] == '{{gmail.previous_day}}'
            assert draft['schedule']['frequency'] == 'daily'
            assert draft['schedule']['hour'] == 9
        elif prompt == UNREAD:
            assert args['query'] == UNREAD_QUERY
            assert '최근 안 읽은 Gmail 메일' in draft['explanation']
            assert 'gmail에서 안읽은 메일 3건 요약 정리해서 slack에 올려줘' in summary['settings']['instructions']
            assert not any('\u0400' <= char <= '\u04ff' for char in draft['explanation'])
            assert draft['schedule'] is None
        else:
            assert args['query'].startswith('after:')
            assert draft['schedule'] is None
        assert not coverage_report(reviewed, draft)['issues']
        assert not validate_node(reviewed)['issues']

        saved = graph.invoke(Command(resume={'type': 'confirm'}), config)
        assert saved['status'] == 'saved'
        persisted = store.agent(saved['saved_agent_id'])['config']
        assert persisted['workflow'] == draft['workflow']
        assert persisted['schedule'] == draft['schedule']
    finally:
        connection.close()
        store.close()


def test_destination_and_summary_lineage_cannot_be_changed_after_review():
    current = state(SCHEDULED)
    current['messages'].append(dict(role='user', content=CHANNEL))
    result = gmail_slack_reply(current)
    assert result is not None
    full = {**current, **result}
    for change in ('channel', 'binding', 'count', 'schedule'):
        altered = deepcopy(result['draft'])
        reader, _, sender = altered['workflow']['nodes']
        if change == 'channel':
            sender['settings']['arguments_json'] = json.dumps({'channel_id': 'C0CH769U6BY'})
        elif change == 'binding':
            sender['bindings'][0]['node_id'] = 'mail'
        elif change == 'count':
            reader['settings']['arguments_json'] = json.dumps({'max_results': 1, 'query': '{{gmail.previous_day}}'})
        else:
            altered['schedule']['hour'] = 10
        assert coverage_report(full, altered)['issues'], change


def test_unread_filter_cannot_be_removed_after_review():
    current = state(UNREAD)
    current.update(gmail_connected=True, slack_connected=True)
    current['messages'].append(dict(role='user', content=CHANNEL))
    result = gmail_slack_reply(current)
    assert result is not None
    assert '연결된 Gmail 읽기 계정과 Slack 계정을 사용합니다' in result['draft']['explanation']
    altered = deepcopy(result['draft'])
    reader = altered['workflow']['nodes'][0]
    reader['settings']['arguments_json'] = json.dumps({'max_results': 3})
    assert coverage_report({**current, **result}, altered)['issues']


def test_unrelated_and_cancelled_requests_do_not_enter_bounded_path():
    assert not gmail_slack_candidate(state('Gmail에서 어제 받은 메일을 요약해줘'))
    assert not gmail_slack_candidate(state('Slack에서 메시지 요약해서 보내줘'))
    assert not gmail_slack_candidate(state('Gmail에서 안 읽은 메일은 제외하고 요약해서 Slack으로 보내줘'))
    current = state(PROMPT)
    current['messages'].append(dict(role='user', content='Slack 전송은 취소해줘'))
    assert not gmail_slack_candidate(current)
    assert gmail_slack_request(current) is None


def test_recent_three_mail_request_keeps_count_and_asks_for_named_channel_id():
    current = state('가장 최근 Gmail 3건 요약해 Slack #소셜 채널로 전송해줘')
    request = gmail_slack_request(current)
    assert request['period'] == 'recent' and request['count'] == 3
    assert request['channel_id'] is None
    assert request['missing_conditions'] == ['요약을 보낼 Slack 채널 ID를 알려주세요. (예: C0123456789)']
    current['messages'].append(dict(role='user', content='C0CH66XVCUB'))
    request = gmail_slack_request(current)
    assert request['channel_id'] == 'C0CH66XVCUB'
    assert request['missing_conditions'] == []


def test_missing_period_answer_is_applied_to_its_own_turn():
    from agent_builder.design.temporal_context import temporal_context
    current = state('Gmail 메시지를 요약해서 Slack으로 보내줘')
    current['temporal_context'] = temporal_context(current)
    request = gmail_slack_request(current)
    assert len(request['missing_conditions']) == 2
    current['messages'].append(dict(role='user', content='어제'))
    current['temporal_context'] = temporal_context(current)
    current['messages'].append(dict(role='user', content=CHANNEL))
    current['temporal_context'] = temporal_context(current)
    request = gmail_slack_request(current)
    assert request['period'] == 'yesterday'
    assert request['missing_conditions'] == []
    assert request['query'].startswith('after:')


def test_unread_today_keeps_both_date_and_unread_filters():
    current = state('Gmail에서 오늘 안 읽은 메일 3건 요약해서 Slack으로 보내줘')
    request = gmail_slack_request(current)
    assert request['period'] == 'today'
    assert request['count'] == 3
    assert request['query'].startswith('is:unread after:')
    assert request['query'].endswith('-in:sent -in:drafts')
