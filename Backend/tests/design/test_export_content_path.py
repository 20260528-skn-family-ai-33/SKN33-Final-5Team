import json

import pytest

from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest
from infrastructure.persistence.storage import Storage
from tests.design.test_requirement_coverage import seed
from tests.workflow_engine.test_workflow import bind, edge, node, saved
from workflow_engine.runner import run_workflow


def report_with_dynamic_name(body_source, operation):
    state = seed()
    draft = state['draft']
    workflow = draft['workflow']
    # The first filtered row supplies a filename. The body is chosen separately.
    filename = node('filename', 'json_transform', [bind('value', 'filter', 'table')],
                    operation='lookup', arguments_json=json.dumps({'path': ['rows', 0, 'filename']}))
    path_port = 'filename' if operation == 'serialize' else 'path'
    args = {'format': 'json'} if operation == 'serialize' else {}
    writer = node('out', 'export_result',
                  [bind(path_port, 'filename', 'text'), bind('value', body_source, 'table')],
                  operation=operation, arguments_json=json.dumps(args))
    workflow['nodes'][-1:] = [filename, writer]
    workflow['edges'][-1:] = [edge('filter', 'filename'), edge('filename', 'out')]
    draft['tool_ids'][-1:] = ['json_transform', 'export_result']
    return state


@pytest.mark.parametrize('operation', ['write_json', 'serialize'])
def test_using_filtered_rows_for_only_the_filename_cannot_pass_the_save_gate(tmp_path, operation):
    state = report_with_dynamic_name('read', operation)
    assert validate_node(state)['status'] != 'ready'
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='최종 저장 경로'):
            store.save_agent({**state, 'confirmed': True, 'status': 'ready'})
        assert not store.agents()
    finally:
        store.close()


def test_filtered_headers_do_not_prove_the_csv_contains_filtered_rows():
    state = seed()
    request = state['messages'][0]['content'].replace('JSON', 'CSV')
    state['messages'][0]['content'] = request
    for task in state['requirement_ledger']['tasks']:
        task['quote'] = request
    state['draft']['requirements'][0]['quote'] = request
    state['draft'].update(instructions=request, purpose=request)
    wf = state['draft']['workflow']
    raw_rows = node('rows', 'json_transform', [bind('value', 'read', 'table')],
                    operation='lookup', output_port='records', arguments_json='{"path":["rows"]}')
    columns = node('headers', 'json_transform', [bind('value', 'filter', 'table')],
                   operation='lookup', arguments_json='{"path":["columns"]}')
    writer = node('out', 'export_result',
                  [bind('rows', 'rows', 'records'), bind('columns', 'headers', 'result')],
                  operation='write_csv', arguments_json='{"path":"report.csv"}')
    wf['nodes'][-1:] = [raw_rows, columns, writer]
    wf['edges'][-1:] = [edge('filter', 'rows'), edge('rows', 'headers'), edge('headers', 'out')]
    state['draft']['tool_ids'][-1:] = ['json_transform', 'export_result']
    export = next(t for t in state['requirement_ledger']['tasks'] if t['spec']['kind'] == 'export')
    export['spec']['format'] = 'csv'
    state['requirement_fingerprint'] = ledger_fingerprint(state)
    state['requirement_digest'] = plan_digest(state['requirement_ledger'])
    result = validate_node(state)
    assert any('최종 저장 경로' in issue for issue in result['issues'])


@pytest.mark.parametrize('wrapped', [False, True])
def test_processed_json_body_with_dynamic_filename_is_downloadable(tmp_path, wrapped):
    state = report_with_dynamic_name('filter', 'write_json')
    if wrapped:
        writer = state['draft']['workflow']['nodes'][-1]
        writer['settings']['arguments_json'] = json.dumps({'value': {'report': {'$binding': 'value'}}})
    assert validate_node(state)['status'] == 'ready'
    store, identifier, _ = saved(tmp_path, state['draft']['workflow'])
    try:
        rows = [{'qty': 1, 'filename': 'small.json'}, {'qty': 3, 'filename': 'report.json'}]
        store.add_asset(dict(id='input', name='sales.csv', kind='table', columns=list(rows[0])), rows)
        run = run_workflow(store, identifier, '실행', 'unused', file_ids=['input'])
        assert run['status'] == 'succeeded', run.get('error')
        data = json.loads((tmp_path / run['report_path']).read_text(encoding='utf-8'))
        if wrapped:
            data = data['report']
        assert data['rows'] == [{'qty': 3, 'filename': 'report.json', 'amount': 30}]
    finally:
        store.close()
