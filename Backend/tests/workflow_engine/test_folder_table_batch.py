"""Folder batches must combine every compatible table without losing bad inputs."""
from copy import deepcopy
import csv
import io
import json

import pytest

from workflow_engine.executors.table_batch import merge_table_assets


def item(name, columns, rows):
    return {'asset': {'id': name, 'name': name, 'kind': 'table', 'columns': columns}, 'rows': rows}


def test_batch_combines_reordered_headers_and_preserves_zero_missing_values_and_sources():
    inputs = [item('one.csv', ['team', 'amount'], [{'team': 'A', 'amount': 0}, {'team': 'B', 'amount': None}]),
              item('two.csv', ['amount', 'team'], [{'team': 'A', 'amount': 250}])]
    before = deepcopy(inputs)
    merged = merge_table_assets(inputs)
    assert merged == {'columns': ['team', 'amount'], 'rows': [
        {'team': 'A', 'amount': 0}, {'team': 'B', 'amount': None}, {'team': 'A', 'amount': 250}],
        'source_asset_ids': ['one.csv', 'two.csv'], 'row_count': 3}
    merged['rows'][0]['amount'] = 123
    assert inputs == before


def test_batch_rejects_mismatching_schema_including_an_empty_file_instead_of_omitting_it():
    inputs = [item('one.csv', ['team', 'amount'], [{'team': 'A', 'amount': 1}]),
              item('wrong.csv', ['team', 'total'], [])]
    with pytest.raises(ValueError, match='wrong.csv.*없는 열: amount.*다른 열: total'):
        merge_table_assets(inputs)


@pytest.mark.parametrize('invalid', [
    item('broken.csv', ['amount', 'amount'], []),
    item('broken.csv', [], []),
    item('broken.csv', ['amount'], [{'amount': 1, 'hidden': 999}]),
    item('broken.csv', ['amount'], [3]),
])
def test_batch_rejects_malformed_table_data(invalid):
    with pytest.raises(ValueError, match='broken.csv'):
        merge_table_assets([invalid])


def test_batch_empty_folder_is_an_actionable_error_and_empty_tables_still_keep_the_schema():
    with pytest.raises(ValueError, match='폴더.*표 파일'):
        merge_table_assets([])
    assert merge_table_assets([item('empty.csv', ['amount'], [])])['columns'] == ['amount']


@pytest.fixture
def report_folder(tmp_path, monkeypatch):
    from contracts.models import AgentDraft
    from infrastructure.persistence.asset_library import AssetLibrary
    from infrastructure.persistence.ids import uid
    from infrastructure.persistence.storage import Storage
    from tests.workflow_engine.test_gmail_scheduling import bind, draft, edge, initial, node
    from workflow_engine.scheduling.library_sources import LibrarySources

    monkeypatch.setenv('A4B_STORAGE', 'sqlite')
    monkeypatch.setenv('EASYFLOW_PUBLIC_URL', 'http://localhost')
    monkeypatch.delenv('A4B_BUSINESS_RECORDS', raising=False)
    store = Storage(tmp_path)
    library = AssetLibrary(store)
    folder = library.create_folder('매출 보고 자료')
    nodes = [node('read', 'read_table'),
             node('sum', 'aggregate_table', [bind('table', 'read', 'table')],
                  fields=['team'], aggregation='sum', sum_field='amount'),
             node('write', 'write_result', [bind('table', 'sum', 'table')], format='csv')]
    config = {**draft(), 'tool_ids': [n['tool'] for n in nodes],
              'workflow': {'entry': 'read', 'nodes': nodes,
                           'edges': [edge('read', 'sum'), edge('sum', 'write')]}}
    agent_id = store.save_agent({**initial(), 'confirmed': True, 'draft': AgentDraft.model_validate(config).model_dump()})
    manager = LibrarySources(store)
    selection = manager.save(agent_id, {'mode': 'all', 'space': 'personal', 'team_id': None,
                                       'folder_id': folder['id'], 'asset_ids': [], 'target_node_id': 'read'}, 0)

    def upload(name, rows, columns=None):
        return library.upload({'name': name, 'kind': 'table', 'format': 'csv',
                               'columns': columns or ['team', 'amount'], 'profile': {},
                               'processing_status': 'ready', 'text': '', 'version': 1,
                               'hash': name}, rows, folder['id'])

    def resolve():
        from workflow_engine.scheduling.library_sources import library_export
        refs = manager.resolve('test', agent_id, library_export(store, selection, 'table')['candidates'])['refs']
        run_id = uid()
        view = manager.stage(run_id, store.agent(agent_id),
                             {**library_export(store, selection, 'table', refs), 'selection': selection})
        return view, run_id, refs

    try:
        yield store, agent_id, manager, selection, upload, resolve
    finally:
        store.close()


def test_folder_tables_produce_one_aggregated_csv_and_new_runs_include_added_files(report_folder):
    from agent_builder.conditions.input_requirements import preflight_execution
    from workflow_engine.runner import run_workflow
    from workflow_engine.scheduling.library_sources import source_tags

    store, agent_id, manager, _, upload, resolve = report_folder
    upload('first.csv', [{'team': 'A', 'amount': 60}, {'team': 'B', 'amount': 80}])
    upload('second.csv', [{'team': 'A', 'amount': 60}], ['amount', 'team'])
    view, run_id, _ = resolve()
    assert preflight_execution(view.bound['config'], view)[0]['ready']
    run = run_workflow(view, agent_id, '', 'unused', forced_run_id=run_id, run_tags=source_tags(view))
    assert run['status'] == 'succeeded', run.get('error')
    assert sorted(run['rows'], key=lambda row: row['team']) == [{'team': 'A', 'sum': 120}, {'team': 'B', 'sum': 80}]
    exported = list(csv.DictReader(io.StringIO(run['artifact']['content'].lstrip('\ufeff'))))
    assert sorted(exported, key=lambda row: row['team']) == [{'team': 'A', 'sum': '120'}, {'team': 'B', 'sum': '80'}]
    assert len(run['library_source_files']) == 2
    assert [step['node'] for step in run['steps']] == ['read', 'sum', 'write']
    assert store.db.execute('SELECT COUNT(*) FROM builder_schedule_jobs WHERE {ws}').fetchone()[0] == 0
    manager.complete(run)

    upload('third.csv', [{'team': 'A', 'amount': 10}])
    fresh, next_id, _ = resolve()
    updated = run_workflow(fresh, agent_id, '', 'unused', forced_run_id=next_id, run_tags=source_tags(fresh))
    assert updated['status'] == 'succeeded', updated.get('error')
    assert sorted(updated['rows'], key=lambda row: row['team']) == [{'team': 'A', 'sum': 130}, {'team': 'B', 'sum': 80}]
    assert len(updated['library_source_files']) == 3


def test_folder_report_retry_keeps_the_original_file_set_and_contents(report_folder):
    from workflow_engine.executors.dispatch import execute_node
    from workflow_engine.runner import run_workflow
    from workflow_engine.scheduling.library_sources import source_tags

    store, agent_id, manager, selection, upload, resolve = report_folder
    first = upload('first.csv', [{'team': 'A', 'amount': 60}])
    upload('second.csv', [{'team': 'A', 'amount': 40}])
    view, run_id, refs = resolve()

    def fail_sum(node, state, storage, config, llm):
        if node['id'] == 'sum':
            raise ValueError('일시 집계 오류')
        return execute_node(node, state, storage, config, llm)

    failed = run_workflow(view, agent_id, '', 'unused', forced_run_id=run_id,
                          run_tags=source_tags(view), executor=fail_sum)
    assert failed['status'] == 'failed'
    raw = store.raw_asset(first['id'])
    raw['hash'] = 'changed'
    store.db.execute('UPDATE builder_assets SET json=?,rows_json=? WHERE {ws} AND id=?',
                     (json.dumps(raw), json.dumps([{'team': 'A', 'amount': 999}]), first['id']))
    store.db.commit()
    upload('third.csv', [{'team': 'A', 'amount': 500}])
    pinned = manager.stage(run_id, store.agent(agent_id), {'selection': selection, 'refs': refs, 'verified_only': True})
    retried = run_workflow(pinned, agent_id, '', 'unused', run_id=run_id, retry=True)
    assert retried['status'] == 'succeeded', retried.get('error')
    assert retried['rows'] == [{'team': 'A', 'sum': 100}]
    assert len(retried['library_source_files']) == 2


def test_ordinary_manual_multiple_table_uploads_still_require_an_explicit_source(report_folder):
    from agent_builder.conditions.input_requirements import preflight_execution

    store, agent_id, _, _, upload, _ = report_folder
    files = [upload('one.csv', [{'team': 'A', 'amount': 1}]), upload('two.csv', [{'team': 'A', 'amount': 2}])]
    with pytest.raises(ValueError, match='표 읽기 노드.*한 파일'):
        preflight_execution(store.agent(agent_id)['config'], store, [asset['id'] for asset in files])


def test_manual_attachment_overrides_a_saved_folder_for_only_one_run(report_folder, tmp_path):
    from service.worker import handle

    store, agent_id, manager, selection, upload, _ = report_folder
    upload('first.csv', [{'team': 'A', 'amount': 100}])
    upload('second.csv', [{'team': 'A', 'amount': 200}])
    attached = upload('override.csv', [{'team': 'A', 'amount': 7}])
    run = handle({'directory': str(tmp_path), 'action': 'test', 'id': agent_id,
                  'content': '', 'file_ids': [attached['id']]})
    assert run['status'] == 'succeeded', run.get('error')
    assert run['rows'] == [{'team': 'A', 'sum': 7}]
    assert not run.get('library_source')
    assert manager.get(agent_id) == selection
