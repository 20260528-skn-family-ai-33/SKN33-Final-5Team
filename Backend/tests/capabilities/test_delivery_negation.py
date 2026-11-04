"""A particle between the verb and its negation still negates the delivery request.

Live 2026-11-03 (qwen3.5:4b, B-6 n01): "결과를 메일로 보내지는 마" was turned into
missing_capability by the code check although the model judged existing_tools.
"""
import pytest
from agent_builder.capabilities.capability_judgement import _external_delivery, assess_capability
from tests.capabilities.test_capability_assessment import state, packet, Fake


@pytest.mark.parametrize('text', [
    '결과를 슬랙으로 보내지는 마. CSV로만 저장해.',
    '담당자에게 문자로 보내지는 말고 파일로만 저장해.',
    '슬랙으로는 보내지도 마. JSON으로 저장해.',
    '카톡으로 알려주지는 마. CSV로 저장해.',
    '결과를 슬랙에 공유하지는 마. CSV로만 저장해.',
])
def test_negated_delivery_with_particle_is_not_reclassified(text):
    assert next(_external_delivery(text), None) is None
    s = state(text)
    result = assess_capability(s, Fake([packet(s, 'existing_tools')]))
    assert result['decision'] == 'existing_tools' and 'code_external_delivery' not in result


@pytest.mark.parametrize('text,channel', [
    ('결과를 슬랙으로 보내줘. 보내지는 말라는 말은 아니야.', '슬랙'),
    ('담당자에게 문자로 보내고 CSV로도 저장해.', '문자'),
    ('정리한 표를 슬랙에 공유해줘.', '슬랙'),
])
def test_real_delivery_requests_are_still_recorded(text, channel):
    s = state(text)
    result = assess_capability(s, Fake([packet(s, 'existing_tools')]))
    assert result['decision'] == 'missing_capability'
    assert result['missing_actions'] == ['외부 ' + channel + ' 전송']
