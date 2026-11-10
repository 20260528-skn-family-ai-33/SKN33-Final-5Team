"""A ready draft must fill missing cells with the requested JSON scalar type."""
import json

import pytest

from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_ledger import (
    ledger_fingerprint, plan_digest, validate_plan,
)
from contracts.models import AgentDraft
from workflow_engine.unified_tools.dispatcher import execute


def fill_state(expected, actual):
    request = f'qty 열의 빈 값을 {json.dumps(expected)}로 채워 JSON으로 저장해줘.'
    state = {
        'messages': [{'role': 'user', 'content': request}],
        'resources': [{'id': 'table-file', 'name': 'input.csv', 'kind': 'table', 'columns': ['qty']}],
    }
    tasks = [
        {'id': 'r1', 'source_message': 0, 'quote': request, 'description': '빈 값 채우기',
         'depends_on': [], 'spec': {'kind': 'fill_missing', 'fields': ['qty'], 'value': expected}},
        {'id': 'r2', 'source_message': 0, 'quote': request, 'description': 'JSON 저장',
         'depends_on': ['r1'], 'spec': {'kind': 'export', 'format': 'json'}},
    ]
    ledger = validate_plan({'tasks': tasks, 'unresolved': []}, state)

    def node(identifier, tool, source=None, **settings):
        defaults = dict(file_ids=[], query=None, fields=[], instructions=None, format=None,
                        question=None, condition_field=None, condition_operator=None)
        return dict(id=identifier, tool=tool, label=identifier,
                    bindings=[dict(input='table', node_id=source, output='table')] if source else [],
                    settings={**defaults, **settings})

    workflow = {
        'entry': 'read',
        'nodes': [node('read', 'read_table', file_ids=['table-file']),
                  node('fill', 'filter_sort', 'read', operation='handle_missing',
                       arguments_json=json.dumps({'columns': ['qty'], 'action': 'fill', 'fill_value': actual})),
                  node('out', 'write_result', 'fill', format='json')],
        'edges': [dict(source='read', target='fill', branch='next'),
                  dict(source='fill', target='out', branch='next')],
    }
    draft = AgentDraft.model_validate({
        'name': '빈 값 처리', 'purpose': request, 'instructions': request,
        'tool_ids': ['read_table', 'filter_sort', 'write_result'], 'document_ids': ['table-file'],
        'requirements': [{'description': '요청 처리', 'source_id': 'user', 'quote': request}],
        'questions': [], 'unsupported': [], 'explanation': '빈 값을 채워 저장합니다.',
        'execution_plan': dict(filters=[], sort=[], match='all', missing_policy='keep',
                               duplicate_policy='keep', duplicate_field=None, output_format='json'),
        'workflow': workflow,
    }).model_dump()
    return {**state, 'draft': draft, 'requirement_required': True, 'requirement_ledger': ledger,
            'requirement_fingerprint': ledger_fingerprint(state), 'requirement_digest': plan_digest(ledger)}


@pytest.mark.parametrize('expected,actual', [
    (0, False), (False, 0), (1, True), (True, 1),
    (0.0, False), (False, 0.0), (1.0, True), (True, 1.0),
    (0, '0'), ('0', 0), (False, 'false'), ('false', False),
])
def test_wrong_scalar_type_cannot_be_ready(expected, actual):
    result = validate_node(fill_state(expected, actual))
    assert result['status'] != 'ready'
    assert result['requirement_coverage']['tasks'][0]['status'] == 'missing_or_mismatched'


@pytest.mark.parametrize('expected,actual', [
    (0, 0), (0, 0.0), (0.0, 0), (1, 1.0), (1.0, 1),
    (False, False), (True, True), ('0', '0'), ('false', 'false'),
])
def test_equivalent_scalars_remain_ready(expected, actual):
    result = validate_node(fill_state(expected, actual))
    assert result['status'] == 'ready', result['issues']
    assert result['requirement_coverage']['tasks'][0]['status'] == 'covered'
    output = execute('filter_sort', 'handle_missing', {
        'table': {'columns': ['qty'], 'rows': [{'qty': None}]},
        'columns': ['qty'], 'action': 'fill', 'fill_value': actual,
    })
    value = output['table']['rows'][0]['qty']
    assert type(value) is type(actual)
    assert value == actual
