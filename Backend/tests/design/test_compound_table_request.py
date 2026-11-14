"""T06/T08: original kit inputs, adversarial plans, and persisted CSV execution.

Model transports are controlled. These tests prove backend behavior, not live
provider reliability. Expected CSVs are independent manual-kit oracles.
"""
import base64
import csv
import io
import json
from copy import deepcopy
from pathlib import Path

import pytest

from agent_builder.conditions.condition_check import make_condition_check
from agent_builder.design.compound_table_request import compound_table_request, request_specs
from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_ledger import extract_plan, make_requirement_check
from contracts.models import AgentDraft, ExecutionPlan
from infrastructure.persistence.storage import Storage
from langgraph.types import Command
from service.worker import handle
from tests.design.test_builder import initial, draft
from tests.workflow_engine.test_workflow import node, bind, edge


FIXTURES = Path(__file__).parents[1] / 'fixtures' / 'compound_table'


class Forbidden:
    def invoke(self, messages):
        raise AssertionError('A fully grounded compound request must not be flattened by a model')


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def case_state(case):
    folder = FIXTURES / case
    source = next(p for p in folder.glob('*.csv') if p.name != 'expected.csv')
    columns, rows = read_csv(source)
    state = initial()
    state.update(messages=[dict(role='user', content=(folder / 'request.txt').read_text(encoding='utf-8-sig').strip())],
                 resources=[dict(id='table-source', name=source.name, kind='table', format='csv', columns=columns)])
    return state, rows


def config_for(state):
    request = compound_table_request(state)
    assert request is not None
    text = state['messages'][0]['content']
    config = draft()
    config.update(name='복합 조건 회귀 검증', instructions=text, document_ids=['table-source'],
                  requirements=[dict(description='요청 전체 보존', source_id='user', quote=text)])
    config['execution_plan'].update(missing_policy='keep')
    nodes = [node('read', 'read_table', file_ids=['table-source'])]
    for i, spec in enumerate([*request['filters'], dict(sort=request['sort'])]):
        plan = ExecutionPlan(filters=spec.get('filters', []), sort=spec.get('sort', []),
                             match=spec.get('match', 'all'), missing_policy='keep', duplicate_policy='keep',
                             duplicate_field=None, output_format='json').model_dump()
        nodes.append(node(f'filter{i}', 'filter_sort', [bind('table', nodes[-1]['id'], 'table')], execution_plan=plan))
    nodes.extend([
        node('rows', 'json_transform', [bind('value', nodes[-1]['id'], 'table')],
             operation='lookup', arguments_json=json.dumps(dict(path=['rows'])), output_port='records'),
        node('select', 'filter_sort', [bind('rows', 'rows', 'records')],
             operation='select_columns', arguments_json=json.dumps(dict(columns=request['columns']))),
        node('write', 'export_result', [bind('rows', 'select', 'records')],
             operation='write_csv', arguments_json=json.dumps(dict(columns=request['columns'], path=request['path'])))])
    config['tool_ids'] = list(dict.fromkeys(n['tool'] for n in nodes))
    config['workflow'] = dict(entry='read', nodes=nodes, edges=[edge(a['id'], b['id']) for a, b in zip(nodes, nodes[1:])])
    return AgentDraft.model_validate(config).model_dump()


@pytest.mark.parametrize('case', ['T06', 'T08'])
def test_condition_scope_and_requirement_extraction_preserve_groups(case):
    state, _ = case_state(case)
    condition = make_condition_check(mode='enforce', transport=Forbidden())(state)
    assert condition['condition_route'] == 'conversation'
    assert not condition['condition_enforced']
    update = make_requirement_check('unused', mode='enforce', transport=Forbidden())({**state, **condition})
    assert update['requirement_required']
    assert update['requirement_route'] == 'plan'
    assert update['trace'][-1]['model_calls'] == 0
    specs = [t['spec'] for t in update['requirement_ledger']['tasks']]
    assert specs == request_specs(compound_table_request(state))
    assert all(t['quote'] == state['messages'][0]['content'] for t in update['requirement_ledger']['tasks'])


def decode_artifact(run):
    assert run['status'] == 'succeeded', run.get('error')
    artifact = run['artifact']
    text = (base64.b64decode(artifact['content']).decode('utf-8-sig')
            if artifact.get('encoding') == 'base64' else artifact['content'].lstrip('\ufeff'))
    reader = csv.DictReader(io.StringIO(text))
    return reader.fieldnames, list(reader)


@pytest.mark.parametrize('case', ['T06', 'T08'])
def test_graph_confirm_reopen_execute_matches_entire_manual_kit_csv(case, tmp_path, monkeypatch):
    monkeypatch.setenv('A4B_ORCHESTRATION_MODE', 'off')
    state, rows = case_state(case)
    config = config_for(state)
    store = Storage(tmp_path)
    store.add_asset(state['resources'][0], rows)
    cfg = {'configurable': {'thread_id': state['session_id']}}
    def graph_for(storage):
        return build_graph(storage, conversation=lambda s: dict(draft=deepcopy(config)),
                           condition_mode='enforce', condition_transport=Forbidden(),
                           requirement_mode='enforce', requirement_transport=Forbidden(),
                           capability_mode='off', verifier_mode='off')
    graph, connection = graph_for(store)
    try:
        ready = graph.invoke(state, cfg)
        assert ready['status'] == 'ready', ready.get('issues')
        assert ready['__interrupt__'][0].value['kind'] == 'review'
        assert not store.agents()
    finally:
        connection.close()
        store.close()
    store = Storage(tmp_path)
    graph, connection = graph_for(store)
    try:
        saved = graph.invoke(Command(resume={'type': 'confirm'}), cfg)
        assert saved['status'] == 'saved', saved.get('issues')
        identifier = saved['saved_agent_id']
    finally:
        connection.close()
        store.close()
    result = handle(dict(directory=str(tmp_path), action='test', id=identifier, content=''))
    assert result['artifact']['name'] == compound_table_request(state)['path']
    assert decode_artifact(result) == read_csv(FIXTURES / case / 'expected.csv')


@pytest.mark.parametrize('case', ['T06', 'T08'])
@pytest.mark.parametrize('damage', ['missing_filter', 'inverted_group', 'missing_policy', 'dedup', 'bypass',
                                   'wrong_sort', 'wrong_columns', 'wrong_filename', 'literal_rows'])
@pytest.mark.parametrize('ledger', [False, True])
def test_corrupt_plans_are_rejected_even_without_requirement_ledger(case, damage, ledger, monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state, _ = case_state(case)
    config = config_for(state)
    if ledger:
        state.update(make_requirement_check('unused', mode='enforce', transport=Forbidden())(state))
    nodes = config['workflow']['nodes']
    plan = nodes[1]['settings']['execution_plan']
    if damage == 'missing_filter':
        plan['filters'].pop()
    elif damage == 'inverted_group':
        plan['match'] = 'any' if plan['match'] == 'all' else 'all'
    elif damage == 'missing_policy':
        plan['missing_policy'] = 'exclude'
    elif damage == 'dedup':
        plan.update(duplicate_policy='first', duplicate_field='번호')
    elif damage == 'bypass':
        nodes[-3]['bindings'] = [bind('value', 'read', 'table')]
    elif damage == 'wrong_sort':
        nodes[-4]['settings']['execution_plan']['sort'][0]['direction'] = 'descending'
    elif damage == 'wrong_columns':
        nodes[-1]['settings']['arguments_json'] = json.dumps(dict(columns=['번호'], path=compound_table_request(state)['path']))
    elif damage == 'wrong_filename':
        args = json.loads(nodes[-1]['settings']['arguments_json'])
        args['path'] = 'wrong.csv'
        nodes[-1]['settings']['arguments_json'] = json.dumps(args)
    else:
        args = json.loads(nodes[-1]['settings']['arguments_json'])
        args['rows'] = [{'번호': 'fake'}]
        nodes[-1]['settings']['arguments_json'] = json.dumps(args)
    result = validate_node({**state, 'draft': config})
    assert result['status'] != 'ready'
    assert any('최종 CSV' in issue for issue in result['issues']), result['issues']


@pytest.mark.parametrize('case', ['T06', 'T08'])
def test_corrupt_requirement_group_cannot_be_sealed(case):
    state, _ = case_state(case)
    specs = request_specs(compound_table_request(state))
    specs[1]['match'] = 'any' if specs[1]['match'] == 'all' else 'all'
    raw = dict(tasks=[dict(source_message=0, description=s['kind'], spec=s) for s in specs], unresolved=[])
    with pytest.raises(ValueError, match='OR/AND'):
        extract_plan(raw, state)


@pytest.mark.parametrize('case', ['T06', 'T08'])
@pytest.mark.parametrize('change', ['extra_clause', 'quoted', 'followup', 'unknown_column', 'two_sources'])
def test_partial_or_revised_requests_keep_general_owner(case, change):
    state, _ = case_state(case)
    if change == 'extra_clause':
        state['messages'][0]['content'] += ' 그리고 요약도 해줘.'
    elif change == 'quoted':
        state['messages'][0]['content'] = '이 예시를 설명해줘: "' + state['messages'][0]['content'] + '"'
    elif change == 'followup':
        state['messages'].append(dict(role='user', content='그 조건을 적용하지 마.'))
    elif change == 'unknown_column':
        state['resources'][0]['columns'].remove('금액')
    else:
        state['resources'].append({**state['resources'][0], 'id': 'other'})
    assert compound_table_request(state) is None


@pytest.mark.parametrize('case', ['T06', 'T08'])
def test_renamed_files_fields_and_sort_direction_are_source_grounded(case):
    state, _ = case_state(case)
    asset = state['resources'][0]
    translations = {asset['name']: '새자료.csv', '번호': '식별자', '금액': '비용', '오름차순': '내림차순'}
    for old, new in translations.items():
        state['messages'][0]['content'] = state['messages'][0]['content'].replace(old, new)
        asset['name'] = asset['name'].replace(old, new)
        asset['columns'] = [c.replace(old, new) for c in asset['columns']]
    contract = compound_table_request(state)
    assert contract['sort'][0] == dict(field='식별자', direction='descending', value_type='text')
    assert any(f['field'] == '비용' for group in contract['filters'] for f in group['filters'])


def test_decoder_keeps_each_or_group_as_a_separate_stage(monkeypatch):
    from jsonschema import Draft202012Validator
    from tests.design.test_beta_feedback import native_schema
    from agent_builder.design.operation_steps import schema_for
    from agent_builder.design.requirement_generation import required_operation_variants
    from agent_builder.design.projected_table_decoder import constrain_projected_table
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    state, _ = case_state('T08')
    state.update(make_requirement_check('unused', transport=Forbidden(), mode='enforce')(state))
    specs = [t['spec'] for t in state['requirement_ledger']['tasks']]
    base = native_schema()
    original = AgentDraft.model_json_schema()
    base['$defs']['WorkflowNode']['anyOf'] += required_operation_variants(specs, original['$defs']['WorkflowNode'],
                                                                       original['$defs']['Binding'], set(state['resources'][0]['columns']))
    schema = constrain_projected_table(schema_for(base), state)
    slots = schema['$defs']['WorkflowDefinition']['properties']['next_steps']['properties']
    assert len(slots) == 7
    for step, match, count in [('step_2', 'any', 2), ('step_3', 'any', 2), ('step_4', 'all', 2)]:
        settings = slots[step]['anyOf'][0]['properties']['settings']['properties']
        plan = config_for(state)['workflow']['nodes'][int(step[-1])-1]['settings']['execution_plan']
        assert plan['match'] == match and len(plan['filters']) == count
        check = Draft202012Validator(settings['execution_plan'])
        check.validate(plan)
        plan['match'] = 'all' if match == 'any' else 'any'
        assert not check.is_valid(plan)


def execute_config(tmp_path, state, rows, config):
    checked = validate_node({**state, 'draft': config})
    assert checked['status'] == 'ready', checked['issues']
    store = Storage(tmp_path)
    try:
        store.add_asset(state['resources'][0], rows)
        identifier = store.save_agent({**state, 'draft': config, 'confirmed': True})
    finally:
        store.close()
    return decode_artifact(handle(dict(directory=str(tmp_path), action='test', id=identifier, content='')))


@pytest.mark.parametrize('case', ['T06', 'T08'])
def test_empty_whitespace_zero_boundaries_and_duplicates(tmp_path, case):
    state, _ = case_state(case)
    columns = state['resources'][0]['columns']
    if case == 'T06':
        values = [('A', '고객', 0, None), ('B', '고객', '0', ''), ('C', None, 10, '이름없음'),
                  ('D', '   ', 10, ''), ('E', '고객', None, ''), ('F', '고객', ' ', ''),
                  ('G', '고객', -1, ''), ('A', '고객', 0, None)]
        expected = [('A', '고객', '0', ''), ('A', '고객', '0', ''), ('B', '고객', '0', ''), ('G', '고객', '-1', '')]
    else:
        values = [('A', '서울', None, 100), ('B', '', 'VIP', 100), ('C', '', '', 200),
                  ('D', '서울', '일반', None), ('E', '부산', None, 200), ('F', '서울', '일반', 99),
                  ('G', '서울', 'VIP', 101), ('H', '부산', '일반', 200), ('A', '서울', None, 100),
                  ('I', '서울', '일반', 0), ('J', '서울', 'VIP', ' ')]
        expected = [('A', '서울', '', '100'), ('A', '서울', '', '100'), ('B', '', 'VIP', '100'), ('G', '서울', 'VIP', '101')]
    rows = [dict(zip(columns, row)) for row in reversed(values)]
    fields, actual = execute_config(tmp_path, state, rows, config_for(state))
    assert fields == columns
    assert actual == [dict(zip(columns, row)) for row in expected]


def test_numeric_sort_uses_source_profile(tmp_path):
    state, _ = case_state('T08')
    state['resources'][0]['profile'] = {'번호': {'types': ['number']}}
    rows = [dict(번호=i, 지역='서울', 등급='일반', 금액=100) for i in (10, 2, 1)]
    _, actual = execute_config(tmp_path, state, rows, config_for(state))
    assert [r['번호'] for r in actual] == ['1', '2', '10']


@pytest.mark.parametrize('case', ['T06', 'T08'])
def test_unrequested_empty_or_zero_rules_cannot_replace_requested_filter(case):
    state, _ = case_state(case)
    config = config_for(state)
    filters = config['workflow']['nodes'][1]['settings']['execution_plan']['filters']
    if case == 'T06':
        filters[0]['field'] = '메모'
    else:
        filters[0]['operator'] = 'is_empty'
    assert validate_node({**state, 'draft': config})['status'] != 'ready'


@pytest.mark.parametrize('case', ['T06', 'T08'])
def test_invalid_workflow_cannot_be_saved_by_graph(case, tmp_path):
    state, rows = case_state(case)
    # Exercise the terminal rejection/save gate after the repair budget. Model
    # repair quality is outside this deterministic backend regression.
    state['repair_count'] = 2
    config = config_for(state)
    config['workflow']['nodes'][1]['settings']['execution_plan']['filters'] = []
    store = Storage(tmp_path)
    store.add_asset(state['resources'][0], rows)
    graph, connection = build_graph(store, conversation=lambda s: dict(draft=deepcopy(config)),
                                   condition_mode='enforce', condition_transport=Forbidden(),
                                   requirement_mode='enforce', requirement_transport=Forbidden(),
                                   capability_mode='off', verifier_mode='off')
    try:
        result = graph.invoke(state, {'configurable': {'thread_id': state['session_id']}})
        assert result['status'] != 'ready'
        assert not store.agents()
        assert not result.get('saved_agent_id')
    finally:
        connection.close()
        store.close()


@pytest.mark.parametrize('case', ['T06', 'T08'])
def test_exact_retry_preserves_contract_and_citation(case):
    state, _ = case_state(case)
    expected = compound_table_request(state)
    state['messages'] += [dict(role='assistant', content='일시 오류: 같은 메시지로 재시도'), deepcopy(state['messages'][0])]
    assert compound_table_request(state) == expected
    result = make_requirement_check('unused', mode='enforce', transport=Forbidden())(state)
    assert result['requirement_route'] == 'plan'
    assert [t['spec'] for t in result['requirement_ledger']['tasks']] == request_specs(expected)


@pytest.mark.parametrize('key', ['name', 'id', 'columns'])
def test_incomplete_attachment_metadata_does_not_break_general_validation(key):
    state, _ = case_state('T06')
    del state['resources'][0][key]
    assert compound_table_request(state) is None


@pytest.mark.parametrize('key', ['base_agent_id', 'required_node_refs', 'redesign_context', 'tool_gap_choice'])
def test_existing_workflow_edits_keep_general_owner(key):
    state, _ = case_state('T06')
    state[key] = 'existing'
    assert compound_table_request(state) is None


def test_unrepresentable_threshold_does_not_silently_round():
    state, _ = case_state('T08')
    state['messages'][0]['content'] = state['messages'][0]['content'].replace('100 이상', '9007199254740993 이상')
    assert compound_table_request(state) is None


def test_t08_truth_table_reaches_same_csv_as_independent_predicate(tmp_path):
    from itertools import product
    state, _ = case_state('T08')
    rows = [dict(번호=f'R{i:03}', 지역=region, 등급=grade, 금액=amount)
            for i, (region, grade, amount) in enumerate(product(
                ['서울', '부산', None, ' '], ['VIP', '일반', None, ''], [99, 100, 101, 0, None, ' ']))]
    expected = [r for r in rows if (r['지역'] == '서울' or r['등급'] == 'VIP')
                and isinstance(r['금액'], int) and r['금액'] >= 100]
    fields, actual = execute_config(tmp_path, state, list(reversed(rows)), config_for(state))
    assert fields == state['resources'][0]['columns']
    assert actual == [{k: '' if v is None else str(v) for k, v in r.items()} for r in expected]


@pytest.mark.parametrize('case', ['T06', 'T08'])
def test_no_matches_still_exports_original_headers(case, tmp_path):
    state, _ = case_state(case)
    columns = state['resources'][0]['columns']
    row = ('X', None, 100, '') if case == 'T06' else ('X', '부산', '일반', 200)
    assert execute_config(tmp_path, state, [dict(zip(columns, row))], config_for(state)) == (columns, [])


@pytest.mark.parametrize('case', ['T06', 'T08'])
def test_zero_exclusion_and_exclusive_threshold_are_not_requested(case):
    state, _ = case_state(case)
    config = config_for(state)
    index = 1 if case == 'T06' else 3
    filters = config['workflow']['nodes'][index]['settings']['execution_plan']['filters']
    filters[-1].update(field='금액', operator='gt', value=0 if case == 'T06' else 100, value_type='number')
    result = validate_node({**state, 'draft': config})
    assert result['status'] != 'ready'
    assert any('최종 CSV' in issue for issue in result['issues'])
