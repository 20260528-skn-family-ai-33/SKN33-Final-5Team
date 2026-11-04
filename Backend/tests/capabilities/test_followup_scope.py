import json

import pytest

from agent_builder.capabilities.capability_check import make_capability_check
from agent_builder.capabilities.capability_judgement import delivery_conflict, validate_assessment_result
from tests.capabilities.test_capability_assessment import packet
from tests.conditions.test_condition_readiness import Fake, state


def assess(session, tools):
    latest = session['messages'][-1]['content']

    class RecordingModel(Fake):
        def invoke(self, messages):
            self.request = json.loads(messages[1]['content'])
            return super().invoke(messages)

    model = RecordingModel([{**packet(session, tools=tools), 'user_quotes': [latest]}])
    update = make_capability_check('fixture', model, mode='enforce')(session)
    return model.request, update


@pytest.mark.parametrize('followup', [
    '거기에 월별 매출 합계도 계산해줘.',
    '월별 매출 합계를 계산해줘.',
    '월별 결과를 새 파일로 만들어줘.',
])
def test_followup_keeps_cancelled_email_in_capability_decision(followup):
    original = '팀별 매출을 집계해줘. 이메일 발송은 취소해줘.'
    session = state(original)
    session['messages'] += [{'role': 'assistant', 'content': '확인했습니다.'},
                            {'role': 'user', 'content': followup}]
    request, update = assess(session, ['aggregate_table', 'email.send'])
    assert request['user_quote_catalog'] == [original, followup]
    assert request['active_delivery_directives']['email']['intent'] == 'cancel_delivery'
    assert update['capability_route'] == 'stop'
    assert update['capability_assessment']['decision'] == 'manual_review'


def test_explicit_new_task_stays_separate_during_later_replies_and_delivery_validation():
    first = '거래 목록을 집계해줘. 이메일로 보내줘.'
    new = '새 업무로 반품 정책 문서에서 반품 기한을 찾아줘.'
    edit = '찾은 원문도 함께 보여줘.'
    session = state(first)
    session['messages'] += [{'role': 'assistant', 'content': '확인했습니다.'},
                            {'role': 'user', 'content': new}]
    request, decision = assess(session, ['read_document'])
    assert request['user_quote_catalog'] == [new]
    assert decision['capability_route'] == 'conditions'
    session['messages'] += [{'role': 'assistant', 'content': '문서를 확인할게요.'},
                            {'role': 'user', 'content': edit}]
    request, decision = assess(session, ['read_document'])
    assert request['user_quote_catalog'] == [new, edit]
    assert decision['capability_route'] == 'conditions'
    session['messages'].append({'role': 'assistant', 'content': '초안을 만들었습니다.'})
    assert delivery_conflict(session, ['read_document']) is None
    with pytest.raises(ValueError, match='원문 근거'):
        validate_assessment_result({**packet(session, tools=['read_document']), 'user_quotes': [first]}, session)


@pytest.mark.parametrize(('next_request', 'is_new'), [
    ('10월 지점 매출을 지난달과 비교해서 표로 만들어줘.', True),
    ('창고 재고표에서 발주 기준 미만인 상품만 골라줘.', True),
    ('그 영수증의 품목별 금액을 계산해서 CSV로 만들어줘.', False),
    ('품목별 비용을 집계해줘.', False),
])
def test_named_new_input_and_goal_versus_new_steps_on_the_same_input(next_request, is_new):
    first = '영수증에서 항목을 추출하고 슬랙으로 보내줘.'
    session = state(first)
    session['messages'] += [{'role': 'assistant', 'content': '확인했습니다.'},
                            {'role': 'user', 'content': next_request}]
    request, _ = assess(session, ['read_table'])
    assert request['user_quote_catalog'] == ([next_request] if is_new else [first, next_request])
