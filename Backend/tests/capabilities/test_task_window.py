"""The capability judgment sees only a new task request, not earlier unrelated requests (A5)."""
from agent_builder.capabilities.capability_judgement import _task_window

RECEIPT = '올려준 영수증 이미지 파일 내용을 엑셀파일로 정리해줘'
SALES = '10월 26일부터 11월 1일까지 지점 매출을 그 전주와 비교해서 회의용 표로 만들어줘.'


def conv(*turns, **extra):
    roles = ['user', 'assistant'] * len(turns)
    return {'messages': [{'role': r, 'content': c} for r, c in zip(roles, turns) if c is not None], **extra}


def test_a_new_task_after_an_unrelated_one_is_judged_alone():
    s = conv(RECEIPT, '분석할 파일을 먼저 올려 주세요.', SALES)
    assert _task_window(s) == [s['messages'][-1]]


def test_answers_to_pending_questions_keep_the_conversation():
    s = conv('비용 큰 것들만 골라서 파일로 만들어줘.', '기준 값은 얼마인가요?', '100만원 초과로 해줘. CSV로 저장해.',
             pending_questions=[{'id': 'q', 'key': 'filter.value'}])
    assert _task_window(s) == s['messages']


def test_short_replies_and_field_lists_keep_the_conversation():
    for reply in ['네', '모두 유지', '상품명, 수량, 단가, 금액', '그만할래']:
        s = conv(RECEIPT, '어떤 항목을 한 줄씩 뽑을까요?', reply)
        assert _task_window(s) == s['messages'], reply


def test_first_request_and_full_mode_are_unchanged(monkeypatch):
    s = conv(SALES)
    assert _task_window(s) == s['messages']
    s = conv(RECEIPT, '분석할 파일을 먼저 올려 주세요.', SALES)
    monkeypatch.setenv('A4B_CAPABILITY_CONTEXT', 'full')
    assert _task_window(s) == s['messages']
