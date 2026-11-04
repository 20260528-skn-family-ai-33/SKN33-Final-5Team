import pytest
from tests.conditions.test_condition_readiness import state, Fake
from agent_builder.conditions.condition_check import make_condition_check
from agent_builder.capabilities.capability_gate import input_fingerprint, require_save_ready


@pytest.mark.parametrize('action', ['CSV를 JSON 파일로 변환', '외부 이메일 발송'])
def test_disagreeing_models_cannot_confirm_or_record_missing_capability(action):
    s = state('CSV 파일을 JSON으로 저장해줘.')
    s.update(capability_enforced=True, capability_assessment={'decision': 'existing_tools',
             'tool_ids': ['read_table', 'write_result']})
    s['capability_fingerprint'] = input_fingerprint(s)
    r = make_condition_check(mode='enforce', transport=Fake([{
        'operation': 'other', 'conditions': [], 'unsupported_actions': [action]}]))(s)
    assert r['condition_route'] == 'stop'
    assert r['status'] == 'validation_failed'
    assert r['condition_ledger']['decision'] == 'manual_review'
    assert r['condition_ledger']['reported_unsupported_actions'] == [action]
    assert r['draft']['unsupported'] == []
    with pytest.raises(ValueError):
        require_save_ready({**s, **r, 'confirmed': True})


def test_known_unsupported_without_opposing_capability_verdict_still_stops():
    s = state('외부 Slack으로 보내줘.')
    r = make_condition_check(mode='enforce', transport=Fake([{
        'operation': 'other', 'conditions': [], 'unsupported_actions': ['외부 Slack 발송']}]))(s)
    assert r['status'] == 'unsupported'
    assert r['condition_route'] == 'supervisor'


def test_other_supported_scope_still_reaches_normal_planner():
    s = state('CSV 파일을 JSON으로 저장해줘.')
    r = make_condition_check(mode='enforce', transport=Fake([{
        'operation': 'other', 'conditions': [], 'unsupported_actions': []}]))(s)
    assert r['condition_route'] == 'conversation'
    assert not r['condition_enforced']
