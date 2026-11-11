"""Use synthetic candidates to distinguish requested work from tool suggestions."""
import json

import pytest

from agent_builder.capabilities.capability_gate import input_fingerprint
from agent_builder.design.gmail_summary import gmail_summary_reply
from agent_builder.design.processing_guard import processing_issues
from agent_builder.design.request_meaning import extract_meaning, validate_meaning
from tests.design.test_qwen_public_meaning_failures import request
from tests.design.test_request_meaning import Extractor, meaning
from tests.design.test_task_omission import processing_request
from tests.workflow_engine.test_workflow import bind, edge, node


def table_state(text, suggestions):
    nodes = [node('read', 'read_table'),
             node('out', 'write_result', [bind('table', 'read', 'table')], format='json')]
    state = processing_request(nodes, [edge('read', 'out')])
    state['messages'] = [{'role':'user', 'content':text}]
    state['capability_assessment'].update(tool_ids=suggestions, user_quotes=[text])
    state['capability_fingerprint'] = input_fingerprint(state)
    return state


def test_scheduled_copy_does_not_require_an_incidental_filter_suggestion():
    state = table_state('연결된 표를 매일 오전 8시에 JSON으로 저장해줘.',
                        ['read_table', 'filter_sort', 'write_result'])
    assert processing_issues(state, state['draft']) == []


@pytest.mark.parametrize('text', [
    '연결한 표에서 region별 amount 합계를 집계하고 JSON으로 저장해줘.',
    '첨부 표에서 amount가 30 이상인 행만 선별해서 JSON으로 저장해줘.',
])
def test_source_only_candidate_does_not_authorize_omitting_requested_work(text):
    state = table_state(text, ['read_table', 'write_result'])
    assert processing_issues(state, state['draft'])


@pytest.mark.parametrize('text', [
    'Gmail에서 중요한 이메일만 골라 요약해 줘.',
    '내 Gmail 메일 중 중요한 것만 요약해줘.',
    'Gmail에서 제목이 "중요 표시"인 메일을 요약해줘.',
])
def test_important_flag_requires_user_evidence(text):
    raw = meaning(count=None, evidence=None, filters=['is:important'])
    with pytest.raises(ValueError):
        validate_meaning(raw, request(text))


def test_explicit_gmail_important_flag_reaches_executable_query():
    state = request('Gmail에서 중요 표시된 메일을 요약해줘.')
    raw = meaning(count=None, evidence=None, filters=['is:important'])
    state.update(extract_meaning(state, Extractor(raw)))
    response = gmail_summary_reply(state)
    assert response is not None
    reader = next(n for n in response['draft']['workflow']['nodes'] if n['tool']=='email.list')
    assert 'is:important' in json.loads(reader['settings']['arguments_json'])['query'].split()


def test_answer_updates_its_own_requirement_question_after_resume(monkeypatch):
    from agent_builder.design.nodes import await_input

    question = {'id':'req-name', 'source':'requirement', 'key':'compute.output_column',
                'text':'결과 열 이름을 알려 주세요.', 'options':[]}
    state = {'messages':[{'role':'user','content':'계산 열을 만들어 주세요.'},
                         {'role':'assistant','content':question['text']}],
             'condition_enforced':True, 'pending_questions':[question],
             'draft':{'questions':[question['text']]}, 'issues':[]}
    monkeypatch.setattr(await_input, 'interrupt', lambda payload: {
        'type':'message', 'question_id':'req-name', 'content':'net_total'})
    resumed = await_input.await_input_node(state)
    assert not resumed.get('condition_answers')
    event = resumed['requirement_answers'][-1]
    assert event['question_ids'] == ['req-name']
    assert event['candidate_keys'] == ['compute.output_column']
    assert event['answer'] == 'net_total' and event['turn'] == 2
