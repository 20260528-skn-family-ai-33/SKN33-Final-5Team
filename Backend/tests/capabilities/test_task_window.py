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


def test_turns_that_point_back_keep_the_conversation():
    """Judged alone, "다시 확인해줘" has no task; its quotes must come from the earlier request (PR #39 review 3)."""
    for reply in ['운영팀 검토를 마쳤습니다. 원래 조건으로 다시 확인해줘.', '위 조건 그대로 CSV로 저장해줘.',
                  '아니, amount가 30 초과로 바꿔줘.', '아까 그 표를 JSON 말고 CSV로 저장해줘.']:
        s = conv(RECEIPT, '어떤 항목을 한 줄씩 뽑을까요?', reply)
        assert _task_window(s) == s['messages'], reply
    s = conv(RECEIPT, '분석할 파일을 먼저 올려 주세요.', SALES)
    assert _task_window(s) == [s['messages'][-1]]


def test_cancelling_part_of_the_request_keeps_the_conversation():
    """PR #41 replays cancellations over the earlier request; judged alone they have no task."""
    for reply in ['중복 제거와 이메일 전송을 취소해줘.', '메일 발송은 하지 마. 요약만 해줘.', '이메일은 빼고 표만 저장해줘.']:
        s = conv('매출을 요약해서 메일로 보내줘.', '수신자는 누구인가요?', reply)
        assert _task_window(s) == s['messages'], reply


def test_mail_directives_of_an_earlier_request_do_not_bind_a_new_task():
    """Merged with PR #41: validation replays mail directives; a new task must not inherit them."""
    from agent_builder.capabilities.capability_judgement import delivery_directives
    s = conv('요약을 만들어줘.', '확인했습니다.', '메일로 보내줘.', '확인했습니다.', SALES)
    assert delivery_directives(s) == {}
    s = conv('요약을 만들어줘.', '확인했습니다.', '메일로 보내줘.', '수신자는요?', '메일은 취소해줘.')
    assert delivery_directives(s)['email']['intent'] == 'cancel_delivery'
