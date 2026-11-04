"""A short reply that names no new work keeps the previous capability verdict without a model call.

Live 2026-11-03 (reproduction of a user session): after missing_capability, "모두 유지" and "네"
re-judged the whole conversation; the verdict flipped to existing_tools and then to an invented
"월간 예약·이메일 발송" gap that the request never mentioned.
"""
import pytest
from agent_builder.capabilities.capability_check import make_capability_check
from agent_builder.capabilities.capability_gate import input_fingerprint
from tests.conditions.test_condition_readiness import state, Fake
from tests.capabilities.test_capability_assessment import packet

REQUEST = '10월 26일부터 11월 1일까지 지점 매출을 그 전주와 비교해줘. 지점별 증감을 회의용 표로 만들어줘.'


class Forbidden:
    calls = 0
    def invoke(self, *a): pytest.fail('짧은 답에 지원 판단 모델을 다시 호출함')


def after(previous_decision, reply):
    s = state(REQUEST)
    kw = {'missing_capability': {'actions': ['전주 대비 비교 분석']}, 'clarify': {'conditions': ['비교할 기간']},
          'manual_review': {}}[previous_decision]
    s['capability_assessment'] = packet(s, previous_decision, tools=['read_table', 'aggregate_table'], **kw)
    s['capability_fingerprint'] = input_fingerprint(s)
    s['messages'] = s['messages'] + [{'role': 'assistant', 'content': '확인했습니다.'}, {'role': 'user', 'content': reply}]
    return s


@pytest.mark.parametrize('previous', ['missing_capability', 'manual_review'])
@pytest.mark.parametrize('reply', ['네', '모두 유지', '그만할래', '응 좋아.', '알겠어요'])
def test_short_reply_keeps_the_verdict(previous, reply):
    update = make_capability_check('fixture', Forbidden(), mode='enforce')(after(previous, reply))
    result = update['capability_assessment']
    assert result['decision'] == previous and result['model_calls'] == 0
    assert result['answer_resolution'] == 'short_reply'


def test_a_short_answer_to_a_clarify_question_is_judged_again():
    # "운영팀이 검토했습니다" answers the capability question; it is not a non-request reply.
    s = after('clarify', '운영팀이 검토했습니다.')
    transport = Fake([packet(s, 'existing_tools', tools=['read_table'])])
    make_capability_check('fixture', transport, mode='enforce')(s)
    assert transport.calls == 1


@pytest.mark.parametrize('reply', ['CSV로 저장해줘', '100만원', '대신 지역별 합계로 바꿔줘', '매주 월요일로 예약해', '지난달 실적과 비교하는 표를 원해'])
def test_a_reply_with_new_work_is_judged_again(reply):
    s = after('missing_capability', reply)
    transport = Fake([packet(s, 'existing_tools', tools=['read_table'])])
    update = make_capability_check('fixture', transport, mode='enforce')(s)
    assert transport.calls == 1 and update['capability_assessment']['decision'] == 'existing_tools'


def test_changed_resources_are_judged_again():
    s = after('missing_capability', '네')
    s['resources'] = [{'id': 'new', 'kind': 'table', 'name': 'sales.csv', 'columns': ['지점', '매출'], 'version': '1'}]
    transport = Fake([packet(s, 'existing_tools', tools=['read_table'])])
    make_capability_check('fixture', transport, mode='enforce')(s)
    assert transport.calls == 1
