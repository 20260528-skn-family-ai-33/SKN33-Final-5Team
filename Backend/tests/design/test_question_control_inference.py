"""Customer decisions stay selectable even when a planner declares plain text."""
from copy import deepcopy

import pytest

from agent_builder.design.question_controls import (
    complete_draft_question_controls,
    complete_questions,
    infer_question_control,
    project_question_controls,
)


SCREENSHOT_QUESTIONS = [
    "매일 오전에 gmail에서 안읽은 메일들을 요약 정리해달라는 요청입니다. '매일 오전'의 구체적인 시간을 알려주세요. (예: 09:00)",
    "'안읽은 메일'을 정의하는 기준이 무엇인가요? (예: 읽지 않은 상태, 특정 기간 내 등)",
    "요약할 메일의 범위는 '전체 안읽은 메일'인가요, 아니면 특정 키워드나 주제에 대한 메일인가요?",
]


def plain_question(text, **extra):
    return {'text': text, 'options': [], 'response': {'type': 'text'}, **extra}


def test_screenshot_questions_get_clock_and_real_choice_controls():
    original = [plain_question(text) for text in SCREENSHOT_QUESTIONS]
    questions = complete_questions(original, {})
    assert questions[0]['response']['type'] == 'time'
    assert questions[0]['options'] == []
    for question in questions[1:]:
        assert question['response']['type'] == 'single_choice'
        assert len(question['options']) >= 2
    criteria = ' '.join(questions[1]['options'])
    assert '읽지 않은 상태' in criteria and '특정 기간' in criteria
    scope = ' '.join(questions[2]['options'])
    assert '전체 안읽은 메일' in scope and '키워드' in scope
    assert not any('7일' in option for question in questions for option in question['options'])
    assert original == [plain_question(text) for text in SCREENSHOT_QUESTIONS]


@pytest.mark.parametrize('text', [
    '보고서의 파일 형식은 CSV 또는 JSON 중 무엇으로 만들까요?',
    '저장할 파일 형식을 선택해주세요. (예: CSV, JSON)',
])
def test_explicit_format_alternatives_become_choices_outside_email(text):
    question = infer_question_control(plain_question(text), {})
    assert question['response']['type'] == 'single_choice'
    assert question['options'] == ['CSV', 'JSON']


def test_all_items_wording_preserves_multiple_selection():
    question = infer_question_control(plain_question(
        '보고서에 포함할 항목을 모두 선택해주세요. (요약, 상세 내역, 오류 목록)'), {})
    assert question['response']['type'] == 'multi_choice'
    assert question['options'] == ['요약', '상세 내역', '오류 목록']


def test_a_quoted_column_name_is_not_a_sort_direction_choice():
    question=infer_question_control(plain_question("'가격' 열은 '오름차순' 또는 '내림차순'으로 정렬할까요?"),{})
    assert question['options']==['오름차순','내림차순']


def test_explicit_single_selection_takes_priority_over_included_items_wording():
    question=infer_question_control(plain_question('보고서에 포함할 항목은 요약 또는 상세 중 한 가지만 선택해주세요.'),{})
    assert question['response']['type']=='single_choice'
    assert question['options']==['요약','상세']


def test_live_capability_labels_do_not_include_parenthetical_question_headings():
    text='요약 형식 (메일별 핵심 요약 또는 전체 메일 통합 요약)에 대해 알려주세요.'
    assert infer_question_control(plain_question(text),{})['options']==['메일별 핵심 요약','전체 메일 통합 요약']
    assert infer_question_control(plain_question('실행 시각 (시간)에 대해 알려주세요.'),{})['response']=={'type':'time'}


def test_already_saved_malformed_labels_are_repaired_on_reload():
    question=plain_question('요약 형식 (메일별 핵심 요약 또는 전체 메일 통합 요약)에 대해 알려주세요.',
        options=['요약 형식 (메일별 핵심 요약','전체 메일 통합 요약)에 대해 알려주세요'],response={'type':'single_choice'})
    assert infer_question_control(question,{})['options']==['메일별 핵심 요약','전체 메일 통합 요약']


@pytest.mark.parametrize('text', [
    '기준 금액은 얼마인가요?',
    '한 번에 몇 건을 처리할까요?',
    '대기 시간은 몇 초인가요?',
])
def test_actual_numeric_decisions_use_number_without_invented_bounds(text):
    question = infer_question_control(plain_question(text), {})
    assert question['response']['type'] == 'number'
    assert question['options'] == []
    for field in ('minimum', 'maximum', 'step'):
        assert question['response'].get(field) is None


@pytest.mark.parametrize('text', [
    '요약할 주제와 원하는 문체를 설명해주세요.',
    '보고서 제목은 무엇인가요? (예: 월간 현황)',
    '어떤 업무인가요?',
    '',
])
def test_open_answers_and_single_examples_do_not_invent_choices(text):
    question = infer_question_control(plain_question(text), {})
    assert question['response']['type'] == 'text'
    assert question['options'] == []


def test_existing_multiple_selection_and_numeric_bounds_remain_authoritative():
    questions = [
        {'text': '포함할 열을 선택해주세요.', 'options': ['이름', '날짜'],
         'response': {'type': 'multi_choice'}},
        {'text': '처리량을 정해주세요.', 'options': [],
         'response': {'type': 'number', 'minimum': 0, 'maximum': 500, 'step': 0.5, 'unit': '건'}},
    ]
    original = deepcopy(questions)
    assert complete_questions(questions, {}) == original
    assert questions == original


def test_repeated_options_are_deduplicated_without_changing_the_question_identity():
    question = {'id': 'same-question', 'key': 'report.format', 'source': 'requirements',
                'required': False, 'group': '결과 형식', 'reason': '저장 형식을 결정합니다.',
                'text': '결과 형식을 선택해주세요.', 'options': ['CSV', 'JSON', 'CSV'],
                'response': {'type': 'single_choice'}}
    original = deepcopy(question)
    result = infer_question_control(question, {})
    assert result['options'] == ['CSV', 'JSON']
    for field in ('id', 'key', 'source', 'required', 'group', 'reason', 'text'):
        assert result[field] == original[field]
    assert question == original


def test_draft_completion_keeps_other_workflow_data_and_alignment():
    draft = {'name': '원래 업무', 'workflow': {'entry': 'same-node'},
             'questions': SCREENSHOT_QUESTIONS, 'question_options': [[], [], []],
             'question_responses': [{'type': 'text'}] * 3}
    original = deepcopy(draft)
    result = complete_draft_question_controls(draft, {})
    assert result['questions'] == original['questions']
    assert result['workflow'] == original['workflow']
    assert [response['type'] for response in result['question_responses']] == [
        'time', 'single_choice', 'single_choice']
    assert len(result['question_options']) == len(result['questions'])
    assert draft == original


def test_reopening_old_pending_and_draft_questions_projects_the_same_controls():
    text = '보고서의 파일 형식은 CSV 또는 JSON 중 무엇으로 만들까요?'
    state = {'messages': [{'role': 'user', 'content': '보고서를 만들어줘.'}],
             'pending_questions': [plain_question(text, id='persisted-id', key='format',
                                                   source='requirements', required=False)],
             'draft': {'questions': [text], 'question_options': [[]],
                       'question_responses': [{'type': 'text'}]}}
    original = deepcopy(state)
    result = project_question_controls(state)
    pending = result['pending_questions'][0]
    assert pending['id'] == 'persisted-id' and pending['required'] is False
    assert pending['options'] == ['CSV', 'JSON']
    assert pending['response']['type'] == 'single_choice'
    assert result['draft']['question_options'] == [['CSV', 'JSON']]
    assert result['draft']['question_responses'] == [pending['response']]
    assert state == original


def test_reopening_draft_only_questions_does_not_require_pending_records():
    state = {'draft': {'questions': SCREENSHOT_QUESTIONS, 'question_options': [[], [], []],
                       'question_responses': [{'type': 'text'}] * 3}, 'pending_questions': []}
    result = project_question_controls(state)
    assert result['pending_questions'] == []
    assert [response['type'] for response in result['draft']['question_responses']] == [
        'time', 'single_choice', 'single_choice']
    assert all(len(options) >= 2 for options in result['draft']['question_options'][1:])


def test_direct_planner_persists_completed_controls_without_an_extra_model_call(monkeypatch):
    from tests.design.test_direct_conversation import Fake, draft, planner, state
    text = '보고서의 파일 형식은 CSV 또는 JSON 중 무엇으로 만들까요?'
    response = {**draft(), 'questions': [text], 'question_options': [[]],
                'question_responses': [{'type': 'text'}]}
    fake = Fake(response)
    # The request must leave the format open: a format the user already wrote is never asked again.
    result = planner(monkeypatch, fake)({**state(),'messages':[{'role':'user','content':'입력 표를 파일로 저장해줘'}]})
    assert result['draft']['question_options'] == [['CSV', 'JSON']]
    assert result['draft']['question_responses'][0]['type'] == 'single_choice'
    assert len(fake.calls) == 1


def test_deep_planner_persists_completed_controls_without_an_extra_model_call(monkeypatch):
    from agent_builder.design.nodes.conversation import make_conversation
    from tests.design.test_deep_agent_conversation import draft, state
    text = '보고서의 파일 형식은 CSV 또는 JSON 중 무엇으로 만들까요?'
    calls = []

    class Agent:
        def invoke(self, payload, config):
            calls.append(payload)
            response = draft(questions=[text])
            response['question_responses'] = [{'type': 'text'}]
            return {'structured_response': response}

    monkeypatch.setenv('A4B_PLANNER_MODE', 'deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',
                        lambda **kwargs: Agent())
    result = make_conversation(None, 'test', model_factory=lambda: object())(
        state([{'role': 'user', 'content': '보고서를 만들어줘.'}]))
    assert result['draft']['question_options'] == [['CSV', 'JSON']]
    assert result['draft']['question_responses'][0]['type'] == 'single_choice'
    assert len(calls) == 1


def test_capability_clarification_uses_the_same_controls_before_interrupt(monkeypatch):
    from agent_builder.capabilities.capability_check import make_capability_check
    current={'messages':[{'role':'user','content':'보고서 설정을 확인해줘.'}],'resources':[],'trace':[]}
    monkeypatch.setattr('agent_builder.capabilities.capability_check.assess_capability',lambda *_:{
        'decision':'clarify','missing_conditions':[
            '파일 형식을 선택해주세요. (예: CSV, JSON)','한 번에 몇 건을 처리할까요?'],
        'model_calls':1})
    result=make_capability_check('fake',transport=object(),mode='enforce')(current)
    assert result['capability_route']=='await_capability'
    assert [q['response']['type'] for q in result['pending_questions']]==['single_choice','number']
    assert result['draft']['question_options'][0]==['CSV','JSON']
    assert [q['type'] for q in result['draft']['question_responses']]==[
        q['response']['type'] for q in result['pending_questions']]


def test_recovered_controls_are_preserved_with_the_answer_evidence(monkeypatch):
    from agent_builder.design.nodes.await_input import await_input_node
    text='보고서의 파일 형식은 CSV 또는 JSON 중 무엇으로 만들까요?'
    current={'messages':[],'issues':[],'draft':{'questions':[text]},'pending_questions':[
        plain_question(text,id='same-id',key='format',source='requirements')]}
    monkeypatch.setattr('agent_builder.design.nodes.await_input.interrupt',lambda _:{
        'type':'message','content':text+' → JSON','question_answers':[{'question_id':'same-id','value':'JSON'}]})
    result=await_input_node(current)
    event=result['requirement_answers'][0]
    assert event['value']=='JSON'
    assert event['questions'][0]['id']=='same-id'
    assert event['questions'][0]['response']['type']=='single_choice'
    assert event['questions'][0]['options']==['CSV','JSON']


def test_capability_typed_answers_reject_unknown_options_then_preserve_selected_value(monkeypatch):
    from agent_builder.capabilities.capability_check import await_capability_node
    text='파일 형식을 선택해주세요. (예: CSV, JSON)'
    question=plain_question(text,id='same-id',key='format',source='capability')
    current={'messages':[],'capability_questions':[question],'pending_questions':[question]}
    submitted=iter([
        {'type':'message','content':'형식 → PDF','question_answers':[{'question_id':'same-id','value':'PDF'}]},
        {'type':'message','content':'형식 → JSON','question_answers':[{'question_id':'same-id','value':'JSON'}]},
    ])
    payloads=[]
    def interrupt(payload):
        payloads.append(payload)
        return next(submitted)
    monkeypatch.setattr('langgraph.types.interrupt',interrupt)
    result=await_capability_node(current)
    assert len(payloads)==2 and 'error' in payloads[1]
    assert result['messages']==[{'role':'user','content':'형식 → JSON'}]
    assert result['capability_answers'][0]['answers'][0]['value']=='JSON'
