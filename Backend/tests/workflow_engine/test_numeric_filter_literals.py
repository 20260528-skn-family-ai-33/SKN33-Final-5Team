import json

import pytest

from tests.workflow_engine.test_workflow import bind, edge, node, saved
from workflow_engine.executors.legacy_tools.filter_sort import filter_sort_node
from workflow_engine.runner import run_workflow


def test_saved_grouped_threshold_executes_at_the_exact_boundary(tmp_path):
    criterion = dict(field='qty', operator='gte', value_type='number', value='1,000.25',
                     compare_field=None)
    plan = dict(filters=[criterion], sort=[], match='all', missing_policy='error',
                duplicate_policy='keep', duplicate_field=None, output_format='json')
    workflow = dict(entry='read', nodes=[
        node('read', 'read_table'),
        node('filter', 'filter_sort', [bind('table', 'read', 'table')], execution_plan=plan),
        node('out', 'write_result', [bind('table', 'filter', 'table')], format='json'),
    ], edges=[edge('read', 'filter'), edge('filter', 'out')])
    store, identifier, _ = saved(tmp_path, workflow)
    try:
        rows = [{'qty': '1000.24'}, {'qty': '1000.25'}, {'qty': '1001'}]
        store.add_asset(dict(id='data', name='qty.csv', kind='table', columns=['qty']), rows)
        result = run_workflow(store, identifier, '실행', '', file_ids=['data'])
        assert result['status'] == 'succeeded', result.get('error')
        assert json.loads((tmp_path / result['report_path']).read_text(encoding='utf-8')) == rows[1:]
    finally:
        store.close()


def test_malformed_grouped_threshold_is_rejected_even_if_no_rows_require_comparison():
    empty_table = dict(columns=['qty'], rows=[], trace=[])
    criterion = dict(field='qty', operator='gte', value_type='number', value='1,00',
                     compare_field=None)
    with pytest.raises(ValueError):
        filter_sort_node(empty_table, [criterion], [], missing_policy='keep')
