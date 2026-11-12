"""A settled question must have evidence for that question and keep its UI controls aligned."""

from agent_builder.conditions.question_contract import answered_question, drop_settled, settled_by
from agent_builder.design.nodes.await_input import await_input_node


def _state(text):
    return {'messages': [{'role': 'user', 'content': text}]}


def test_input_format_does_not_answer_result_format_question():
    question = '결과 파일 형식은 무엇인가요?'
    assert settled_by(question, _state('CSV로 받은 자료를 정리해줘.')) is None
    assert settled_by(question, _state('CSV로 된 자료를 분석해서 저장해줘.')) is None
    assert settled_by(question, _state('CSV로 되어 있는 파일을 정리해줘.')) is None
    assert settled_by(question, _state('CSV로 작성된 자료를 분석해서 저장해줘.')) is None
    assert settled_by(question, _state('CSV로 받은 자료를 JSON으로 저장해줘.')) == 'JSON으로'
    assert settled_by(question, _state('CSV로 작성된 자료를 분석해서 JSON으로 저장해줘.')) == 'JSON으로'
    assert settled_by('입력 파일 형식은 무엇인가요?', _state('JSON으로 저장해줘.')) is None
    assert settled_by('보고서 파일 형식은 무엇인가요?', _state('영수증을 CSV로 저장해줘.')) is None
    assert settled_by('보고서 파일 형식은 무엇인가요?',
                      _state('영수증을 CSV로 저장하고 보고서를 PDF로 작성해줘.')) == 'PDF로'


def test_comparator_for_another_field_does_not_answer_question():
    current = _state('매출은 100 이상, 비용도 선별해줘.')
    assert settled_by('비용 비교 방식은 초과와 이상 중 무엇인가요?', current) is None
    assert settled_by('매출 비교 방식은 초과와 이상 중 무엇인가요?', current) == '100 이상'
    assert settled_by('비용 비교 방식은 초과와 이상 중 무엇인가요?', _state('비용은 100 초과로 해줘.')) == '100 초과'


def test_dropping_a_question_keeps_response_metadata_with_its_question():
    draft = {'questions': ['결과 파일 형식은 무엇인가요?', '포함할 항목은 무엇인가요?'],
             'question_options': [['CSV', 'JSON'], ['이름', '날짜']],
             'question_responses': [{'type': 'single_choice'}, {'type': 'multi_choice'}]}
    filtered, removed = drop_settled(draft, _state('JSON으로 저장해줘.'))
    assert removed == ['결과 파일 형식은 무엇인가요?']
    assert filtered['questions'] == ['포함할 항목은 무엇인가요?']
    assert filtered['question_options'] == [['이름', '날짜']]
    assert filtered['question_responses'] == [{'type': 'multi_choice'}]
    assert len(draft['questions']) == 2


def test_structured_answer_remembers_only_answered_requirement_questions(monkeypatch):
    first = {'id': '1' * 24, 'key': 'summary.fields', 'source': 'requirements',
             'text': '요약할 항목을 선택해주세요.', 'options': ['이름', '날짜'],
             'response': {'type': 'single_choice'}}
    second = {'id': '2' * 24, 'key': 'summary.style', 'source': 'requirements',
              'text': '선호하는 요약 방식이 있나요?', 'required': False,
              'options': [], 'response': {'type': 'text'}}
    state = {'pending_questions': [first, second],
             'draft': {'questions': [first['text'], second['text']]},
             'messages': [{'role': 'user', 'content': '자료를 요약해줘.'}], 'issues': []}
    reply = {'type': 'message', 'content': '요약 항목: 이름',
             'question_answers': [{'question_id': first['id'], 'value': '이름'},
                                  {'question_id': second['id'], 'value': None, 'skip': True}]}
    monkeypatch.setattr('agent_builder.design.nodes.await_input.interrupt', lambda payload: reply)
    result = await_input_node(state)
    event = result['clarification_answers'][0]
    assert event['explicit'] is True and event['kind'] == 'answer'
    assert event['question_ids'] == [first['id']]
    assert event['questions'][0]['text'] == first['text']
    later = {'pending_questions': [], 'clarification_answers': result['clarification_answers']}
    assert answered_question(first['text'], later)
    assert not answered_question(second['text'], later)


def test_legacy_structured_requirement_answer_still_closes_its_question():
    question = {'id': '3' * 24, 'text': '한 번에 몇 건을 처리할까요?'}
    old_clarification = {'question_ids': [question['id']], 'kind': 'answer', 'explicit': False}
    current = {'pending_questions': [], 'clarification_answers': [old_clarification],
               'requirement_answers': [{'questions': [question], 'question_ids': [question['id']],
                                        'answer': '0', 'value': 0}]}
    assert answered_question(question['text'], current)
    assert not answered_question('다른 질문은 무엇인가요?', current)


def test_legacy_free_text_skip_and_unknown_values_do_not_close_questions():
    question = {'id': '4' * 24, 'text': '요약 기준을 선택해주세요.'}
    old_clarification = {'question_ids': [question['id']], 'kind': 'answer', 'explicit': False}
    base = {'pending_questions': [], 'clarification_answers': [old_clarification]}
    for event in ({'questions': [question], 'answer': '이름'},
                  {'questions': [question], 'answer': '선택사항 건너뛰기', 'value': None, 'skipped': True},
                  {'questions': [question], 'answer': '모르겠어요', 'value': '모르겠어요'}):
        assert not answered_question(question['text'], {**base, 'requirement_answers': [event]})
