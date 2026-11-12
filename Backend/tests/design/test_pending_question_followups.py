"""Questions about a pending question, and help requests after a failure (2026-11-12 beta feedback).

"기준값이 뭐야?" consumed the pending question and removed its choices; "그럼 어떻게 해야돼?" after a
failure got a notice about real-time information. Pure router checks, then the service path.
"""
import tempfile

import pytest

from agent_builder.design.turn_intent import split_turn, side_reply
from service.worker import handle
from tests.design.test_runtime_file_questions import question_draft

STATE = {'messages': [{'role': 'user', 'content': '주문내역.csv에서 매출이 높은 주문만 정리해서 저장해줘.'}],
         'resources': [{'columns': ['amount', 'region', 'customer_id']}]}
THRESHOLD = [{'id': 'q1', 'text': '선별할 기준 값은 얼마인가요?', 'key': 'filter.value', 'response': {'type': 'number'}}]
FORMAT = [{'id': 'q2', 'text': '결과 파일 형식은 무엇으로 할까요?', 'options': ['CSV', 'JSON'], 'response': {'type': 'single_choice'}}]
RECIPIENT = [{'id': 'q3', 'text': '받는 사람 이메일 주소를 알려주세요.', 'response': {'type': 'text'}}]
SCHEDULE = [{'id': 'q4', 'text': '몇 시 몇 분에 실행할까요?', 'response': {'type': 'time'}}]


@pytest.mark.parametrize('pending,question', [(THRESHOLD, '기준값이 뭐야?'), (THRESHOLD, '그게 무슨 뜻이야?'), (THRESHOLD, '예를 들면?'),
                                              (FORMAT, '파일 형식이 뭔데?'), (RECIPIENT, '받는 사람이 뭐야?'), (SCHEDULE, '이게 무슨 말이야?')])
def test_a_question_about_the_pending_question_keeps_it_and_explains_it(pending, question):
    turn = split_turn(question, STATE, pending)
    assert turn['work'] == '' and turn['kinds'] == ['about_question']
    reply = side_reply(turn, [q['text'] for q in pending], pending)
    assert pending[0]['text'] in reply and '그대로 남아 있어요' in reply and '일회성' not in reply


def test_the_explanation_uses_only_the_question_record():
    assert '09:00처럼 시각으로' in side_reply(split_turn('이게 무슨 말이야?', STATE, SCHEDULE), [], SCHEDULE)
    assert 'CSV, JSON' in side_reply(split_turn('파일 형식이 뭔데?', STATE, FORMAT), [], FORMAT)
    plain = side_reply(split_turn('받는 사람이 뭐야?', STATE, RECIPIENT), [], RECIPIENT)
    assert '@' not in plain  # no example value is invented


@pytest.mark.parametrize('pending,answer', [
    (THRESHOLD, '20 이상으로 해줘.'), (THRESHOLD, '20 이상이 맞아?'), (THRESHOLD, 'amount야?'),
    (FORMAT, 'JSON'), (FORMAT, 'csv로 할까?'), (RECIPIENT, '수신자는 audit@example.invalid야.'),
    (SCHEDULE, '오전 9시 정각으로 해줘'), (SCHEDULE, '월요일 아침이면 될까?'),
    ([{'id': 'd', 'text': '중복된 행은 어떻게 처리할까요?', 'options': ['첫 행', '마지막 행']}], '마지막 행만 남길까?'),
])
def test_anything_that_carries_an_answer_stays_an_answer(pending, answer):
    turn = split_turn(answer, STATE, pending)
    assert turn['side'] == [] and turn['work'] == answer


def test_a_question_next_to_an_answer_lets_only_the_answer_through_without_a_reply():
    turn = split_turn('기준값이 뭐야? 100 이상으로 해줘', STATE, THRESHOLD)
    assert turn['work'] == '100 이상으로 해줘' and side_reply(turn, [], THRESHOLD) == ''


def test_without_a_pending_question_side_questions_and_greetings_are_unchanged():
    turn = split_turn('오늘 날씨 어때?', STATE, [])
    assert turn['kinds'] == ['side_question'] and '일회성 질문' in side_reply(turn, [])
    assert split_turn('안녕하세요', STATE, [])['kinds'] == ['greeting']


def test_a_help_request_after_a_failure_gets_the_failure_guidance():
    turn = split_turn('그럼 어떻게 해야돼?', STATE, [])
    reply = side_reply(turn, [], [], failure=True)
    assert '처리 오류로 멈춘 상태' in reply and '조건 그대로 다시 시도' in reply and '실시간 정보' not in reply


# Service path: the graph is not resumed, the question and its controls stay, and the request is recorded.

def _service(monkeypatch):
    for key, value in {'A4B_CAPABILITY_MODE': 'off', 'A4B_CONDITION_MODE': 'off', 'A4B_REQUIREMENT_MODE': 'off',
                       'A4B_VERIFIER_MODE': 'off'}.items():
        monkeypatch.setenv(key, value)
    directory = tempfile.mkdtemp()
    s = handle({'directory': directory, 'action': 'new_session', 'model': 'm', 'credential_id': 'local'})
    calls = []

    def planner(state):
        calls.append(state['messages'][-1]['content'])
        d = question_draft('선별할 기준 값은 얼마인가요?')
        d['question_responses'] = [{'type': 'number'}]
        return {'draft': d, 'confirmed': False, 'repair_count': state.get('repair_count', 0) + 1}

    def act(content, request_id):
        nonlocal s
        s = handle({'directory': directory, 'action': 'message', 'id': s['id'], 'revision': s['revision'],
                    'content': content, 'generation_request_id': request_id}, conversation=planner)
        return s
    return act, calls, directory


def test_service_keeps_a_planner_question_and_its_controls_when_asked_about_it(monkeypatch):
    act, calls, directory = _service(monkeypatch)
    first = act('매출이 높은 주문만 저장해줘', '00000000-0000-4000-8000-000000000001')
    asked = act('기준값이 뭐야?', '00000000-0000-4000-8000-000000000002')
    assert calls == ['매출이 높은 주문만 저장해줘']  # the graph was not resumed
    assert asked['draft']['questions'] == first['draft']['questions']
    assert asked['draft'].get('question_responses') == first['draft'].get('question_responses')
    assert asked['messages'][-1]['scope'] == 'side' and '선별할 기준 값은 얼마인가요?' in asked['messages'][-1]['content']
    assert asked['last_generation_request_id'] == '00000000-0000-4000-8000-000000000002'
    history = handle({'directory': directory, 'action': 'generation_history', 'id': asked['id']})['requests']
    assert history[0]['request_id'] == '00000000-0000-4000-8000-000000000002' and history[0]['status'] != 'running'
    answered = act('100 이상으로 해줘', '00000000-0000-4000-8000-000000000003')
    assert calls[-1] == '100 이상으로 해줘'
    assert answered['last_generation_request_id'] == '00000000-0000-4000-8000-000000000003'


def test_a_structured_answer_from_the_question_controls_skips_the_router(monkeypatch):
    act, calls, directory = _service(monkeypatch)
    s = act('매출이 높은 주문만 저장해줘', '00000000-0000-4000-8000-000000000011')

    def never(*args, **kwargs):
        raise AssertionError('router must not read a structured answer')
    monkeypatch.setattr('agent_builder.design.turn_intent.split_turn', never)
    with pytest.raises(ValueError) as error:  # the answer itself is validated by question_answers
        handle({'directory': directory, 'action': 'message', 'id': s['id'], 'revision': s['revision'],
                'content': '선별할 기준 값은 얼마인가요? → 뭐야?', 'question_answers': [{'question_id': 'x' * 24, 'value': 1}],
                'generation_request_id': '00000000-0000-4000-8000-000000000012'})
    assert 'router must not' not in str(error.value)


@pytest.mark.parametrize('answer', ['위약금 조항이 위험한 거 아니야?', '해지 조항이 위험하지 않을까?', '손해배상 한도가 없는 게 위험 기준이야?'])
def test_an_answer_phrased_as_a_question_is_still_an_answer(answer):
    risk = [{'id': 'r', 'text': '어떤 위험 기준을 적용할까요?'}]
    turn = split_turn(answer, {'messages': [{'role': 'user', 'content': '계약서에서 위험한 조항만 골라줘'}]}, risk)
    assert turn['side'] == [] and turn['work'] == answer


def test_asking_what_the_question_means_with_its_own_words_is_not_an_answer():
    risk = [{'id': 'r', 'text': '어떤 위험 기준을 적용할까요?'}]
    assert split_turn('위험 기준이 뭐야?', {'messages': []}, risk)['kinds'] == ['about_question']
