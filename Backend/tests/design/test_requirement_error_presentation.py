from copy import deepcopy

from agent_builder.design.presentation import public_requirement_errors


def test_legacy_requirement_diagnostics_are_not_displayed_as_user_questions():
    detail='요청 업무 목록을 확정하지 못했습니다. gmail_access_token / gmail_account_id / summary_format_preference'
    state={'messages':[{'role':'user','content':'Gmail 요약을 만들어줘.'},{'role':'assistant','content':detail}],
           'issues':[detail],'trace':[{'node':'requirements','detail':detail}],'status':'validation_failed'}
    original=deepcopy(state)
    shown=public_requirement_errors(state)
    assert shown['messages'][0]==state['messages'][0]
    assert '같은 조건으로 다시 시도' in shown['messages'][1]['content']
    assert '추가 확인' not in shown['messages'][1]['content']
    assert all('gmail_access_token' not in str(shown[key]) for key in ('messages','issues','trace'))
    assert state==original


def test_ordinary_questions_and_user_text_remain_unchanged():
    state={'messages':[{'role':'assistant','content':'몇 시에 실행할까요?'},
                       {'role':'user','content':'요청 업무 목록을 확정하지 못했습니다: 원문 인용'}],
           'issues':[],'trace':[{'node':'other','detail':'기존 기록'}]}
    assert public_requirement_errors(state)==state
