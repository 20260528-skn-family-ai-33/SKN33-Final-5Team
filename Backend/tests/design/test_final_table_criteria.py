"""Explicit policies and sort order must survive the final content path."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_builder.design.explicit_table_criteria import explicit_draft_issues
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from tests.design.test_explicit_keep_policy import node, state_for
from tests.design.test_explicit_output_columns import binding, operation, set_nodes, execute_artifact
from workflow_engine.executors.dispatch import execute_node
from workflow_engine.execution_path import artifact_output_ids, execution_path


ASC = [dict(field='qty', direction='ascending', value_type='number')]
DESC = [dict(field='qty', direction='descending', value_type='number')]


def configured(criteria):
    state = state_for({**criteria, 'output_format': 'json'})
    state['resources'] = [dict(id='source', name='input.csv', kind='table', columns=['qty', 'owner'])]
    return state


def seal(state, specs):
    specs = [{**dict(missing_policy=None, duplicate_policy=None, duplicate_field=None), **s}
             if s['kind'] == 'policy' else s for s in specs]
    specs = [dict(kind='read', file_ids=['source']), *specs, dict(kind='export', format='json')]
    plan = extract_plan(dict(tasks=[dict(source_message=0, description=s['kind'], spec=s) for s in specs],
                             unresolved=[]), state)
    state.update(requirement_required=True, requirement_ledger=plan,
                 requirement_fingerprint=ledger_fingerprint(state), requirement_digest=plan_digest(plan))


@pytest.mark.parametrize('before', [False, True])
@pytest.mark.parametrize('operation_id,arguments,criteria,rows', [
    ('handle_missing', {'columns': ['qty'], 'action': 'drop_rows'}, {'missing_policy': 'keep'},
     [dict(qty=None, owner='A'), dict(qty=1, owner='A')]),
    ('handle_missing', {'columns': ['qty'], 'action': 'fill', 'fill_value': 0}, {'missing_policy': 'keep'},
     [dict(qty=None, owner='A'), dict(qty=1, owner='A')]),
    ('deduplicate', {'columns': ['owner'], 'keep': 'first'}, {'duplicate_policy': 'keep'},
     [dict(qty=1, owner='A'), dict(qty=2, owner='A')]),
])
def test_keep_cannot_be_satisfied_before_or_after_a_destructive_operation(monkeypatch, tmp_path, before,
                                                                       operation_id, arguments, criteria, rows):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(criteria)
    change = operation('change', 'filter_sort', [binding('table', 'read' if before else 'policy', 'table')],
                       operation_id, **arguments)
    policy = node('policy', 'filter_sort', 'change' if before else 'read')
    middle = [change, policy] if before else [policy, change]
    set_nodes(state, [node('read', 'read_table', file_ids=['source']), *middle,
                      node('out', 'write_result', middle[-1]['id'], format='json')])
    seal(state, [dict(kind='policy', **criteria)])
    actual = json.loads(execute_artifact(state, tmp_path, rows)['content'])
    assert actual != rows
    checked = validate_node(state)
    assert checked['status'] != 'ready', checked
    assert any('정책' in issue for issue in checked['issues'])


def test_native_keep_followed_by_canonical_keep_is_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(dict(missing_policy='keep', duplicate_policy='keep'))
    nodes = [node('read', 'read_table', file_ids=['source']), node('policy', 'filter_sort', 'read'),
             operation('same', 'filter_sort', [binding('table', 'policy', 'table')],
                       'handle_missing', columns=['qty'], action='keep'),
             node('out', 'write_result', 'same', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', missing_policy='keep', duplicate_policy='keep')])
    assert validate_node(state)['status'] == 'ready'
    rows = [dict(qty=None, owner='A'), dict(qty=None, owner='A')]
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows


@pytest.mark.parametrize('orders,ready', [([ASC, DESC], False), ([DESC, ASC], True), ([ASC, []], True)])
def test_only_the_final_effective_sort_satisfies_the_request(monkeypatch, tmp_path, orders, ready):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(dict(sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source'])]
    for index, order in enumerate(orders):
        nodes.append(node('sort' + str(index), 'filter_sort', nodes[-1]['id'],
                          execution_plan={**deepcopy(state['draft']['execution_plan']), 'sort': order}))
    nodes.append(node('out', 'write_result', nodes[-1]['id'], format='json'))
    set_nodes(state, nodes)
    seal(state, [dict(kind='sort', sort=ASC)])
    checked = validate_node(state)
    assert (checked['status'] == 'ready') == ready, checked
    rows = json.loads(execute_artifact(state, tmp_path, [dict(qty=2, owner='B'), dict(qty=1, owner='A')])['content'])
    assert ([r['qty'] for r in rows] == [1, 2]) == ready


def test_a_later_sort_on_a_different_output_does_not_change_the_saved_content(monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('wanted', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], 'sort': ASC}),
             node('unused', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], 'sort': DESC}),
             node('out', 'write_result', 'wanted', format='json')]
    set_nodes(state, nodes)
    assert validate_node(state)['status'] == 'ready'


def test_non_sorting_calculation_and_rename_preserve_row_order(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('sort', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], 'sort': ASC}),
             node('calc', 'calculate_columns', 'sort', table_operation='compute',
                  calculation_formula='qty * 2', output_column='twice'),
             node('rename', 'calculate_columns', 'calc', table_operation='rename', rename_from='qty', rename_to='amount'),
             node('out', 'write_result', 'rename', format='json')]
    set_nodes(state, nodes)
    assert validate_node(state)['status'] == 'ready'
    rows = json.loads(execute_artifact(state, tmp_path, [dict(qty=2, owner='B'), dict(qty=1, owner='A')])['content'])
    assert [r['amount'] for r in rows] == [1, 2]


def test_canonical_sort_can_override_native_numeric_order(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('sort', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], 'sort': ASC}),
             operation('rows', 'json_transform', [binding('value', 'sort', 'table')], 'lookup', path=['rows']),
             operation('change', 'filter_sort', [binding('rows', 'rows', 'records')],
                       'sort', keys=[dict(column='qty', descending=False)]),
             node('out', 'export_result', format='json')]
    nodes[2]['settings']['output_port'] = 'records'
    nodes[-1]['bindings'] = [binding('records', 'change', 'records')]
    set_nodes(state, nodes)
    rows = json.loads(execute_artifact(state, tmp_path, [dict(qty='2', owner='B'), dict(qty='10', owner='A')])['content'])
    assert [r['qty'] for r in rows] == ['10', '2']
    assert explicit_draft_issues(state, state['draft'])


@pytest.mark.parametrize('field,ready', [('qty', False), ('extra', True)])
def test_overwriting_a_sort_key_invalidates_order_but_new_columns_do_not(monkeypatch, tmp_path, field, ready):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('sort', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], 'sort': ASC}),
             operation('rows', 'json_transform', [binding('value', 'sort', 'table')], 'lookup', path=['rows']),
             operation('change', 'calculate_columns', [binding('rows', 'rows', 'records')],
                       'calculate_records', output_column=field, formula='qty * -1', overwrite=True),
             node('out', 'export_result', format='json')]
    nodes[2]['settings']['output_port'] = 'records'
    nodes[-1]['bindings'] = [binding('records', 'change', 'records')]
    set_nodes(state, nodes)
    assert (validate_node(state)['status'] == 'ready') == ready
    rows = json.loads(execute_artifact(state, tmp_path, [dict(qty=2, owner='B'), dict(qty=1, owner='A')])['content'])
    assert [r['qty'] for r in rows] == ([1, 2] if ready else [-1, -2])


def test_canonical_sort_before_a_final_native_sort_is_safe(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source']),
             operation('rows', 'json_transform', [binding('value', 'read', 'table')], 'lookup', path=['rows']),
             operation('raw_sort', 'filter_sort', [binding('rows', 'rows', 'records')],
                       'sort', keys=[dict(column='qty', descending=True)]),
             operation('table', 'json_transform', [binding('value', 'raw_sort', 'records')], 'lookup',
                       value={'rows': {'$binding': 'value'}, 'columns': ['qty', 'owner']}, path=[]),
             node('sort', 'filter_sort', 'table', execution_plan={**state['draft']['execution_plan'], 'sort': ASC}),
             node('out', 'write_result', 'sort', format='json')]
    nodes[1]['settings']['output_port'] = 'records'
    nodes[3]['settings']['output_port'] = 'table'
    set_nodes(state, nodes)
    assert validate_node(state)['status'] == 'ready'
    rows = json.loads(execute_artifact(state, tmp_path, [dict(qty='2', owner='B'), dict(qty='10', owner='A')])['content'])
    assert [r['qty'] for r in rows] == ['2', '10']


def test_raw_predicate_cannot_drop_nulls_after_native_keep(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(missing_policy='keep'))
    nodes = [node('read', 'read_table', file_ids=['source']), node('policy', 'filter_sort', 'read'),
             operation('rows', 'json_transform', [binding('value', 'policy', 'table')], 'lookup', path=['rows']),
             operation('drop', 'filter_sort', [binding('rows', 'rows', 'records')],
                       'filter', predicates=[dict(column='qty', op='ne', value=None)]),
             node('out', 'export_result', format='json')]
    nodes[2]['settings']['output_port'] = 'records'
    nodes[-1]['bindings'] = [binding('records', 'drop', 'records')]
    set_nodes(state, nodes)
    source = [dict(qty=None, owner='A'), dict(qty=1, owner='B')]
    assert json.loads(execute_artifact(state, tmp_path, source)['content']) == source[1:]
    assert validate_node(state)['status'] != 'ready'


def test_invalid_table_envelope_does_not_preserve_the_sort_proof(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('sort', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], 'sort': ASC}),
             operation('rows', 'json_transform', [binding('value', 'sort', 'table')], 'lookup', path=['rows']),
             operation('broken', 'json_transform', [binding('value', 'rows', 'records')], 'lookup',
                       value={'rows': {'$binding': 'value'}}, path=[]),
             node('out', 'write_result', 'broken', format='json')]
    nodes[2]['settings']['output_port'] = 'records'
    nodes[3]['settings']['output_port'] = 'table'
    set_nodes(state, nodes)
    with pytest.raises(KeyError, match='columns'):
        execute_artifact(state, tmp_path, [dict(qty=2, owner='B'), dict(qty=1, owner='A')])
    assert validate_node(state)['status'] != 'ready'


def test_a_registered_list_selection_cannot_hide_row_loss_from_keep(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(dict(missing_policy='keep', duplicate_policy='keep'))
    nodes = [node('read', 'read_table', file_ids=['source']), node('policy', 'filter_sort', 'read'),
             operation('rows', 'json_transform', [binding('value', 'policy', 'table')], 'lookup', path=['rows']),
             operation('pick', 'list_transform', [binding('items', 'rows', 'records')], 'select', index=1),
             operation('one', 'json_transform', [binding('value', 'pick', 'result')],
                       'lookup', value=[{'$binding': 'value'}], path=[]),
             node('out', 'export_result', format='json')]
    nodes[2]['settings']['output_port'] = nodes[4]['settings']['output_port'] = 'records'
    nodes[-1]['bindings'] = [binding('records', 'one', 'records')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', missing_policy='keep', duplicate_policy='keep')])
    source = [dict(qty=None, owner='A'), dict(qty=1, owner='A')]
    assert json.loads(execute_artifact(state, tmp_path, source)['content']) == source[1:]
    assert validate_node(state)['status'] != 'ready'


@pytest.mark.parametrize('dynamic', ['columns', 'action'])
def test_dynamic_missing_settings_cannot_preserve_a_sort_proof(monkeypatch, tmp_path, dynamic):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC))
    args = dict(columns=['qty'], action='fill', fill_value=0)
    value = args[dynamic]
    args[dynamic] = {'$binding': dynamic}
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('sort', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], 'sort': ASC}),
             operation('setting', 'json_transform', [], 'lookup', value=value, path=[]),
             operation('fill', 'filter_sort', [binding('table', 'sort', 'table'), binding(dynamic, 'setting', 'result')],
                       'handle_missing', **args),
             node('out', 'write_result', 'fill', format='json')]
    set_nodes(state, nodes)
    rows = json.loads(execute_artifact(state, tmp_path, [dict(qty=None, owner='A'), dict(qty=1, owner='B')])['content'])
    assert [r['qty'] for r in rows] == [1, 0]
    assert validate_node(state)['status'] != 'ready'


@pytest.mark.parametrize('criteria', [dict(sort=ASC), dict(output_columns=['qty'])])
def test_terminal_human_preview_uses_the_actual_data_ancestry(monkeypatch, tmp_path, criteria):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(criteria)
    request = '표 결과를 사람이 확인하게 해줘.\n제공하는 실행 조건: ' + json.dumps(criteria)
    state['messages'] = [dict(role='user', content=request)]
    state['draft']['instructions'] = request
    state['draft']['requirements'] = [dict(description='표 확인', source_id='user', quote=request)]
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('sort', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], 'sort': ASC}),
             operation('rows', 'json_transform', [binding('value', 'sort', 'table')], 'lookup', path=['rows'])]
    nodes[-1]['settings']['output_port'] = 'records'
    if 'output_columns' in criteria:
        nodes.append(operation('select', 'filter_sort', [binding('rows', 'rows', 'records')],
                               'select_columns', columns=['qty']))
    human = node('human', 'ask_user', question='표 결과를 확인해주세요.')
    human['bindings'] = [binding('records', nodes[-1]['id'], 'records')]
    set_nodes(state, nodes + [human])
    assert validate_node(state)['status'] == 'ready'
    config = state['draft']
    execution = dict(outputs={}, file_ids=[], external_inputs={'table': {
        'columns': ['qty', 'owner'], 'rows': [dict(qty=2, owner='B'), dict(qty=1, owner='A')]}})
    previews = []
    with patch('workflow_engine.executors.control.ask_user.interrupt', lambda data: previews.append(data) or '확인'):
        for current in config['workflow']['nodes']:
            execution['outputs'][current['id']] = execute_node(current, execution, SimpleNamespace(path=str(tmp_path/'data.json')), config, None)
    assert [r['qty'] for r in previews[0]['preview']] == [1, 2]
    if 'output_columns' in criteria:
        assert all(list(r) == ['qty'] for r in previews[0]['preview'])


def test_explicit_aggregation_can_follow_keep_without_being_treated_as_hidden_row_loss(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(missing_policy='keep', duplicate_policy='keep',
                            aggregations=[dict(groups=['owner'], operation='count', value_column=None)]))
    nodes = [node('read', 'read_table', file_ids=['source']), node('policy', 'filter_sort', 'read'),
             node('count', 'aggregate_table', 'policy', fields=['owner'], aggregation='count'),
             node('out', 'write_result', 'count', format='json')]
    set_nodes(state, nodes)
    assert validate_node(state)['status'] == 'ready'
    rows = [dict(qty=None, owner='A'), dict(qty=None, owner='A')]
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == [dict(owner='A', count=2)]


@pytest.mark.parametrize('sort_before', [False, True])
@pytest.mark.parametrize('sort_after', [False, True])
def test_reused_sort_column_name_requires_sorting_the_current_values(monkeypatch, tmp_path, sort_before, sort_after):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source'])]
    if sort_before:
        nodes.append(node('before', 'filter_sort', nodes[-1]['id'],
                          execution_plan={**state['draft']['execution_plan'], 'sort': ASC}))
    nodes.append(node('rename', 'calculate_columns', nodes[-1]['id'], table_operation='rename',
                      rename_from='qty', rename_to='amount'))
    nodes.append(node('compute', 'calculate_columns', 'rename', table_operation='compute',
                      calculation_formula='amount * -1', output_column='qty'))
    if sort_after:
        nodes.append(node('after', 'filter_sort', nodes[-1]['id'],
                          execution_plan={**state['draft']['execution_plan'], 'sort': ASC}))
    nodes.append(node('out', 'write_result', nodes[-1]['id'], format='json'))
    set_nodes(state, nodes)
    assert (validate_node(state)['status'] == 'ready') == sort_after
    source = [dict(qty=1, owner='A'), dict(qty=2, owner='B')]
    actual = json.loads(execute_artifact(state, tmp_path, source)['content'])
    assert [r['qty'] for r in actual] == ([-2, -1] if sort_after else [-1, -2])


@pytest.mark.parametrize('criterion', ['sort', 'keep'])
def test_an_early_artifact_does_not_prove_a_later_branch_output(monkeypatch, tmp_path, criterion):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC) if criterion == 'sort' else dict(missing_policy='keep'))
    change = (node('change', 'filter_sort', 'check', execution_plan={**state['draft']['execution_plan'], 'sort': DESC})
              if criterion == 'sort' else operation('change', 'filter_sort', [binding('table', 'check', 'table')],
                                                     'handle_missing', columns=['qty'], action='drop_rows'))
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('policy', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], 'sort': ASC if criterion == 'sort' else []}),
             node('early', 'write_result', 'policy', format='json'),
             node('check', 'condition', 'policy', condition_operator='empty'),
             node('done', 'finish', instructions='빈 표입니다.'), change,
             node('late', 'write_result', 'change', format='json')]
    set_nodes(state, nodes)
    state['draft']['workflow']['edges'] = [dict(source=a, target=b, branch=c) for a,b,c in [
        ('read', 'policy', 'next'), ('policy', 'early', 'next'), ('early', 'check', 'next'),
        ('check', 'done', 'yes'), ('check', 'change', 'no'), ('change', 'late', 'next')]]
    assert validate_node(state)['status'] != 'ready'
    source = [dict(qty=1, owner='A'), dict(qty=2, owner='B')] if criterion == 'sort' else [dict(qty=None, owner='A'), dict(qty=1, owner='B')]
    config = state['draft']
    execution = dict(outputs={}, file_ids=[], external_inputs={'table': {'columns': ['qty', 'owner'], 'rows': source}})
    for current in [nodes[i] for i in (0, 1, 2, 3, 5, 6)]:
        execution['outputs'][current['id']] = execute_node(current, execution, SimpleNamespace(path=str(tmp_path/'data.json')), config, None)
    assert execution['outputs']['check']['decision'] is False
    assert execution_path(config['workflow'], execution['outputs'])[-1]['id'] == 'late'
    final = artifact_output_ids(config['workflow'], execution['outputs'])[-1]
    actual = json.loads(execution['outputs'][final]['artifact']['content'])
    assert actual == (list(reversed(source)) if criterion == 'sort' else source[1:])


def test_removing_nulls_before_error_policy_cannot_hide_the_error(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(dict(missing_policy='error', sort=ASC))
    policy = {**state['draft']['execution_plan'], 'missing_policy': 'error', 'sort': ASC}
    nodes = [node('read', 'read_table', file_ids=['source']),
             operation('drop', 'filter_sort', [binding('table', 'read', 'table')],
                       'handle_missing', columns=['qty'], action='drop_rows'),
             node('policy', 'filter_sort', 'drop', execution_plan=policy),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', missing_policy='error'), dict(kind='sort', sort=ASC)])
    rows = [dict(qty=None, owner='A'), dict(qty=1, owner='B')]
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows[1:]
    assert validate_node(state)['status'] != 'ready'
    nodes[2]['bindings'] = [binding('table', 'read', 'table')]
    with pytest.raises(ValueError, match='빈 값'):
        execute_artifact(state, tmp_path, rows)


@pytest.mark.parametrize('requested,earlier', [('first','last'), ('last','first'), ('error','first')])
def test_conflicting_dedup_before_native_policy_cannot_choose_the_original_rows(monkeypatch, tmp_path, requested, earlier):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    criteria = dict(duplicate_policy=requested, duplicate_field='owner')
    state = configured(criteria)
    nodes = [node('read', 'read_table', file_ids=['source']),
             operation('drop', 'filter_sort', [binding('table', 'read', 'table')],
                       'deduplicate', columns=['owner'], keep=earlier),
             node('policy', 'filter_sort', 'drop', execution_plan={**state['draft']['execution_plan'], **criteria}),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', **criteria)])
    rows = [dict(qty=1, owner='A'), dict(qty=2, owner='A')]
    actual = json.loads(execute_artifact(state, tmp_path, rows)['content'])
    assert actual == [rows[0 if earlier == 'first' else 1]]
    assert validate_node(state)['status'] != 'ready'


@pytest.mark.parametrize('before', [False, True])
def test_same_field_and_keep_do_not_make_json_and_native_dedup_equivalent(monkeypatch, tmp_path, before):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    criteria = dict(duplicate_policy='first', duplicate_field='owner')
    state = configured(criteria)
    policy = node('policy', 'filter_sort', 'same' if before else 'read',
                  execution_plan={**state['draft']['execution_plan'], **criteria})
    changed = operation('same', 'filter_sort', [binding('table', 'read' if before else 'policy', 'table')],
                        'deduplicate', columns=['owner'], keep='first')
    middle = [changed, policy] if before else [policy, changed]
    nodes = [node('read', 'read_table', file_ids=['source']), *middle,
             node('out', 'write_result', middle[-1]['id'], format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', **criteria)])
    assert validate_node(state)['status'] != 'ready'
    rows = [dict(qty=1, owner={'a': 1, 'b': 2}), dict(qty=2, owner={'b': 2, 'a': 1})]
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows[:1]
    policy['bindings'] = [binding('table', 'read', 'table')]
    set_nodes(state, [nodes[0], policy, node('out', 'write_result', 'policy', format='json')])
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows


@pytest.mark.parametrize('duplicate', ['keep', 'first', 'last', 'error'])
def test_missing_cleanup_cannot_hide_rows_from_an_unrelated_duplicate_policy(monkeypatch, tmp_path, duplicate):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    criteria = dict(duplicate_policy=duplicate, duplicate_field=None if duplicate == 'keep' else 'owner')
    state = configured(criteria)
    nodes = [node('read', 'read_table', file_ids=['source']),
             operation('drop', 'filter_sort', [binding('table', 'read', 'table')],
                       'handle_missing', columns=['qty'], action='drop_rows'),
             node('policy', 'filter_sort', 'drop', execution_plan={**state['draft']['execution_plan'], **criteria}),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', **criteria)])
    rows = [dict(qty=None, owner='A'), dict(qty=1, owner='A')]
    if duplicate == 'last':
        rows.reverse()
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == [dict(qty=1, owner='A')]
    assert validate_node(state)['status'] != 'ready'


@pytest.mark.parametrize('missing', ['keep', 'error', 'exclude'])
def test_dedup_cannot_hide_rows_from_an_unrelated_missing_policy(monkeypatch, tmp_path, missing):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(dict(missing_policy=missing, sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source']),
             operation('drop', 'filter_sort', [binding('table', 'read', 'table')],
                       'deduplicate', columns=['owner'], keep='first'),
             node('policy', 'filter_sort', 'drop', execution_plan={**state['draft']['execution_plan'], 'missing_policy': missing, 'sort': ASC}),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', missing_policy=missing), dict(kind='sort', sort=ASC)])
    rows = [dict(qty=1, owner='A'), dict(qty=2 if missing == 'exclude' else None, owner='A')]
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows[:1]
    assert validate_node(state)['status'] != 'ready'


@pytest.mark.parametrize('before', [False, True])
def test_canonical_exclusion_is_not_proved_by_a_matching_native_policy(monkeypatch, tmp_path, before):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    criteria = dict(missing_policy='exclude', duplicate_policy='first', duplicate_field='owner')
    state = configured(criteria)
    policy = node('policy', 'filter_sort', 'drop' if before else 'read',
                  execution_plan={**state['draft']['execution_plan'], **criteria})
    drop = operation('drop', 'filter_sort', [binding('table', 'read' if before else 'policy', 'table')],
                     'handle_missing', columns=['qty'], action='drop_rows')
    middle = [drop, policy] if before else [policy, drop]
    set_nodes(state, [node('read', 'read_table', file_ids=['source']), *middle,
                      node('out', 'write_result', middle[-1]['id'], format='json')])
    seal(state, [dict(kind='policy', **criteria)])
    assert validate_node(state)['status'] != 'ready'
    rows = [dict(qty=None, owner='A'), dict(qty=1, owner='A')]
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == (rows[1:] if before else [])


def test_explicit_exclude_can_preserve_duplicate_keep(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    criteria = dict(missing_policy='exclude', duplicate_policy='keep')
    state = configured(criteria)
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('policy', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], **criteria}),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', **criteria)])
    assert validate_node(state)['status'] == 'ready'
    rows = [dict(qty=None, owner='A'), dict(qty=1, owner='A'), dict(qty=1, owner='A')]
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows[1:]


def test_an_explicit_fill_task_can_precede_duplicate_first(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    criteria = dict(duplicate_policy='first', duplicate_field='owner')
    state = configured(criteria)
    request = 'qty 빈값을 0으로 채우고 owner 중복 첫 행을 저장해줘.\n제공하는 실행 조건: ' + json.dumps(criteria)
    state['messages'][0]['content'] = request
    state['draft']['instructions'] = request
    state['draft']['requirements'] = [dict(description='빈값 채움 후 중복 처리', source_id='user', quote=request)]
    nodes = [node('read', 'read_table', file_ids=['source']),
             operation('fill', 'filter_sort', [binding('table', 'read', 'table')],
                       'handle_missing', columns=['qty'], action='fill', fill_value=0),
             node('policy', 'filter_sort', 'fill', execution_plan={**state['draft']['execution_plan'], **criteria}),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='fill_missing', fields=['qty'], value=0), dict(kind='policy', **criteria)])
    assert validate_node(state)['status'] == 'ready'
    rows = [dict(qty=None, owner='A'), dict(qty=1, owner='A')]
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == [dict(qty=0, owner='A')]


@pytest.mark.parametrize('sort_before', [False, True])
def test_sorting_the_old_alias_does_not_sort_a_recreated_requested_column(monkeypatch, tmp_path, sort_before):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source'])]
    if sort_before:
        nodes.append(node('before', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], 'sort': ASC}))
    nodes.extend([
        node('rename', 'calculate_columns', nodes[-1]['id'], table_operation='rename', rename_from='qty', rename_to='amount'),
        node('compute', 'calculate_columns', 'rename', table_operation='compute', calculation_formula='amount * -1', output_column='qty'),
        node('wrong', 'filter_sort', 'compute', execution_plan={**state['draft']['execution_plan'], 'sort': [{**ASC[0], 'field': 'amount'}]}),
        node('out', 'write_result', 'wrong', format='json')])
    set_nodes(state, nodes)
    assert validate_node(state)['status'] != 'ready'
    source = [dict(qty=1, owner='A'), dict(qty=2, owner='B')]
    assert [r['qty'] for r in json.loads(execute_artifact(state, tmp_path, source)['content'])] == [-1, -2]


@pytest.mark.parametrize('missing,action', [('exclude', 'drop_rows'), ('error', 'error')])
def test_canonical_missing_action_on_different_columns_is_not_equivalent(monkeypatch, tmp_path, missing, action):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(dict(missing_policy=missing, sort=ASC))
    nodes = [node('read', 'read_table', file_ids=['source']),
             operation('wrong', 'filter_sort', [binding('table', 'read', 'table')],
                       'handle_missing', columns=['owner'], action=action),
             node('policy', 'filter_sort', 'wrong', execution_plan={**state['draft']['execution_plan'], 'missing_policy': missing, 'sort': ASC}),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', missing_policy=missing), dict(kind='sort', sort=ASC)])
    assert validate_node(state)['status'] != 'ready'
    rows = [dict(qty=1, owner=None), dict(qty=2, owner='B')]
    if missing == 'error':
        with pytest.raises(ValueError):
            execute_artifact(state, tmp_path, rows)
    else:
        assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows[1:]
    nodes[2]['bindings'] = [binding('table', 'read', 'table')]
    set_nodes(state, [nodes[0], *nodes[2:]])
    assert validate_node(state)['status'] == 'ready'
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows


@pytest.mark.parametrize('requested', [False, True])
def test_native_filter_cannot_hide_a_later_missing_error(monkeypatch, tmp_path, requested):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    filters = [dict(field='owner', operator='eq', value='B', value_type='text', compare_field=None)]
    criteria = dict(missing_policy='error', sort=ASC, **({'selection': filters} if requested else {}))
    state = configured(criteria)
    plan = {**state['draft']['execution_plan'], 'missing_policy': 'error', 'sort': ASC}
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('hide', 'filter_sort', 'read', execution_plan={**plan, 'sort': [], 'filters': filters}),
             node('policy', 'filter_sort', 'hide', execution_plan=plan),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    specs = [dict(kind='policy', missing_policy='error'), dict(kind='sort', sort=ASC)]
    if requested:
        specs.append(dict(kind='filter', filters=filters, match='all'))
    seal(state, specs)
    assert validate_node(state)['status'] != 'ready'
    rows = [dict(qty=None, owner='A'), dict(qty=1, owner='B')]
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows[1:]
    nodes[2]['bindings'] = [binding('table', 'read', 'table')]
    if requested:
        nodes[2]['settings']['execution_plan']['filters'] = filters
    set_nodes(state, [nodes[0], *nodes[2:]])
    assert validate_node(state)['status'] == 'ready'
    with pytest.raises(ValueError, match='빈 값'):
        execute_artifact(state, tmp_path, rows)


@pytest.mark.parametrize('duplicate', ['keep', 'first', 'last', 'error'])
def test_combined_native_policy_filter_sort_preserves_the_requested_contract(monkeypatch, tmp_path, duplicate):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    filters = [dict(field='owner', operator='eq', value='B', value_type='text', compare_field=None)]
    criteria = dict(missing_policy='exclude', duplicate_policy=duplicate,
                    duplicate_field=None if duplicate == 'keep' else 'owner', selection=filters, sort=ASC)
    state = configured(criteria)
    plan = {**state['draft']['execution_plan'], **{k: v for k,v in criteria.items() if k != 'selection'}, 'filters': filters}
    set_nodes(state, [node('read', 'read_table', file_ids=['source']),
                      node('policy', 'filter_sort', 'read', execution_plan=plan),
                      node('out', 'write_result', 'policy', format='json')])
    seal(state, [dict(kind='policy', **{k:criteria[k] for k in ('missing_policy','duplicate_policy','duplicate_field')}),
                 dict(kind='sort', sort=ASC), dict(kind='filter', filters=filters, match='all')])
    assert validate_node(state)['status'] == 'ready'
    rows = [dict(qty=None, owner='B'), dict(qty=1, owner='B'), dict(qty=3, owner='A')]
    if duplicate == 'error':
        with pytest.raises(ValueError, match='중복'):
            execute_artifact(state, tmp_path, rows)
    else:
        assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == ([] if duplicate == 'first' else rows[1:2])


@pytest.mark.parametrize('canonical', [False, True])
@pytest.mark.parametrize('sort_key', ['qty', 'amount'])
def test_rename_reintroducing_a_requested_name_replaces_its_old_alias(monkeypatch, tmp_path, canonical, sort_key):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = configured(dict(sort=ASC))
    if canonical:
        rename = [operation('rename', 'calculate_columns', [binding('table', 'read', 'table')],
                            'rename_columns', mapping={'qty': 'amount', 'owner': 'qty'})]
    else:
        rename = [node('rename', 'calculate_columns', 'read', table_operation='rename', rename_from='qty', rename_to='amount'),
                  node('reuse', 'calculate_columns', 'rename', table_operation='rename', rename_from='owner', rename_to='qty')]
    set_nodes(state, [node('read', 'read_table', file_ids=['source']), *rename,
                      node('sort', 'filter_sort', rename[-1]['id'], execution_plan={**state['draft']['execution_plan'], 'sort': [{**ASC[0], 'field': sort_key}]}),
                      node('out', 'write_result', 'sort', format='json')])
    assert (validate_node(state)['status'] == 'ready') == (sort_key == 'qty')
    rows = [dict(qty=1, owner=2), dict(qty=2, owner=1)]
    assert [r['qty'] for r in json.loads(execute_artifact(state, tmp_path, rows)['content'])] == ([1, 2] if sort_key == 'qty' else [2, 1])


@pytest.mark.parametrize('canonical', [False, True])
def test_missing_checks_follow_a_proved_rename_on_the_execution_path(monkeypatch, tmp_path, canonical):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(dict(missing_policy='error', sort=ASC))
    plan = {**state['draft']['execution_plan'], 'missing_policy': 'error', 'sort': ASC}
    rename = (operation('rename', 'calculate_columns', [binding('table', 'before', 'table')],
                        'rename_columns', mapping={'qty': 'amount'}) if canonical else
              node('rename', 'calculate_columns', 'before', table_operation='rename', rename_from='qty', rename_to='amount'))
    set_nodes(state, [node('read', 'read_table', file_ids=['source']),
                      node('before', 'filter_sort', 'read', execution_plan=plan), rename,
                      node('after', 'filter_sort', 'rename', execution_plan={**plan, 'sort': [{**ASC[0], 'field': 'amount'}]}),
                      node('out', 'write_result', 'after', format='json')])
    seal(state, [dict(kind='policy', missing_policy='error'), dict(kind='sort', sort=ASC)])
    assert validate_node(state)['status'] == 'ready'
    rows = [dict(qty=2, owner='B'), dict(qty=1, owner='A')]
    assert [r['amount'] for r in json.loads(execute_artifact(state, tmp_path, rows)['content'])] == [1, 2]
    with pytest.raises(ValueError, match='빈 값'):
        execute_artifact(state, tmp_path, [dict(qty=None, owner='A')])
    state['draft']['workflow']['nodes'].reverse()
    assert validate_node(state)['status'] == 'ready'


@pytest.mark.parametrize('criteria,added,rows', [
    (dict(missing_policy='error', sort=ASC), dict(duplicate_policy='first', duplicate_field='owner'),
     [dict(qty=1, owner='A'), dict(qty=None, owner='A')]),
    (dict(duplicate_policy='first', duplicate_field='owner'), dict(missing_policy='exclude'),
     [dict(qty=None, owner='A'), dict(qty=1, owner='A')]),
])
def test_unrequested_opposite_native_policy_cannot_change_the_contract(monkeypatch, tmp_path, criteria, added, rows):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(criteria)
    plan = {**state['draft']['execution_plan'], **criteria}
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('policy', 'filter_sort', 'read', execution_plan={**plan, **added}),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', **{k:v for k,v in criteria.items() if k != 'sort'}),
                 *([dict(kind='sort', sort=ASC)] if criteria.get('sort') else [])])
    actual = json.loads(execute_artifact(state, tmp_path, rows)['content'])
    assert actual == (rows[:1] if 'missing_policy' in criteria else [])
    assert validate_node(state)['status'] != 'ready'
    nodes[1]['settings']['execution_plan'] = plan
    assert validate_node(state)['status'] == 'ready'
    if 'missing_policy' in criteria:
        with pytest.raises(ValueError, match='빈 값'):
            execute_artifact(state, tmp_path, rows)
    else:
        assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows[:1]


@pytest.mark.parametrize('criteria,added,rows', [
    (dict(missing_policy='error', sort=ASC), dict(duplicate_policy='last', duplicate_field='owner'),
     [dict(qty=None, owner='A'), dict(qty=1, owner='A')]),
    (dict(missing_policy='error', sort=ASC), dict(duplicate_policy='error', duplicate_field='owner'),
     [dict(qty=1, owner='A'), dict(qty=2, owner='A')]),
    (dict(duplicate_policy='keep', sort=ASC), dict(missing_policy='error'),
     [dict(qty=None, owner='A'), dict(qty=1, owner='A')]),
])
def test_unrequested_native_last_or_error_also_changes_the_policy(monkeypatch, tmp_path, criteria, added, rows):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(criteria)
    plan = {**state['draft']['execution_plan'], **criteria}
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('policy', 'filter_sort', 'read', execution_plan={**plan, **added}),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', **{k:v for k,v in criteria.items() if k != 'sort'}), dict(kind='sort', sort=ASC)])
    assert validate_node(state)['status'] != 'ready'
    if 'error' in added.values():
        with pytest.raises(ValueError):
            execute_artifact(state, tmp_path, rows)
    else:
        assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows[1:]
    nodes[1]['settings']['execution_plan'] = plan
    assert validate_node(state)['status'] == 'ready'
    if added.get('duplicate_policy') == 'last':
        with pytest.raises(ValueError, match='빈 값'):
            execute_artifact(state, tmp_path, rows)
    else:
        assert len(json.loads(execute_artifact(state, tmp_path, rows)['content'])) == 2


def test_an_independently_sealed_policy_is_allowed_with_explicit_criteria(monkeypatch, tmp_path):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(dict(missing_policy='keep'))
    request = 'owner 중복은 첫 행(first)을 저장해줘.\n제공하는 실행 조건: {"missing_policy":"keep","output_format":"json"}'
    state['messages'][0]['content'] = request
    state['draft']['instructions'] = request
    state['draft']['requirements'] = [dict(description='중복 첫 행 저장', source_id='user', quote=request)]
    policy = dict(missing_policy='keep', duplicate_policy='first', duplicate_field='owner')
    set_nodes(state, [node('read', 'read_table', file_ids=['source']),
                      node('policy', 'filter_sort', 'read', execution_plan={**state['draft']['execution_plan'], **policy}),
                      node('out', 'write_result', 'policy', format='json')])
    seal(state, [dict(kind='policy', **policy)])
    assert validate_node(state)['status'] == 'ready'
    rows = [dict(qty=None, owner='A'), dict(qty=1, owner='A')]
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows[:1]


@pytest.mark.parametrize('duplicate', ['first', 'last', 'error'])
def test_all_native_steps_use_the_independently_sealed_duplicate_key(monkeypatch, tmp_path, duplicate):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    state = configured(dict(missing_policy='keep'))
    request = 'owner 중복 정책은 ' + duplicate + '로 저장해줘.\n제공하는 실행 조건: {"missing_policy":"keep","output_format":"json"}'
    state['messages'][0]['content'] = request
    state['draft']['instructions'] = request
    state['draft']['requirements'] = [dict(description='owner 중복 처리', source_id='user', quote=request)]
    policy = dict(missing_policy='keep', duplicate_policy=duplicate, duplicate_field='owner')
    plan = {**state['draft']['execution_plan'], **policy}
    nodes = [node('read', 'read_table', file_ids=['source']),
             node('wrong', 'filter_sort', 'read', execution_plan={**plan, 'duplicate_field': 'qty'}),
             node('policy', 'filter_sort', 'wrong', execution_plan=plan),
             node('out', 'write_result', 'policy', format='json')]
    set_nodes(state, nodes)
    seal(state, [dict(kind='policy', **policy)])
    assert validate_node(state)['status'] != 'ready'
    rows = [dict(qty=1, owner='A'), dict(qty=1, owner='B')]
    if duplicate == 'error':
        with pytest.raises(ValueError, match='중복'):
            execute_artifact(state, tmp_path, rows)
    else:
        assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == [rows[duplicate == 'last']]
    nodes[2]['bindings'] = [binding('table', 'read', 'table')]
    set_nodes(state, [nodes[0], *nodes[2:]])
    assert validate_node(state)['status'] == 'ready'
    assert json.loads(execute_artifact(state, tmp_path, rows)['content']) == rows


@pytest.mark.parametrize('canonical', [False, True])
@pytest.mark.parametrize('duplicate', ['first', 'last', 'error'])
def test_duplicate_policy_follows_its_renamed_logical_key(monkeypatch, tmp_path, canonical, duplicate):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    criteria = dict(missing_policy='keep', duplicate_policy=duplicate, duplicate_field='owner')
    state = configured(criteria)
    plan = {**state['draft']['execution_plan'], **criteria}
    rename = (operation('rename', 'calculate_columns', [binding('table', 'before', 'table')],
                        'rename_columns', mapping={'owner': 'person'}) if canonical else
              node('rename', 'calculate_columns', 'before', table_operation='rename', rename_from='owner', rename_to='person'))
    set_nodes(state, [node('read', 'read_table', file_ids=['source']),
                      node('before', 'filter_sort', 'read', execution_plan=plan), rename,
                      node('after', 'filter_sort', 'rename', execution_plan={**plan, 'duplicate_field': 'person'}),
                      node('out', 'write_result', 'after', format='json')])
    seal(state, [dict(kind='policy', **criteria)])
    assert validate_node(state)['status'] == 'ready'
    rows = [dict(qty=1, owner='A'), dict(qty=2, owner='B' if duplicate == 'error' else 'A')]
    actual = json.loads(execute_artifact(state, tmp_path, rows)['content'])
    assert [r['qty'] for r in actual] == ([1, 2] if duplicate == 'error' else [1 if duplicate == 'first' else 2])
    assert all(set(r) == {'qty', 'person'} for r in actual)
    if duplicate == 'error':
        with pytest.raises(ValueError, match='중복'):
            execute_artifact(state, tmp_path, [rows[0], rows[0]])
