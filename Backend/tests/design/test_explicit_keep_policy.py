"""Explicit no-op policies must agree with the actual table path."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.explicit_table_criteria import explicit_draft_issues
from agent_builder.design.nodes.validation import validate_node
from contracts.models import AgentDraft, NodeSettings
from workflow_engine.executors.output.write_result import execute as write_result


def node(identifier, tool, parent=None, **settings):
    defaults = dict(file_ids=[], query=None, fields=[], instructions=None, format=None,
                    question=None, condition_field=None, condition_operator=None)
    return dict(id=identifier, tool=tool, label=identifier,
                bindings=[] if parent is None else [dict(input='table', node_id=parent, output='table')],
                settings=NodeSettings.model_validate({**defaults, **settings}).model_dump())


def state_for(criteria, middle=None):
    request = '표를 JSON으로 저장해줘.\n제공하는 실행 조건: ' + json.dumps(criteria, ensure_ascii=False)
    nodes = [node('read', 'read_table'), *([middle] if middle else [])]
    nodes.append(node('out', 'write_result', nodes[-1]['id'], format='json'))
    draft = AgentDraft.model_validate(dict(
        name='표 보관', purpose='요청한 조건으로 표 저장', instructions=request,
        tool_ids=list(dict.fromkeys(n['tool'] for n in nodes)), document_ids=[],
        requirements=[dict(description='표 저장 조건', source_id='user', quote=request)],
        questions=[], unsupported=[], explanation='입력 표를 저장합니다.',
        execution_plan=dict(filters=[], sort=[], match='all', missing_policy='keep',
                            duplicate_policy='keep', duplicate_field=None, output_format='json'),
        workflow=dict(entry='read', nodes=nodes,
                      edges=[dict(source=a['id'], target=b['id'], branch='next')
                             for a, b in zip(nodes, nodes[1:])]))).model_dump()
    return dict(messages=[dict(role='user', content=request)], resources=[], draft=draft,
                trace=[], requirement_required=False)


@pytest.mark.parametrize('criteria', [
    {'missing_policy': 'keep'},
    {'duplicate_policy': 'keep'},
    {'missing_policy': 'keep', 'duplicate_policy': 'keep'},
])
def test_direct_table_export_keeps_nulls_and_duplicate_rows_without_a_filter(monkeypatch, criteria):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    state = state_for(criteria)
    checked = validate_node(state)
    assert checked['status'] == 'ready', checked['issues']
    rows = [{'qty': None}, {'qty': None}, {'qty': 1}, {'qty': 1}]
    result = write_result(state['draft']['workflow']['nodes'][-1],
                          {'outputs': {'read': {'table': {'columns': ['qty'], 'rows': rows}}}},
                          None, state['draft'], None)
    assert json.loads(result['artifact']['content']) == rows


@pytest.mark.parametrize('criteria', [
    {'missing_policy': None},
    {'missing_policy': 'exclude'}, {'missing_policy': 'error'},
    {'duplicate_policy': 'first', 'duplicate_field': 'qty'},
    {'duplicate_policy': 'last', 'duplicate_field': 'qty'},
    {'duplicate_policy': 'error', 'duplicate_field': 'qty'},
])
def test_active_policies_still_require_an_executable_step(criteria):
    state = state_for(criteria)
    assert explicit_draft_issues(state, state['draft'])


@pytest.mark.parametrize('tool,settings', [
    ('filter_sort', {'operation': 'handle_missing', 'arguments_json': {'columns': ['qty'], 'action': 'drop_rows'}}),
    ('filter_sort', {'operation': 'deduplicate', 'arguments_json': {'columns': ['qty'], 'keep': 'first'}}),
    ('filter_sort', {'operation': 'unverified_operation', 'arguments_json': {}}),
    ('aggregate_table', {'fields': [], 'aggregation': 'count'}),
])
def test_keep_does_not_approve_lossy_or_unverified_paths(tool, settings):
    state = state_for({'missing_policy': 'keep', 'duplicate_policy': 'keep'},
                      node('change', tool, 'read', **settings))
    assert explicit_draft_issues(state, state['draft'])


def test_effective_node_policy_still_overrides_global_default():
    state = state_for({'missing_policy': 'error'}, node('filter', 'filter_sort', 'read'))
    draft = state['draft']
    filtered = draft['workflow']['nodes'][1]
    filtered['settings']['execution_plan'] = {**deepcopy(draft['execution_plan']), 'missing_policy': 'error'}
    assert not explicit_draft_issues(state, draft)
    filtered['settings']['execution_plan'] = None
    assert explicit_draft_issues(state, draft)
    draft['execution_plan']['missing_policy'] = 'error'
    assert not explicit_draft_issues(state, draft)


def test_unconnected_policy_node_cannot_satisfy_an_active_policy():
    state = state_for({'missing_policy': 'error'})
    state['draft']['workflow']['nodes'].append(node('unused', 'filter_sort', 'read'))
    assert explicit_draft_issues(state, state['draft'])
