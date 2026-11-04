import json

import pytest

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_coverage import coverage_report
from tests.design.test_pr27_review_regressions import source_case
from tests.design.test_requirement_coverage import joined
from tests.workflow_engine.test_workflow import bind, edge, node


def export_state():
    state = source_case('A')
    request = state['messages'][0]['content']
    state['draft']['requirements'] = [dict(description=request, source_id='user', quote=request)]
    state['draft']['execution_plan'].update(filters=[], sort=[])
    return state


@pytest.mark.parametrize(('path', 'ready'), [
    ({'path': 'inputs/B.csv', 'metadata': {'asset_id': 'A'}}, False),
    ({'asset_id': 'A', 'path': 'ignored.csv', 'metadata': {'asset_id': 'B'}}, True),
])
def test_csv_file_requirement_follows_the_opened_file(path, ready):
    state = export_state()
    reader = state['draft']['workflow']['nodes'][0]
    reader['settings']['arguments_json'] = json.dumps({'path': path})
    result = validate_node(state)
    assert (result['status'] == 'ready') is ready, result['issues']
    if not ready:
        with pytest.raises(ValueError, match='요구사항 검증'):
            require_save_ready({**state, 'confirmed': True, 'status': 'ready'})


def test_file_containing_another_filename_is_not_the_exported_csv():
    state = export_state()
    workflow = state['draft']['workflow']
    # A is a text file containing "inputs/B.csv". The final JSON contains B's rows.
    file_path = node('filename', 'read_document', operation='read_text',
                     arguments_json=json.dumps({'path': {'asset_id': 'A'}}))
    workflow['nodes'].insert(0, file_path)
    workflow['entry'] = 'filename'
    workflow['edges'].insert(0, edge('filename', 'read'))
    reader = workflow['nodes'][1]
    reader['bindings'] = [bind('path', 'filename', 'text')]
    reader['settings']['arguments_json'] = '{}'
    state['draft']['tool_ids'].append('read_document')
    with pytest.raises(ValueError, match='요구사항 검증'):
        require_save_ready({**state, 'confirmed': True, 'status': 'ready'})


def test_join_cannot_swap_the_requested_files_through_explicit_references():
    state = joined()
    join = state['draft']['workflow']['nodes'][2]
    args = json.loads(join['settings']['arguments_json'])
    args.update(left={'$binding': 'right'}, right={'$binding': 'left'})
    join['settings']['arguments_json'] = json.dumps(args)
    report = coverage_report(state, state['draft'])
    assert next(task for task in report['tasks'] if task['kind'] == 'join')['status'] == 'missing_or_mismatched'
