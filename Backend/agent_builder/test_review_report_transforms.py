"""Diagnostic report transforms must keep their unapproved-report provenance."""
import copy
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest

from agent_builder.review_decisions import approved_csv
from agent_builder.test_node_connections import save
from agent_builder.test_reply_review import output, seeded
from agent_builder.test_workflow import bind, edge, node
from agent_builder.workflow_runtime import execute_node, run_workflow


def report_config(base):
    config = copy.deepcopy(base)
    plan = copy.deepcopy(config['execution_plan'])
    plan['filters'] = [{'field': 'verdict', 'operator': 'eq', 'value': 'revise',
                        'compare_field': None, 'value_type': 'text'}]
    config['workflow']['nodes'] += [
        node('summary', 'review_summary', [bind('reviews', 'review', 'reviews')]),
        node('filter', 'filter_sort', [bind('table', 'summary', 'table')], execution_plan=plan),
        node('aggregate', 'aggregate_table', [bind('table', 'filter', 'table')],
             fields=['verdict', 'approval_state'], aggregation='count'),
        node('write', 'write_result', [bind('table', 'aggregate', 'table')], format='csv'),
    ]
    config['workflow']['edges'] += [edge('review', 'summary'), edge('summary', 'filter'),
                                    edge('filter', 'aggregate'), edge('aggregate', 'write')]
    config['tool_ids'] += ['review_summary', 'filter_sort', 'aggregate_table', 'write_result']
    return config


def assert_pending_report(storage, directory, agent, run):
    assert run['status'] == 'succeeded', run.get('error')
    assert run['approval_status'] == 'pending'
    assert len(run['reviews']) == 2
    assert run['artifact']['name'] == '검수_보고서.csv'
    assert run['artifact']['review_report'] is True
    assert (directory / run['report_path']).is_file()
    rows = list(csv.DictReader(io.StringIO(run['artifact']['content'].lstrip('\ufeff'))))
    assert rows == [{'verdict': 'revise', 'approval_state': 'pending', 'count': '2'}]
    approved = list(csv.DictReader(io.StringIO(approved_csv(storage, agent)['content'].lstrip('\ufeff'))))
    assert approved == []


def test_review_report_filters_and_aggregates_before_saving(tmp_path, monkeypatch):
    monkeypatch.setattr('agent_builder.reply_review.run_review', lambda *args: output())
    storage, baseline = seeded(tmp_path, table=True)
    try:
        agent = save(storage, report_config(storage.agent(baseline)['config']))
        run = run_workflow(storage, agent, '검수 통계를 저장하세요.', 'unused', llm=object())
        assert_pending_report(storage, tmp_path, agent, run)
        for source in ['summary', 'filter', 'aggregate']:
            assert run['outputs'][source]['table']['review_report'] is True
        assert len(run['outputs']['filter']['table']['rows']) == 2
        assert len(run['outputs']['aggregate']['table']['rows']) == 1
    finally:
        storage.close()


@pytest.mark.parametrize('port', ['table', 'artifact'])
def test_reused_transformed_reports_save_without_becoming_approved_replies(tmp_path, monkeypatch, port):
    monkeypatch.setattr('agent_builder.reply_review.run_review', lambda *args: output())
    storage, baseline = seeded(tmp_path, table=True)
    try:
        child_config = report_config(storage.agent(baseline)['config'])
        child = save(storage, child_config)
        parent = copy.deepcopy(child_config)
        call = node('call', 'saved_workflow', agent_id=child, agent_version=1,
                    output_node='aggregate' if port == 'table' else 'write', output_port=port)
        nodes = [call]
        edges = []
        if port == 'table':
            nodes.append(node('write', 'write_result', [bind('table', 'call', 'table')], format='csv'))
            edges.append(edge('call', 'write'))
        parent.update(tool_ids=['saved_workflow'] + (['write_result'] if port == 'table' else []),
                      workflow={'entry': 'call', 'nodes': nodes, 'edges': edges})
        agent = save(storage, parent)
        run = run_workflow(storage, agent, '검수 통계를 재사용하세요.', 'unused', llm=object())
        assert_pending_report(storage, tmp_path, agent, run)
        assert [review['id'] for review in run['reviews']] == ['call:0', 'call:1']
        assert run['outputs']['call'][port]['review_report'] is True
    finally:
        storage.close()


@pytest.mark.parametrize('tool', ['filter_sort', 'aggregate_table'])
@pytest.mark.parametrize('marker', [None, False, 'true', 1])
def test_ordinary_tables_do_not_gain_review_report_marker(tool, marker):
    table = {'rows': [{'verdict': 'revise'}], 'columns': ['verdict'], 'file_id': None}
    if marker is not None:
        table['review_report'] = marker
    state = {'outputs': {'input': {'table': table}}}
    plan = {'filters': [], 'sort': [], 'match': 'all', 'missing_policy': 'keep',
            'duplicate_policy': 'keep', 'duplicate_field': None, 'output_format': 'csv'}
    operation = node('operation', tool, [bind('table', 'input', 'table')],
                     fields=['verdict'], aggregation='count')
    result = execute_node(operation, state, None, {'execution_plan': plan}, None)
    assert 'review_report' not in result['table']
    assert table.get('review_report') == marker
