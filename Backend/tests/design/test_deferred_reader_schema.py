"""Execution-time files must be usable in the first Deep Agent response."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.runtime_inputs import runtime_file_question_issues
from tests.design.test_deep_agent_conversation import draft, state


def deferred(request):
    result = state([{'role': 'user', 'content': request},
                    {'role': 'user', 'content': '파일은 실행할 때 넣을게요'}])
    result['capability_assessment'] = {
        'decision': 'existing_tools', 'tool_ids': ['compose_answer', 'ask_user']}
    return result


def generate(monkeypatch, original, answer=None, inspect_reader=None):
    captured = {}
    answer = answer or draft(questions=['검토할 업무 기준을 알려주세요.'])
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setenv('A4B_PLANNER_MODE', 'deepagent')
    monkeypatch.setenv('A4B_REQUIREMENT_GENERATION_MODE', 'baseline')

    def factory(**options):
        captured['schema'] = deepcopy(options['response_format'].schema)
        # Validate against the actual first response format, before any tool call.
        Draft202012Validator(captured['schema']).validate(answer)

        class Agent:
            def invoke(self, payload, config):
                captured['context'] = json.loads(payload['messages'][0]['content'].split('\n', 1)[1])
                if inspect_reader:
                    tools = {tool.name: tool for tool in options['tools']}
                    tools['inspect_tool'].invoke({'tool_id': inspect_reader})
                    class Request:
                        tools = options['tools']

                        def override(self, **kwargs):
                            return SimpleNamespace(**kwargs)

                    def handler(request):
                        captured['inspected_schema'] = deepcopy(request.response_format.schema)
                        return SimpleNamespace(result=[])

                    options['middleware'][0].wrap_model_call(Request(), handler)
                return {'structured_response': answer}
        return Agent()

    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', factory)
    captured['result'] = make_conversation('', 'test', model_factory=lambda: object())(original)
    return captured


def reader_ids(variants):
    return {tool for variant in variants for tool in variant['properties']['tool']['enum']
            if tool in ('read_document', 'read_table')}


@pytest.mark.parametrize('prompt,expected', [
    ('PDF 내용을 검토해줘.', {'read_document'}),
    ('CSV 파일의 금액을 확인해줘.', {'read_table'}),
    ('계약서 PDF와 CSV 파일을 함께 확인해줘.', {'read_document', 'read_table'}),
    ('파일 내용을 확인해줘.', set()),
    ('PDF 문서 내용을 검토하고 결과는 CSV로 저장해줘.', {'read_document'}),
    ('CSV 파일을 확인하고 결과는 PDF 파일로 저장해줘.', {'read_table'}),
    ('파일 내용을 확인하고 결과는 CSV로 저장해줘.', set()),
    ('contract.docx 내용을 검토해줘.', {'read_document'}),
    ('sales.xlsx 내용을 확인해줘.', {'read_table'}),
    ('Excel 파일 내용을 확인해줘.', {'read_table'}),
])
def test_first_schema_and_selected_contracts_include_only_known_input_readers(monkeypatch, prompt, expected):
    captured = generate(monkeypatch, deferred(prompt))
    definitions = captured['schema']['$defs']
    for name in ('WorkflowNode', 'FirstBusinessStep'):
        assert reader_ids(definitions[name]['anyOf']) == expected
    contracts = captured['context']['selected_tool_contracts']
    assert {c['id'] for c in contracts} & {'read_document', 'read_table'} == expected
    for variant in definitions['WorkflowNode']['anyOf']:
        if not reader_ids([variant]):
            continue
        settings = variant['properties']['settings']['properties']
        assert settings['file_ids']['maxItems'] == 0
        assert not settings.get('operation', {}).get('enum')
        assert variant['properties']['bindings']['maxItems'] == 0


def test_pdf_workflow_is_generated_without_inspecting_any_tool(monkeypatch):
    original = deferred('계약서 PDF 내용을 검토하고 사람이 결과를 확인하게 해줘.')
    answer = draft(workflow={
        'first_step': {'tool': 'read_document', 'label': '계약서 읽기',
                       'settings': {'file_ids': [], 'node_ref': None}},
        'next_steps': [
            {'tool': 'compose_answer', 'label': '계약서 검토',
             'settings': {'instructions': '계약 내용을 근거와 함께 검토하세요.', 'node_ref': None},
             'bindings': [{'input': 'documents', 'step': 1, 'output': 'documents'}]},
            {'tool': 'ask_user', 'label': '사람 확인',
             'settings': {'question': '검토 결과를 확인하셨나요?', 'instructions': None, 'node_ref': None},
             'bindings': [{'input': 'text', 'step': 2, 'output': 'text'}]}]})
    answer['tool_ids'] = ['read_document', 'compose_answer', 'ask_user']
    captured = generate(monkeypatch, original, answer)
    checked = validate_node({**original, **captured['result']})
    assert checked['status'] == 'ready' and checked['issues'] == []
    nodes = captured['result']['draft']['workflow']['nodes']
    assert [n['tool'] for n in nodes] == answer['tool_ids']
    assert nodes[0]['settings']['file_ids'] == []
    assert nodes[0]['settings']['operation'] is None
    forged = deepcopy(answer)
    forged['workflow']['first_step']['settings']['file_ids'] = ['invented-file']
    assert not Draft202012Validator(captured['schema']).is_valid(forged)


@pytest.mark.parametrize('spec,expected', [
    ({'kind': 'document_analysis'}, {'read_document'}),
    ({'kind': 'compute', 'formula': '수량*단가', 'output_column': '금액'}, {'read_table'}),
])
def test_sealed_requirements_supply_input_kind_but_stale_requirements_do_not(monkeypatch, spec, expected):
    original = deferred('파일 내용을 확인해줘.')
    plan = extract_plan({'tasks': [{'source_message': 0, 'description': '자료 확인', 'spec': spec}],
                         'unresolved': []}, original)
    original.update(requirement_required=True, requirement_ledger=plan,
                    requirement_fingerprint=ledger_fingerprint(original), requirement_digest=plan_digest(plan))
    assert reader_ids(generate(monkeypatch, original)['schema']['$defs']['WorkflowNode']['anyOf']) == expected
    original['requirement_digest'] = 'stale'
    assert reader_ids(generate(monkeypatch, original)['schema']['$defs']['WorkflowNode']['anyOf']) == set()


def test_assistant_suggestion_and_cancelled_deferral_do_not_add_readers(monkeypatch):
    original = deferred('파일 내용을 확인해줘.')
    original['messages'].insert(1, {'role': 'assistant', 'content': 'PDF와 CSV를 읽겠습니다.'})
    assert not reader_ids(generate(monkeypatch, original)['schema']['$defs']['WorkflowNode']['anyOf'])
    original = deferred('PDF 내용을 확인해줘.')
    original['messages'].append({'role': 'user', 'content': '파일은 지금 올릴게요'})
    assert not reader_ids(generate(monkeypatch, original)['schema']['$defs']['WorkflowNode']['anyOf'])


@pytest.mark.parametrize('capability', [None, {'decision': 'existing_tools', 'tool_ids': []}])
def test_missing_tool_selection_keeps_the_full_catalog(monkeypatch, capability):
    original = deferred('PDF 내용을 검토해줘.')
    original['capability_assessment'] = capability
    captured = generate(monkeypatch, original)
    assert 'selected_tool_contracts' not in captured['context']
    variants = captured['schema']['$defs']['WorkflowNode']['anyOf']
    tools = {tool for variant in variants for tool in variant['properties']['tool']['enum']}
    assert {'read_document', 'read_table', 'compose_answer', 'ask_user', 'filter_sort', 'aggregate_table'} <= tools


def test_existing_resources_keep_their_actual_reader_and_file_ids(monkeypatch):
    original = deferred('CSV 내용을 확인해줘.')
    original['resources'] = [{'id': 'actual-document', 'name': 'contract.pdf', 'kind': 'document', 'text': '계약 내용'}]
    captured = generate(monkeypatch, original)
    assert 'runtime_file_input' not in captured['context']
    variants = captured['schema']['$defs']['WorkflowNode']['anyOf']
    assert reader_ids(variants) == {'read_document'}
    reader = next(v for v in variants if reader_ids([v]))
    assert reader['properties']['settings']['properties']['file_ids']['items']['enum'] == ['actual-document']


def test_inspection_cannot_reintroduce_reader_operations_that_need_existing_assets(monkeypatch):
    captured = generate(monkeypatch, deferred('PDF 내용을 확인해줘.'), inspect_reader='read_document')
    for name in ('schema', 'inspected_schema'):
        definitions = captured[name]['$defs']
        assert reader_ids(definitions['FirstBusinessStep']['anyOf']) == {'read_document'}
        readers = [v for v in definitions['WorkflowNode']['anyOf'] if reader_ids([v])]
        assert len(readers) == 1
        assert not readers[0]['properties']['settings']['properties'].get('operation', {}).get('enum')


@pytest.mark.parametrize('question', [
    '어떤 DOCX를 사용하시나요?',
    '어느 XLSX를 처리할까요?',
    'Excel을 선택해 주세요.',
])
def test_explicit_file_formats_cannot_be_requested_again(question):
    assert runtime_file_question_issues({'questions': [question]}, deferred('파일 내용을 확인해줘.'))


@pytest.mark.parametrize('question', [
    'DOCX에서 검토할 기준을 알려주세요.',
    'XLSX에서 집계할 열을 선택해 주세요.',
    'Excel 파일 처리 규칙은 무엇인가요?',
])
def test_explicit_file_formats_still_allow_business_questions(question):
    assert not runtime_file_question_issues({'questions': [question]}, deferred('파일 내용을 확인해줘.'))
