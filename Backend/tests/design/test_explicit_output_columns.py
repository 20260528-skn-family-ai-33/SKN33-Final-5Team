"""Explicit output columns are a final-output contract, not an ancestor hint."""
from copy import deepcopy
import base64
import json
from types import SimpleNamespace

import pytest

from agent_builder.design.explicit_table_criteria import (
    expected_specs, explicit_draft_issues, validate_explicit_ledger,
)
from agent_builder.design.nodes.validation import validate_node
from tests.design.test_explicit_keep_policy import node, state_for
from workflow_engine.executors.dispatch import execute_node
from workflow_engine.workflow import workflow_errors


def binding(port, parent, output):
    return dict(input=port, node_id=parent, output=output)


def operation(identifier, tool, bindings, operation, **arguments):
    result = node(identifier, tool, operation=operation, arguments_json=arguments)
    result['bindings'] = bindings
    return result


def projected_state(columns=None, **criteria):
    columns = columns or ['owner']
    state = state_for(dict(output_columns=columns, output_format='json', **criteria))
    state['resources'] = [dict(id='source', name='input.csv', kind='table', columns=['qty', 'owner'])]
    nodes = [node('read', 'read_table', file_ids=['source']),
             operation('rows', 'json_transform', [binding('value', 'read', 'table')],
                       'lookup', path=['rows']),
             operation('select', 'filter_sort', [binding('rows', 'rows', 'records')],
                       'select_columns', columns=columns),
             node('out', 'export_result', format='json')]
    nodes[1]['settings']['output_port'] = 'records'
    nodes[-1]['bindings'] = [binding('records', 'select', 'records')]
    set_nodes(state, nodes)
    return state


def set_nodes(state, nodes):
    draft = state['draft']
    draft['tool_ids'] = list(dict.fromkeys(n['tool'] for n in nodes))
    draft['workflow'] = dict(entry=nodes[0]['id'], nodes=nodes,
                            edges=[dict(source=a['id'], target=b['id'], branch='next')
                                   for a, b in zip(nodes, nodes[1:])])


def execute_artifact(state, tmp_path, rows):
    config = deepcopy(state['draft'])
    config['document_ids'] = []
    execution = dict(outputs={}, file_ids=[], external_inputs={
        'table': {'columns': ['qty', 'owner'], 'rows': rows}})
    storage = SimpleNamespace(path=str(tmp_path / 'state.json'))
    for current in config['workflow']['nodes']:
        execution['outputs'][current['id']] = execute_node(current, execution, storage, config, None)
    return execution['outputs']['out']['artifact']


def test_output_columns_are_extracted_as_an_ordered_select_requirement():
    assert expected_specs({'output_columns': ['owner', 'qty']}) == [
        dict(kind='select_fields', fields=['owner', 'qty'])]


@pytest.mark.parametrize('columns', [None, 'owner', {}, 1, True, [], [''], ['  '],
                                     ['owner', 'owner'], ['owner', 1], ['owner'] * 41])
def test_invalid_explicit_output_columns_fail_closed(columns):
    with pytest.raises(ValueError):
        expected_specs({'output_columns': columns})


def test_ledger_cannot_drop_or_reorder_output_columns():
    state = projected_state(['owner', 'qty'])
    for fields in (None, ['qty', 'owner']):
        plan = {'tasks': [] if fields is None else [{'spec': dict(kind='select_fields', fields=fields)}]}
        with pytest.raises(ValueError, match='select_fields'):
            validate_explicit_ledger(plan, state)


def test_direct_read_write_cannot_export_unrequested_columns(monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = state_for({'output_columns': ['owner'], 'output_format': 'json'})
    state['resources'] = [dict(id='source', name='input.csv', kind='table', columns=['qty', 'owner'])]
    state['draft']['document_ids'] = ['source']
    result = validate_node(state)
    assert result['status'] != 'ready'
    assert any('select_fields' in issue or '출력 열' in issue for issue in result['issues'])


@pytest.mark.parametrize('change', ['bypass', 'disconnected', 'order', 'compute', 'rename', 'join', 'unknown', 'later_writer'])
def test_an_ancestor_projection_does_not_prove_the_final_columns(change, tmp_path):
    state = projected_state(['owner', 'qty'])
    nodes = state['draft']['workflow']['nodes']
    if change in ('bypass', 'disconnected'):
        nodes[-1]['bindings'] = [binding('records', 'rows', 'records')]
        if change == 'disconnected':
            nodes = [nodes[0], nodes[1], nodes[3], nodes[2]]
    elif change == 'order':
        nodes[2]['settings']['arguments_json'] = {'columns': ['qty', 'owner']}
    elif change == 'later_writer':
        nodes += [node('raw_out', 'write_result', 'read', format='json')]
    else:
        if change == 'compute':
            extra = operation('change', 'calculate_columns', [binding('rows', 'select', 'records')],
                              'calculate_records', output_column='extra', formula='qty * 2')
        elif change == 'rename':
            table = operation('table', 'json_transform', [binding('value', 'select', 'records')],
                              'lookup', value={'rows': {'$binding': 'value'}, 'columns': ['owner', 'qty']}, path=[])
            table['settings']['output_port'] = 'table'
            nodes.insert(-1, table)
            extra = operation('change', 'calculate_columns', [binding('table', 'table', 'table')],
                              'rename_columns', mapping={'owner': 'name'})
        elif change == 'join':
            extra = operation('change', 'join_tables', [binding('left', 'select', 'records'),
                                                       binding('right', 'rows', 'records')],
                              'join_records', keys=['owner'], how='left', cardinality='many_to_one')
        else:
            extra = operation('change', 'json_transform', [binding('value', 'select', 'records')],
                              'lookup', path=[0])
            extra['settings']['output_port'] = 'records'
        nodes.insert(-1, extra)
        if change == 'rename':
            nodes[-1] = node('out', 'write_result', 'change', format='json')
        else:
            nodes[-1]['bindings'] = [binding('records', 'change', 'records')]
    set_nodes(state, nodes)
    if change in ('compute', 'rename', 'join'):
        assert not workflow_errors(state['draft']['workflow'], state['draft']['tool_ids'],
                                   state['resources'], state['draft']['execution_plan'])
        actual = json.loads(execute_artifact(state, tmp_path, [dict(qty=2, owner='A')])['content'])
        assert list(actual[0]) != ['owner', 'qty']
    assert explicit_draft_issues(state, state['draft'])


@pytest.mark.parametrize('criteria', [{}, {'missing_policy': 'keep', 'duplicate_policy': 'keep'}])
@pytest.mark.parametrize('columns', [['owner'], ['owner', 'qty']])
def test_correct_projection_matches_real_executor_output(monkeypatch, tmp_path, criteria, columns):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = projected_state(columns, **criteria)
    result = validate_node(state)
    assert result['status'] == 'ready', result['issues']
    artifact = execute_artifact(state, tmp_path, [dict(qty=None, owner='A'), dict(qty=None, owner='A')])
    rows = json.loads(artifact['content'])
    expected = {c: dict(owner='A', qty=None)[c] for c in columns}
    assert rows == [expected, expected]
    assert all(list(row) == columns for row in rows)


def test_final_finish_forwards_the_actual_selected_artifact():
    state = projected_state()
    nodes = state['draft']['workflow']['nodes']
    finish = node('done', 'finish')
    finish['bindings'] = [binding('artifact', 'out', 'artifact')]
    set_nodes(state, nodes + [finish])
    assert not explicit_draft_issues(state, state['draft'])


def test_output_columns_without_a_file_request_checks_the_final_record_output():
    state = projected_state()
    state['messages'] = [dict(role='user', content='제공하는 실행 조건: {"output_columns":["owner"]}')]
    set_nodes(state, state['draft']['workflow']['nodes'][:-1])
    assert not explicit_draft_issues(state, state['draft'])
    state['draft']['workflow']['nodes'][-1]['settings']['arguments_json'] = {'columns': ['owner', 'qty']}
    assert explicit_draft_issues(state, state['draft'])


def test_wide_source_schema_is_not_discarded_by_the_requested_column_limit():
    state = projected_state(['missing'])
    state['resources'][0]['columns'] = ['column_' + str(i) for i in range(41)]
    assert explicit_draft_issues(state, state['draft'])


def test_table_envelope_must_supply_columns_required_by_the_writer(tmp_path):
    state = projected_state()
    nodes = state['draft']['workflow']['nodes']
    table = operation('table', 'json_transform', [binding('value', 'select', 'records')],
                      'lookup', value={'rows': {'$binding': 'value'}}, path=[])
    table['settings']['output_port'] = 'table'
    set_nodes(state, nodes[:-1] + [table, node('out', 'write_result', 'table', format='json')])
    with pytest.raises(KeyError, match='columns'):
        execute_artifact(state, tmp_path, [dict(qty=2, owner='A')])
    assert explicit_draft_issues(state, state['draft'])


@pytest.mark.parametrize('writer', ['native', 'serialize'])
def test_csv_with_row_inferred_headers_cannot_guarantee_columns_for_empty_results(writer, tmp_path):
    state = projected_state()
    state['messages'] = [dict(role='user', content='제공하는 실행 조건: {"output_columns":["owner"],"output_format":"csv"}')]
    nodes = state['draft']['workflow']['nodes']
    if writer == 'native':
        nodes[-1]['settings']['format'] = 'csv'
    else:
        nodes[-1] = operation('out', 'export_result', [binding('value', 'select', 'records')],
                              'serialize', format='csv', filename='result.csv')
    set_nodes(state, nodes)
    artifact = execute_artifact(state, tmp_path, [])
    text = base64.b64decode(artifact['content']).decode('utf-8-sig') if artifact.get('encoding') == 'base64' else artifact['content']
    assert 'owner' not in text
    assert explicit_draft_issues(state, state['draft'])


@pytest.mark.parametrize('rows', [[], [dict(qty=2, owner='A')]])
def test_explicit_csv_headers_match_the_executor_even_without_rows(rows, tmp_path):
    state = projected_state()
    state['messages'] = [dict(role='user', content='제공하는 실행 조건: {"output_columns":["owner"],"output_format":"csv"}')]
    nodes = state['draft']['workflow']['nodes']
    nodes[-1] = operation('out', 'export_result', [binding('rows', 'select', 'records')],
                          'write_csv', columns=['owner'], path='result.csv')
    set_nodes(state, nodes)
    assert not explicit_draft_issues(state, state['draft'])
    artifact = execute_artifact(state, tmp_path, rows)
    text = base64.b64decode(artifact['content']).decode('utf-8-sig')
    assert text.splitlines()[0] == 'owner'

