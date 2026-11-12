"""The catalog gate reuses a complete bound summary only while its contracts apply."""
import pytest

from agent_builder.capabilities.capability_judgement import assess_capability
from tests.conditions.test_condition_readiness import Fake
from tests.design.test_summary_consumers import REQUEST, source


def existing(state):
    return dict(decision='existing_tools', tool_ids=['read_document'], missing_conditions=[],
                missing_actions=[], reason='controlled catalog response',
                user_quotes=[state['messages'][0]['content']])


def test_one_grounded_summary_two_consumers_need_no_model(monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = source()
    transport = Fake([existing(state)])
    assessment = assess_capability(state, transport)
    assert transport.calls == 0
    assert assessment['tool_ids'] == ['read_document', 'compose_answer', 'export_result', 'email.send']
    assert assessment['decision'] == 'existing_tools'
    assert assessment['allows_execution'] is assessment['allows_generation'] is False


@pytest.mark.parametrize('text', [
    REQUEST + ' 보내기 전에 내 승인을 받아.',
    REQUEST + ' 요약을 Slack에도 보내줘.',
    REQUEST.replace('같은 요약을 Gmail', '문서 원문을 Gmail'),
    REQUEST.replace('day@example.invalid', 'day@example.invalid 와 night@example.invalid'),
    REQUEST.replace('업무 전달', 'a' * 2001),
])
def test_unaccounted_effect_or_different_body_needs_catalog_review(monkeypatch, text):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = source(text)
    transport = Fake([existing(state)])
    assess_capability(state, transport)
    assert transport.calls == 1


@pytest.mark.parametrize('changed', ['owner', 'body_port', 'missing_tool', 'send_required_field', 'format', 'operation', 'native'])
def test_summary_contract_drift_or_missing_owner_keeps_assessment(monkeypatch, changed):
    from workflow_engine.catalog import canonical, registry
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off' if changed == 'owner' else 'enforce')
    if changed == 'body_port':
        original = canonical.ports
        def modified(tool, spec, selected=None):
            inputs, outputs = original(tool, spec, selected)
            return ({**inputs, 'body': 'records'}, outputs) if tool == 'email.send' else (inputs, outputs)
        monkeypatch.setattr(canonical, 'ports', modified)
    elif changed == 'missing_tool':
        monkeypatch.setattr('agent_builder.capabilities.capability_judgement.TOOLS',
                            [item for item in registry.TOOLS if item['id'] != 'export_result'])
    elif changed == 'send_required_field':
        original = canonical.arguments_spec
        monkeypatch.setattr(canonical, 'arguments_spec',
                            lambda tool,spec: {**original(tool,spec), 'bcc': True} if tool == 'email.send'
                            else original(tool,spec))
    elif changed == 'format':
        monkeypatch.setattr('workflow_engine.workflow.NATIVE_TEXT_EXPORT_FORMATS', ('json',))
    elif changed == 'operation':
        original = canonical.operation_spec
        monkeypatch.setattr(canonical, 'operation_spec', lambda tool, op: {**original(tool, op), 'runtime_id': 'email.list'}
                            if tool == 'email.send' else original(tool, op))
    elif changed == 'native':
        exporter = next(tool for tool in registry.TOOLS if tool['id'] == 'export_result')
        monkeypatch.setitem(exporter, 'execution_status', 'registered')
    state = source()
    transport = Fake([existing(state)])
    assess_capability(state, transport)
    assert transport.calls == 1
