import pytest
from agent_builder.design.planner_constraints import passthrough_format
from agent_builder.capabilities.capability_gate import input_fingerprint
from agent_builder.conditions.condition_check import make_condition_check
from tests.conditions.test_condition_readiness import state
from tests.design.test_direct_conversation import Fake, draft


def supported(text='첨부한 CSV의 모든 행과 열을 그대로 유지해서 JSON 파일로 저장해줘.'):
    s = state(text)
    s['capability_assessment'] = {'decision': 'existing_tools', 'tool_ids': ['read_table', 'write_result']}
    s['capability_enforced'] = True
    s['capability_fingerprint'] = input_fingerprint(s)
    return s


def test_whole_table_serialization_delegates_without_condition_model():
    s = supported()
    assert passthrough_format(s) == 'json'
    r = make_condition_check(mode='enforce', transport=object())(s)
    assert r['condition_route'] == 'conversation'
    assert not r['condition_enforced']


@pytest.mark.parametrize('extra', [' 비용 100만원 초과만 선별해.', ' 합계를 계산해.', ' 빈 행은 제거해.',
    ' 이메일로 보내줘.', ' S3에 저장해.', ' reports/custom 경로로 저장해.', ' 매주 예약해.',
    ' 중복을 유지해.', ' 정렬하지 마.', ' 이름을 익명화해.', ' 비밀번호를 마스킹해.'])
def test_compound_or_custom_destination_is_not_scoped(extra):
    assert passthrough_format(supported('CSV를 그대로 JSON으로 저장해.' + extra)) is None


def test_stale_or_missing_capability_verdict_cannot_bypass_conditions():
    s = supported(); s['messages'].append({'role': 'user', 'content': '변경'})
    assert passthrough_format(s) is None
    s = supported(); s['capability_assessment']['decision'] = 'manual_review'
    assert passthrough_format(s) is None


def test_extra_filter_advice_does_not_change_explicit_whole_table_intent():
    s = supported()
    s['capability_assessment']['tool_ids'].append('filter_sort')
    assert passthrough_format(s) == 'json'


@pytest.mark.parametrize('version', ['v1', 'v6', 'v8'])
def test_native_serialization_schema_cannot_mix_operation_ports(monkeypatch, version):
    from agent_builder.design.nodes.direct_conversation import make_direct_conversation
    monkeypatch.setenv('A4B_PLANNER_PROMPT', version)
    monkeypatch.setenv('A4B_TOOL_ADVICE_MODE', 'off')
    s = supported(); s['node_library'] = []
    fake = Fake(draft())
    make_direct_conversation('', 'fixture', model_factory=lambda: fake)(s)
    schema = fake.schema; defs = schema['$defs']
    assert defs['NodeSettings']['properties']['operation'] == {'type': 'null'}
    assert schema['properties']['tool_ids']['items']['enum'] == ['read_table', 'write_result', 'finish']
    assert defs['NativeNode_write_result']['properties']['settings']['properties']['format']['enum'] == ['json']
    assert schema['properties']['questions']['maxItems'] == 0
    assert schema['properties']['workflow'] == {'$ref': '#/$defs/WorkflowDefinition'}
    assert defs['NativeNode_read_table']['properties']['id']['enum'] == ['read_table_1']
    assert defs['WorkflowDefinition']['properties']['entry']['enum'] == ['read_table_1']
    assert defs['NodeSettings']['properties']['node_ref'] == {'type': 'null'}
    assert defs['Requirement']['properties']['quote']['enum'] == [s['messages'][0]['content']]


def test_deep_agent_uses_only_native_serialization_contracts(monkeypatch):
    from agent_builder.design.nodes.conversation import make_conversation
    from tests.design.test_deep_agent_conversation import draft as response
    captured = {}
    def factory(**kwargs):
        captured['schema'] = kwargs['response_format'].schema
        class Agent:
            def invoke(self, payload, config):
                return {'structured_response': response(questions=['확인해 주세요'])}
        return Agent()
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE', 'direct')
    monkeypatch.setenv('A4B_TOOL_ADVICE_MODE', 'off')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', factory)
    s = supported(); s['node_library'] = []
    make_conversation('', 'fixture', model_factory=lambda: object())(s)
    schema = captured['schema']
    variants = schema['$defs']['WorkflowNode']['anyOf']
    assert {v['properties']['tool']['enum'][0] for v in variants} == {'read_table', 'write_result', 'finish'}
    for variant in variants:
        settings = variant['properties']['settings']['properties']
        assert 'operation' not in settings and 'arguments_json' not in settings
        if variant['properties']['tool']['enum'] == ['write_result']:
            assert settings['format']['enum'] == ['json']
    assert schema['properties']['questions']['maxItems'] == 0
