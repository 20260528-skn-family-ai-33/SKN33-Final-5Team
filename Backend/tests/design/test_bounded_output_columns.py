"""A bounded request must leave room for registered table/record adapters."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from agent_builder.design.native_settings import NATIVE_SETTINGS
from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_generation import generation_requirements
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from infrastructure.persistence.storage import Storage
from tests.design.test_deep_agent_conversation import draft, state
from workflow_engine.executors.dispatch import execute_node


def sealed_state(*, fill=False, rename=False):
    column = 'amount' if rename else 'qty'
    request = ('첨부 CSV의 qty 빈값은 0으로 채우는 조건을 사용할 수 있습니다. '
               '요청한 이름과 열 순서대로 JSON으로 저장해줘.\n제공하는 실행 조건: '
               + json.dumps(dict(output_columns=[column], output_format='json')))
    original = state([dict(role='user', content=request)])
    original['resources'] = [dict(id='source', name='input.csv', kind='table', columns=['qty', 'other'])]
    original['capability_assessment'] = dict(decision='existing_tools', tool_ids=['filter_sort', 'write_result'])
    specs = [dict(kind='read', file_ids=['source'])]
    if fill:
        specs.append(dict(kind='fill_missing', fields=['qty'], value=0))
    if rename:
        specs.append(dict(kind='rename', source='qty', target='amount'))
    specs += [dict(kind='select_fields', fields=[column]), dict(kind='export', format='json')]
    plan = extract_plan(dict(tasks=[dict(source_message=0, description=s['kind'], spec=s) for s in specs],
                             unresolved=[]), original)
    original.update(requirement_required=True, requirement_ledger=plan,
                    requirement_fingerprint=ledger_fingerprint(original), requirement_digest=plan_digest(plan))
    return original


def native(tool, **values):
    return {**{key: [] if key in ('file_ids', 'fields') else None for key in NATIVE_SETTINGS[tool]},
            'node_ref': None, **values}


def answer_for(original, *, fill=False, rename=False, omit_select=False):
    first = dict(tool='read_table', label='표 읽기', settings=native('read_table', file_ids=['source']))
    steps = []
    parent = 1

    def append(tool, settings, input_port, output_port):
        nonlocal parent
        steps.append(dict(tool=tool, label=tool, settings=settings,
                          bindings=[dict(input=input_port, step=parent, output=output_port)]))
        parent += 1

    if fill:
        append('filter_sort', dict(operation='handle_missing',
                                  arguments_json=dict(columns=['qty'], action='fill', fill_value=0),
                                  output_port=None), 'table', 'table')
    if rename:
        append('calculate_columns', native('calculate_columns', table_operation='rename',
                                           rename_from='qty', rename_to='amount'), 'table', 'table')
    append('json_transform', dict(operation='lookup', arguments_json=dict(path=['rows']),
                                  output_port='records'), 'value', 'table')
    if not omit_select:
        append('filter_sort', dict(operation='select_columns',
                                  arguments_json=dict(columns=['amount' if rename else 'qty']),
                                  output_port=None), 'rows', 'records')
    append('export_result', native('export_result', format='json'), 'records', 'records')
    answer = draft(workflow=dict(first_step=first, next_steps=steps))
    answer['document_ids'] = ['source']
    answer['tool_ids'] = list(dict.fromkeys([first['tool'], *[s['tool'] for s in steps]]))
    answer['instructions'] = original['messages'][0]['content']
    answer['execution_plan']['output_format'] = 'json'
    return answer


def generate(monkeypatch, original, answer):
    captured = {}
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setenv('A4B_PLANNER_MODE', 'deepagent')
    monkeypatch.setenv('A4B_REQUIREMENT_GENERATION_MODE', 'bounded')
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')

    def factory(**options):
        captured['initial'] = deepcopy(options['response_format'].schema)
        assert captured['initial']['$defs']['WorkflowDefinition']['properties']['next_steps']['type'] == 'array'

        class Agent:
            def invoke(self, payload, config):
                tools = {tool.name: tool for tool in options['tools']}
                for identifier in ('json_transform', 'export_result'):
                    tools['inspect_tool'].invoke({'tool_id': identifier})

                class Request:
                    tools = options['tools']

                    def override(self, **kwargs):
                        return SimpleNamespace(**kwargs)

                def handler(request):
                    captured['expanded'] = deepcopy(request.response_format.schema)
                    return SimpleNamespace(result=[])

                options['middleware'][0].wrap_model_call(Request(), handler)
                Draft202012Validator(captured['expanded']).validate(answer)
                return dict(structured_response=answer)
        return Agent()

    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', factory)
    captured['result'] = make_conversation('', 'test', model_factory=lambda: object())(original)
    return captured


@pytest.mark.parametrize('fill,rename', [(False, False), (True, False), (False, True), (True, True)])
def test_bounded_projection_can_generate_validate_and_execute_with_required_adapters(monkeypatch, tmp_path, fill, rename):
    original = sealed_state(fill=fill, rename=rename)
    specs, order = generation_requirements(original)
    assert specs == [task['spec'] for task in original['requirement_ledger']['tasks']]
    assert order == []
    captured = generate(monkeypatch, original, answer_for(original, fill=fill, rename=rename))
    checked = validate_node({**original, **captured['result']})
    assert checked['status'] == 'ready', checked['issues']
    config = captured['result']['draft']
    execution = dict(outputs={}, file_ids=['source'], external_inputs={})
    storage = Storage(tmp_path)
    try:
        storage.add_asset(original['resources'][0], [dict(qty=None, other='not requested'), dict(qty=2, other='extra')])
        for current in config['workflow']['nodes']:
            execution['outputs'][current['id']] = execute_node(current, execution, storage, config, None)
        artifact = execution['outputs'][config['workflow']['nodes'][-1]['id']]['artifact']
    finally:
        storage.close()
    column = 'amount' if rename else 'qty'
    assert json.loads(artifact['content']) == [{column: 0 if fill else None}, {column: 2}]


def test_projection_fallback_does_not_waive_required_selection(monkeypatch):
    original = sealed_state()
    captured = generate(monkeypatch, original, answer_for(original, omit_select=True))
    checked = validate_node({**original, **captured['result']})
    assert checked['status'] != 'ready'
    assert any('select_fields' in issue or '출력 열' in issue for issue in checked['issues'])


def test_projection_after_join_keeps_source_and_join_constraints(monkeypatch, tmp_path):
    original = sealed_state()
    original['messages'][0]['content'] = ('두 표를 item 기준 left/many_to_one으로 결합하고 null 키는 오류, '
        '최대 100행으로 제한한 뒤 qty만 JSON으로 저장해줘.\n'
        '제공하는 실행 조건: {"output_columns":["qty"],"output_format":"json"}')
    original['resources'] = [dict(id='source', name='orders.csv', kind='table', columns=['item', 'qty']),
                             dict(id='reference', name='prices.csv', kind='table', columns=['item', 'price'])]
    join = dict(kind='join', keys=['item'], how='left', cardinality='many_to_one',
                null_keys='error', max_rows=100, left_sources=['source'], right_sources=['reference'])
    specs = [dict(kind='read', file_ids=['source']), dict(kind='read', file_ids=['reference']),
             join, dict(kind='select_fields', fields=['qty']), dict(kind='export', format='json')]
    plan = extract_plan(dict(tasks=[dict(source_message=0, description=s['kind'], spec=s) for s in specs],
                             unresolved=[]), original)
    original.update(requirement_ledger=plan, requirement_fingerprint=ledger_fingerprint(original),
                    requirement_digest=plan_digest(plan))
    answer = answer_for(original)
    steps = answer['workflow']['next_steps']
    for step in steps:
        step['bindings'][0]['step'] += 2
    read = dict(tool='read_table', label='오른쪽 표', settings=native('read_table', file_ids=['reference']), bindings=[])
    joined = dict(tool='join_tables', label='결합', settings=dict(operation='join', output_port=None,
        arguments_json={key: join[key] for key in ('keys', 'how', 'cardinality', 'null_keys', 'max_rows')}),
        bindings=[dict(input='left', step=1, output='table'), dict(input='right', step=2, output='table')])
    answer['workflow']['next_steps'] = [read, joined, *steps]
    answer['tool_ids'].append('join_tables')
    captured = generate(monkeypatch, original, answer)
    checked = validate_node({**original, **captured['result']})
    assert checked['status'] == 'ready', checked['issues']
    join_variant = next(v for v in captured['expanded']['$defs']['WorkflowNode']['anyOf']
                        if v['properties']['tool'].get('enum') == ['join_tables'])
    args = join_variant['properties']['settings']['properties']['arguments_json']
    assert not Draft202012Validator(args).is_valid({**joined['settings']['arguments_json'], 'how': 'inner'})
    assert not Draft202012Validator(args).is_valid({**joined['settings']['arguments_json'], 'left': {'rows': []}})
    storage = Storage(tmp_path)
    config = captured['result']['draft']
    execution = dict(outputs={}, file_ids=['source', 'reference'], external_inputs={})
    try:
        storage.add_asset(original['resources'][0], [dict(item='A', qty=1), dict(item='B', qty=2)])
        storage.add_asset(original['resources'][1], [dict(item='A', price=100), dict(item='B', price=200)])
        for current in config['workflow']['nodes']:
            execution['outputs'][current['id']] = execute_node(current, execution, storage, config, None)
        artifact = execution['outputs'][config['workflow']['nodes'][-1]['id']]['artifact']
        assert json.loads(artifact['content']) == [dict(qty=1), dict(qty=2)]
    finally:
        storage.close()


def test_native_table_only_requests_keep_their_bounded_order():
    from tests.design.test_requirement_coverage import joined
    assert generation_requirements(joined())[1] == [
        'read_table', 'read_table', 'join_tables', 'calculate_columns',
        'aggregate_table', 'filter_sort', 'write_result']
