"""Calendar interpretation is anchored to the request, not the host or retry."""
from datetime import datetime, timezone
import json

import pytest

from agent_builder.design.temporal_context import temporal_context, ensure_temporal_context


def state(text='어제 받은 자료를 요약해줘', zone='Asia/Seoul'):
    return {'messages': [{'role': 'user', 'content': text}], 'user_timezone': zone}


@pytest.mark.parametrize(('instant', 'today', 'yesterday'), [
    ('2026-11-09T14:59:59Z', '2026-11-09', '2026-11-08'),
    ('2026-11-09T15:00:00Z', '2026-11-10', '2026-11-09'),
    ('2027-01-01T00:00:00+09:00', '2027-01-01', '2026-12-31'),
])
def test_korean_calendar_boundary_uses_request_zone(instant, today, yesterday):
    clock = temporal_context(state(), now=instant)
    assert clock['relative_days']['today']['date'] == today
    assert clock['relative_days']['yesterday']['date'] == yesterday
    previous = clock['relative_days']['yesterday']
    assert previous['end_epoch'] - previous['start_epoch'] == 86400
    assert previous['end'] == clock['relative_days']['today']['start']


@pytest.mark.parametrize(('instant', 'hours'), [
    ('2026-03-09T12:00:00Z', 23), ('2026-11-02T12:00:00Z', 25),
])
def test_day_window_respects_dst_not_fixed_24_hours(instant, hours):
    previous = temporal_context(state(zone='America/New_York'), now=instant)['relative_days']['yesterday']
    assert previous['end_epoch'] - previous['start_epoch'] == hours * 3600


def test_same_user_turn_keeps_clock_through_repair_and_restart():
    original = state()
    saved = ensure_temporal_context(original, now='2026-11-09T16:00:00Z')
    repaired = {**original, **saved, 'issues': ['설정 오류'],
                'messages': original['messages'] + [{'role': 'assistant', 'content': '수정 중'}]}
    assert temporal_context(repaired, now='2026-11-10T16:00:00Z') == saved['temporal_context']
    # Returning a context must not mutate a persisted checkpoint through aliasing.
    returned = temporal_context(repaired)
    returned['relative_days'].clear()
    assert saved['temporal_context']['relative_days']


def test_followup_tomorrow_does_not_move_original_yesterday():
    original = state()
    saved = ensure_temporal_context(original, now='2026-11-09T16:00:00Z')
    following = {**original, **saved, 'messages': original['messages'] + [
        {'role': 'assistant', 'content': '어떤 형식으로 정리할까요?'},
        {'role': 'user', 'content': '짧은 글로 해줘'}]}
    clock = temporal_context(following, now='2026-11-10T16:00:00Z')
    assert clock['relative_days']['yesterday']['date'] == '2026-11-10'
    assert clock['user_message_anchors'][0]['relative_days']['yesterday']['date'] == '2026-11-09'
    assert clock['user_message_anchors'][1]['relative_days']['yesterday']['date'] == '2026-11-10'


@pytest.mark.parametrize('zone', [None, '', 'Invalid/Timezone'])
def test_unknown_user_timezone_never_falls_back_to_host(zone, monkeypatch):
    monkeypatch.setenv('TZ', 'America/Los_Angeles')
    clock = temporal_context(state(zone=zone), now='2026-11-10T00:00:00Z')
    assert clock['timezone'] is None
    assert clock['relative_days'] == {} and clock['local_time'] is None


def test_configured_default_is_explicit_and_can_be_disabled(monkeypatch):
    current = {'messages': state()['messages']}
    monkeypatch.delenv('A4B_DEFAULT_TIMEZONE', raising=False)
    clock = temporal_context(current, now='2026-11-09T16:00:00Z')
    assert clock['timezone'] == 'Asia/Seoul' and clock['timezone_source'] == 'application_default'
    monkeypatch.setenv('A4B_DEFAULT_TIMEZONE', 'America/New_York')
    assert temporal_context(current, now='2026-11-09T16:00:00Z')['relative_days']['today']['date'] == '2026-11-09'
    monkeypatch.setenv('A4B_DEFAULT_TIMEZONE', '')
    assert temporal_context(current, now='2026-11-09T16:00:00Z')['timezone'] is None


def test_timezone_change_reinterprets_same_instant_without_new_clock():
    original = state()
    saved = ensure_temporal_context(original, now='2026-11-09T16:00:00Z')
    updated = temporal_context({**original, **saved, 'user_timezone': 'America/New_York'},
                               now='2026-11-11T16:00:00Z')
    assert updated['reference_time'] == saved['temporal_context']['reference_time']
    assert updated['relative_days']['today']['date'] == '2026-11-09'
    assert updated['user_message_anchors'][0]['timezone'] == 'America/New_York'


def test_old_unanchored_messages_do_not_acquire_fabricated_receipt_times():
    current = state()
    current['messages'].append({'role': 'user', 'content': '짧게 해줘'})
    clock = temporal_context(current, now='2026-11-10T00:00:00Z')
    assert clock['user_message_anchors'][0]['reference_time'] is None
    assert clock['user_message_anchors'][0]['relative_days'] == {}


def test_injected_clock_must_include_offset():
    with pytest.raises(ValueError, match='UTC 오프셋'):
        temporal_context(state(), now=datetime(2026, 11, 10))
    assert temporal_context(state(), now=datetime(2026, 11, 10, tzinfo=timezone.utc))['reference_time'].endswith('Z')


def test_direct_planner_receives_frozen_calendar(monkeypatch):
    from tests.design.test_direct_conversation import state as direct_state, draft, Fake, planner
    original = direct_state()
    original.update(ensure_temporal_context(original, now='2026-11-09T16:00:00Z'))
    fake = Fake(draft())
    planner(monkeypatch, fake)(original)
    context = json.loads(fake.calls[0][1]['content'].split('\n', 1)[1])
    assert context['temporal_context'] == original['temporal_context']
    assert '반복 예약' in fake.calls[0][0]['content']
    assert '날짜를 다시 묻지 않습니다' in fake.calls[0][0]['content']


def test_deepagent_receives_frozen_calendar(monkeypatch):
    from tests.design.test_deep_agent_conversation import state as agent_state, draft
    from agent_builder.design.nodes.conversation import make_conversation
    original = agent_state(state()['messages'])
    original.update(ensure_temporal_context(original, now='2026-11-09T16:00:00Z'))
    captured = {}
    def factory(**options):
        captured['system'] = options['system_prompt']
        class Agent:
            def invoke(self, payload, config):
                captured['context'] = json.loads(payload['messages'][0]['content'].split('\n', 1)[1])
                return {'structured_response': draft(questions=['어떤 길이로 요약할까요?'])}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', factory)
    make_conversation('', 'fixture', model_factory=lambda: object())(original)
    assert captured['context']['temporal_context'] == original['temporal_context']
    assert '반복 예약' in captured['system']


def test_capability_assessment_uses_same_calendar():
    from tests.conditions.test_condition_readiness import state as condition_state
    from tests.capabilities.test_capability_assessment import packet
    from agent_builder.capabilities.capability_judgement import assess_capability
    original = condition_state()
    original.update(ensure_temporal_context(original, now='2026-11-09T16:00:00Z'))
    class Fake:
        def invoke(self, messages):
            assert json.loads(messages[1]['content'])['temporal_context'] == original['temporal_context']
            assert '반복 예약' in messages[0]['content']
            return {'content': json.dumps(packet(original), ensure_ascii=False)}
    assert assess_capability(original, Fake())['decision'] == 'existing_tools'
