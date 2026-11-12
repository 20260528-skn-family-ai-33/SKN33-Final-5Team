"""Clarification turns through the service message/confirm entry (handover 2026-11 cases).

Real LangGraph + SQLite; capability and planner replies are controlled fixtures, so these
check routing/state contracts, not the production model's behaviour.
"""
import json

import pytest

from agent_builder.design.attachment_precheck import QUESTION, missing_attachment_reply
from agent_builder.design.runtime_inputs import runtime_file_context
from agent_builder.design.turn_intent import split_turn
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from service.worker import handle
from tests.design.test_runtime_file_questions import question_draft
from tests.workflow_engine.test_workflow import node, bind, edge

ORIGINAL = '계약서를 검토하고 위험한 조항만 근거를 붙여 답해줘'
RISK_Q = '어떤 위험 기준을 적용할까요?'


class Fake:
    def __init__(self, packets):
        self.packets = list(packets); self.calls = 0; self.seen = []
    def invoke(self, messages):
        self.calls += 1; self.seen.append(messages)
        return {'content': json.dumps(self.packets.pop(0), ensure_ascii=False)}


def assessment(request, questions=()):
    return dict(decision='clarify' if questions else 'existing_tools',
                tool_ids=['read_document', 'search_document', 'compose_answer'],
                missing_conditions=list(questions), missing_actions=[], reason='fixture', user_quotes=[request])


def document_planner(calls, request):
    def planner(s):
        calls.append(s['messages'][-1]['content'])
        nodes = [node('read', 'read_document'),
                 node('search', 'search_document', [bind('documents', 'read', 'documents')], query='위험'),
                 node('answer', 'compose_answer', [bind('documents', 'search', 'documents')], instructions='원문 근거로 답변')]
        d = question_draft('unused')
        d.update(name='계약서 검토', purpose='위험 조항 확인', tool_ids=[n['tool'] for n in nodes], questions=[],
                 question_options=[], requirements=[dict(description='검토', source_id='user', quote=request)],
                 workflow=dict(entry='read', nodes=nodes, edges=[edge('read', 'search'), edge('search', 'answer')]))
        return dict(draft=d, confirmed=False)
    return planner


@pytest.fixture
def service(tmp_path, monkeypatch):
    import service.handlers.design as worker
    original = worker.build_graph
    box = {}
    def wrapped(*args, **kwargs):
        kwargs.update(capability_mode='enforce', capability_transport=box['cap'], condition_mode='enforce', verifier_mode='off')
        return original(*args, **kwargs)
    monkeypatch.setattr(worker, 'build_graph', wrapped)
    session = handle({'directory': str(tmp_path), 'action': 'new_session', 'model': 'test', 'credential_id': uid()})
    state = {'s': session}
    def act(name, planner=None, **fields):
        result = handle({'directory': str(tmp_path), 'id': state['s']['id'], 'revision': state['s']['revision'],
                         'action': name, **fields}, conversation=planner)
        state['s'] = result
        return result
    def saved():
        store = Storage(tmp_path)
        try: return len(store.agents())
        finally: store.close()
    return box, act, saved


def test_side_question_keeps_pending_question_and_only_the_real_answer_resumes(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(ORIGINAL, [RISK_Q]), assessment(ORIGINAL)])
    calls = []
    planner = document_planner(calls, ORIGINAL)
    first = act('message', planner, content=ORIGINAL)
    assert first['status'] == 'needs_clarification' and box['cap'].calls == 1
    qid = first['pending_questions'][0]['id']

    side = act('message', planner, content='오늘 날씨 어때?')
    assert side['status'] == 'needs_clarification'
    assert [q['id'] for q in side['pending_questions']] == [qid]
    assert box['cap'].calls == 1 and calls == [] and saved() == 0
    # While a question waits, any side question is answered with that question explained (2026-11-12).
    assert side['messages'][-1]['scope'] == 'side' and '기록하지 않았어요' in side['messages'][-1]['content']
    assert '어떤 위험 기준을 적용할까요?' in side['messages'][-1]['content']
    # The side question is shown, but is not a graph message, answer event or fingerprint input.
    with pytest.raises(ValueError):
        act('confirm')
    assert saved() == 0

    answered = act('message', planner, content='위약금이 있는 조항을 위험으로 봐줘', question_id=qid)
    assert answered['status'] == 'ready' and box['cap'].calls == 2
    assert calls == ['위약금이 있는 조항을 위험으로 봐줘']
    user_turns = [m['content'] for m in box['cap'].seen[-1][1:2]]
    assert '오늘 날씨 어때' not in user_turns[0]
    assert saved() == 0
    done = act('confirm')
    assert done['status'] == 'saved' and saved() == 1


def test_mixed_answer_and_side_question_resumes_with_the_answer_part_only(service):
    box, act, saved = service
    box['cap'] = Fake([assessment(ORIGINAL, [RISK_Q]), assessment(ORIGINAL)])
    calls = []
    planner = document_planner(calls, ORIGINAL)
    act('message', planner, content=ORIGINAL)
    result = act('message', planner, content='위약금이 있는 조항을 위험으로 봐줘. 그런데 오늘 날씨 어때?')
    assert calls == ['위약금이 있는 조항을 위험으로 봐줘.']
    assert result['status'] == 'ready' and saved() == 0
    assert any(m.get('scope') == 'side' and '날씨' in m['content'] for m in result['messages'])


def test_greeting_or_one_off_question_alone_creates_no_draft(service):
    box, act, saved = service
    box['cap'] = Fake([])
    for text in ('안녕하세요', '오늘 날씨 어때?'):
        result = act('message', content=text)
        assert result['draft'] is None and box['cap'].calls == 0 and saved() == 0


def test_greeting_with_an_automation_request_still_designs():
    turn = split_turn('안녕하세요! 매일 아침 9시에 날씨를 메일로 보내줘', {'messages': []})
    assert turn['side'] == [] and '매일 아침' in turn['work']
    assert split_turn('매일 아침 날씨 알려줄래?', {'messages': []})['side'] == []


def test_capability_question_already_settled_by_the_user_is_not_shown(service):
    box, act, saved = service
    request = '계약서 해지 조항을 요약해줘'
    box['cap'] = Fake([assessment(request, ['어떤 조항을 요약할까요?']), assessment(request)])
    calls = []
    result = act('message', document_planner(calls, request), content=request)
    assert box['cap'].calls == 2  # one bounded re-assessment with the settled evidence
    assert '어떤 조항을 요약할까요?' not in json.dumps(result['pending_questions'], ensure_ascii=False)
    assert result['status'] == 'ready' and len(calls) == 1 and saved() == 0


def test_repeated_settled_only_capability_question_stays_in_review(service):
    box, act, saved = service
    request = '계약서 해지 조항을 요약해줘'
    box['cap'] = Fake([assessment(request, ['어떤 조항을 요약할까요?'])] * 2)
    calls = []
    result = act('message', document_planner(calls, request), content=request)
    assert result['status'] == 'validation_failed' and calls == [] and not result['pending_questions']
    with pytest.raises(ValueError):
        act('confirm')
    assert saved() == 0


def dialogue(*texts):
    return {'messages': [{'role': 'user', 'content': t} for t in texts], 'resources': []}


def test_declared_run_input_is_not_requested_now():
    s = dialogue('워크플로우 입력은 계약서 PDF 1개로 설정하고, 업로드된 파일 도구로 해지 조항을 정리해줘')
    assert missing_attachment_reply(s) is None and runtime_file_context(s)


def test_mixed_files_ask_only_for_the_file_needed_now():
    s = dialogue('방금 첨부한 계약서 PDF를 분석해줘. 비교용 견적서는 실행할 때 첨부할게요.')
    reply = missing_attachment_reply(s)
    assert reply['draft']['questions'] == [QUESTION]
    assert any('견적서' in r['description'] for r in reply['draft']['requirements'])
    assert missing_attachment_reply(dialogue('방금 올린 PDF를 분석해줘'))['draft']['questions'] == [QUESTION]


@pytest.mark.parametrize('choice', ['실행할 때 넣을게요', '작업을 돌리는 시점에 자료를 넘기겠습니다',
                                    '워크플로우를 실행하는 시점에 파일을 전달할게요', '나중에 돌릴 때 PDF를 올릴게요'])
def test_paraphrased_runtime_choice_is_recorded_the_same_way(choice):
    s = dialogue('첨부한 계약서 PDF를 검토해줘', choice, '첨부한 계약서 PDF의 해지 조건도 검토해줘')
    assert missing_attachment_reply(s) is None and runtime_file_context(s)


@pytest.mark.parametrize('change', ['실행할 때 말고 지금 올릴게요', '실행할 때 넣지 않을게요'])
def test_explicit_timing_change_replaces_the_runtime_choice(change):
    s = dialogue('첨부한 계약서 PDF를 검토해줘', '실행할 때 넣을게요', change, '첨부한 계약서 PDF의 해지 조건도 검토해줘')
    assert missing_attachment_reply(s)['draft']['questions'] == [QUESTION]
    assert runtime_file_context(s) is None


def planner_state(text):
    return dict(session_id='f', model='f', credential_id='local-model', messages=[dict(role='user', content=text)],
                resources=[], issues=[], repair_count=0, node_library=[], required_node_refs=[], saved_workflows=[], trace=[])


def test_direct_planner_redesigns_once_and_never_shows_a_settled_question(monkeypatch):
    from agent_builder.design.nodes.direct_conversation import make_direct_conversation
    monkeypatch.setenv('A4B_PLANNER_PROMPT', 'v8'); monkeypatch.setenv('A4B_TOOL_ADVICE_MODE', 'off')
    model = Fake([question_draft('어떤 조항을 요약할까요?')] * 2)
    with pytest.raises(ValueError, match='이미 정한 조건을 다시 묻는'):
        make_direct_conversation('', 'f', model_factory=lambda: model)(planner_state('계약서 해지 조항을 요약해줘'))
    assert model.calls == 2
    # A real open question is still delivered on the first attempt.
    model = Fake([question_draft('어떤 위험 기준을 적용할까요?')])
    result = make_direct_conversation('', 'f', model_factory=lambda: model)(planner_state(ORIGINAL))
    assert result['draft']['questions'] == ['어떤 위험 기준을 적용할까요?'] and model.calls == 1


def test_deepagent_planner_redesigns_once_and_never_shows_a_settled_question(monkeypatch):
    from unittest.mock import patch
    from agent_builder.design.nodes.conversation import make_conversation
    from tests.design.test_deep_agent_conversation import draft
    monkeypatch.setenv('A4B_PLANNER_MODE', 'deepagent'); monkeypatch.setenv('A4B_TOOL_ADVICE_MODE', 'off')
    calls = []
    class Agent:
        def invoke(self, payload, config):
            calls.append(1)
            return {'structured_response': draft(questions=['어떤 조항을 요약할까요?'])}
    with patch('agent_builder.design.nodes.conversation.create_deep_agent', lambda **kw: Agent()):
        with pytest.raises(ValueError, match='이미 정한 조건을 다시 묻는'):
            make_conversation('', 'f', model_factory=object)(planner_state('계약서 해지 조항을 요약해줘'))
    assert len(calls) == 2
