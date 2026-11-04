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


@pytest.mark.parametrize('request_text', ['첨부한 CSV의 모든 행과 열을 그대로 유지해서 JSON 파일로 저장해줘.',
                                         '첨부한 CSV를 JSON 파일로 저장해줘.',
                                         '첨부한 CSV를 JSON 파일로 만들어줘.',
                                         '첨부한 CSV를 JSON으로 내보내줘.'])
def test_read_only_draft_cannot_be_approved_for_json_export(request_text):
    from agent_builder.design.nodes.validation import validate_node
    s = supported(request_text)
    s['resources'] = [{'id': 'asset-1', 'kind': 'table', 'name': 'sample.csv', 'columns': ['id']}]
    s['capability_fingerprint'] = input_fingerprint(s)
    d = draft()
    d['requirements'][0]['quote'] = s['messages'][0]['content']
    d['tool_ids'] = ['read_table']
    d['workflow']['nodes'] = d['workflow']['nodes'][:1]
    d['workflow']['edges'] = []
    result = validate_node({**s, 'draft': d})
    assert result['status'] != 'ready'
    assert any('JSON 결과를 저장' in issue for issue in result['issues'])


def test_multiple_report_formats_require_a_choice():
    from agent_builder.design.nodes.validation import validate_node

    request = '첨부한 표를 CSV로 저장하고 JSON으로 저장해줘.'
    s = supported(request)
    s['resources'] = [{'id': 'asset-1', 'kind': 'table', 'name': 'sample.csv', 'columns': ['id']}]
    s['capability_fingerprint'] = input_fingerprint(s)
    d = draft()
    d['requirements'][0]['quote'] = request
    result = validate_node({**s, 'draft': d})
    assert result['status'] != 'ready'
    assert any('CSV와 JSON 중' in question for question in result['draft']['questions'])


def test_report_format_change_resolves_previous_choice():
    from agent_builder.design.output_policy import requested_report_formats

    assert requested_report_formats([
        'CSV로 저장하고 JSON으로 저장해줘.',
        'CSV로 저장하지 말고 JSON 파일로 만들어줘.',
    ]) == {'json'}

    assert requested_report_formats([
        'CSV로 저장하고 JSON으로 저장해줘.',
        'JSON으로',
    ]) == {'json'}


@pytest.mark.parametrize('paths, expected', [
    ([('save', 'end')], True),
    ([('save', 'end'), ('end',)], False),
    ([('save', 'end'), ('save_csv', 'save', 'end')], True),
    ([('save', 'save_csv', 'end')], False),
])
def test_report_contract_checks_last_artifact_on_each_path(paths, expected):
    from workflow_engine.catalog.output_contracts import final_report_matches
    from tests.design.test_direct_conversation import node, edge

    nodes = [node('start', 'condition')]
    available = {'save': node('save', 'write_result', format='json'),
                 'save_csv': node('save_csv', 'write_result', format='csv'),
                 'end': node('end', 'finish')}
    edges = []
    for path in paths:
        for name in path:
            if available[name] not in nodes:
                nodes.append(available[name])
        for source, target in zip(('start', *path), path):
            link = edge(source, target)
            if link not in edges:
                edges.append(link)
    assert final_report_matches({'workflow': {'entry': 'start', 'nodes': nodes, 'edges': edges}}, 'json') is expected


def test_negating_one_format_preserves_the_other():
    from agent_builder.design.output_policy import requested_report_formats

    assert requested_report_formats([
        'CSV로 저장하고 JSON으로 저장해줘.',
        'CSV로 저장하지 마.',
    ]) == {'json'}


def test_legacy_draft_needs_writer_in_requested_format():
    from agent_builder.design.nodes.validation import validate_node

    s = supported('첨부한 CSV를 JSON 파일로 저장해줘.')
    s['resources'] = [{'id': 'asset-1', 'kind': 'table', 'name': 'sample.csv', 'columns': ['id']}]
    s['capability_fingerprint'] = input_fingerprint(s)
    d = draft()
    d['requirements'][0]['quote'] = s['messages'][0]['content']
    d.update(workflow=None, tool_ids=['read_table'])
    assert validate_node({**s, 'draft': d})['status'] != 'ready'
    d['tool_ids'].append('write_result')
    d['execution_plan']['output_format'] = 'csv'
    assert validate_node({**s, 'draft': d})['status'] != 'ready'
    d['execution_plan']['output_format'] = 'json'
    assert validate_node({**s, 'draft': d})['status'] == 'ready'
