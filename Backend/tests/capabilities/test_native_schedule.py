"""A refusal whose only gap is the app's own daily/weekly schedule goes to the condition checker.

Live 2026-11-03 (RunPod, B-8): "매주 월요일 오전 8시마다 … JSON으로 저장해줘" was refused twice with
only "주간 예약" / "매주 월요일 오전 8 시 예약" as the gap. The app implements daily/weekly schedules.
"""
import pytest
from agent_builder.capabilities.capability_judgement import _native_schedule_label, assess_capability
from agent_builder.capabilities.capability_check import make_capability_check
from tests.conditions.test_condition_readiness import state, Fake
from tests.capabilities.test_capability_assessment import packet

WEEKLY = '매주 월요일 오전 8시마다 비용이 50만원 이상인 행만 JSON으로 저장해줘. 빈 비용은 그대로 둬. 중복 거래ID는 첫 행만 남겨. 정렬은 하지 마.'
TOOLS = ['read_table', 'filter_sort', 'write_result']


@pytest.mark.parametrize('label', ['매주 월요일 오전 8 시 예약', '매일 오전 9시 예약', '매주 오후 6시마다 예약', '주간 예약'])
def test_time_of_day_words_belong_to_the_schedule_label(label):
    assert _native_schedule_label(label)


@pytest.mark.parametrize('label', ['매주 오전 8시 Slack 전송', '연간 예약', '매일 오전 이메일 발송', '예약 활성화'])
def test_other_gaps_are_never_schedule_labels(label):
    assert not _native_schedule_label(label)


def test_schedule_only_refusal_is_handed_to_the_condition_checker():
    s = state(WEEKLY)
    refusal = packet(s, 'missing_capability', actions=['주간 예약'], tools=TOOLS)
    review = packet(s, 'missing_capability', actions=['매주 월요일 오전 8 시 예약'], tools=TOOLS)
    result = assess_capability(s, Fake([refusal, review]))
    assert result['decision'] == 'existing_tools' and result['native_schedule_reconciled']
    assert result['native_app_claims_removed'] == ['매주 월요일 오전 8 시 예약']
    assert not result['allows_generation'] and not result['allows_execution']


def test_reconciled_schedule_forces_the_condition_checker():
    s = state(WEEKLY)
    transport = Fake([packet(s, 'missing_capability', actions=['주간 예약'], tools=TOOLS),
                      packet(s, 'missing_capability', actions=['매주 월요일 오전 8 시 예약'], tools=TOOLS)])
    update = make_capability_check('fixture', transport, mode='enforce')(s)
    assert update['capability_assessment']['decision'] == 'existing_tools'
    assert update['condition_required'] is True


@pytest.mark.parametrize('text,actions', [
    ('매년 1월 1일 오전 9시에 표를 JSON으로 저장해줘.', ['연간 예약']),
    ('매주 월요일 오전 8시에 표를 저장하고 슬랙으로 보내줘.', ['매주 월요일 오전 8 시 예약', '외부 슬랙 전송']),
])
def test_real_gaps_stay_refused(text, actions):
    s = state(text)
    result = assess_capability(s, Fake([packet(s, 'missing_capability', actions=actions, tools=TOOLS)] * 2))
    assert result['decision'] == 'missing_capability'
    assert 'native_schedule_reconciled' not in result


def test_schedule_label_without_a_schedule_request_is_not_reconciled():
    s = state('비용이 50만원 이상인 행만 JSON으로 저장해줘.')
    result = assess_capability(s, Fake([packet(s, 'missing_capability', actions=['주간 예약'], tools=TOOLS)] * 2))
    assert result['decision'] == 'manual_review'


def test_refusal_without_registered_tools_is_not_reconciled():
    s = state(WEEKLY)
    result = assess_capability(s, Fake([packet(s, 'missing_capability', actions=['주간 예약'], tools=[])] * 2))
    assert result['decision'] == 'manual_review'
