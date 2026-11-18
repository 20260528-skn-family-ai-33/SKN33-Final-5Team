"""Persisted grouped results keep the requested name, rows and run ownership."""
import base64
import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from service.worker import handle
from tests.design.test_feedback_service_scenarios import UnexpectedConditionCall
from tests.design.test_grouped_totals_request import controlled_draft, upload_profile
from tests.design.test_manual_kit_requirement_regressions import REGIONAL_REQUEST
from tests.design.test_review151_followup_contract import contract_packet
from tests.workflow_engine.test_workflow import bind, edge, node, saved
from workflow_engine.runner import run_workflow


COLUMNS = ['주문번호', '주문상태', '지역', '수량', '단가']
RESULT_COLUMNS = ['지역', '총매출', '주문건수']


def orders(*values):
    return [dict(zip(COLUMNS, (f'O{index:03d}', '완료', region, quantity, price)))
            for index, (region, quantity, price) in enumerate(values, 1)]


@pytest.fixture
def save_grouped_workflow(tmp_path, monkeypatch):
    import service.handlers.design as design

    class Capabilities:
        def invoke(self, messages):
            return dict(content=json.dumps(dict(decision='existing_tools', reason='표 집계',
                tool_ids=['read_table', 'calculate_columns', 'filter_sort', 'aggregate_table',
                          'join_tables', 'json_transform', 'export_result'], missing_conditions=[],
                missing_actions=[], user_quotes=[REGIONAL_REQUEST])))

    class Requirements:
        def invoke(self, messages):
            payload = json.loads(messages[1]['content'])
            packet = contract_packet(dict(messages=payload['conversation'], resources=payload['resources']))
            for task in packet['tasks']:
                if task['spec']['kind'] == 'sort':
                    task['spec']['sort'][0]['value_type'] = 'text'
            return dict(content=json.dumps(packet))

    build = design.build_graph
    monkeypatch.setattr(design, 'build_graph', lambda *a, **kw: build(*a, **{**kw,
        'capability_mode':'enforce', 'capability_transport':Capabilities(), 'condition_mode':'enforce',
        'condition_transport':UnexpectedConditionCall(), 'requirement_mode':'enforce',
        'requirement_transport':Requirements(), 'verifier_mode':'off'}))

    def save(fmt='csv'):
        base = dict(directory=str(tmp_path))
        result = handle({**base, 'action':'new_session', 'model':'fixture', 'credential_id':'fixture'})
        session = result['id']
        rows = orders(('서울', 2, 3))
        result = handle({**base, 'action':'upload', 'id':session, 'revision':0, 'rows':rows,
            'asset':dict(name='주문내역.csv', kind='table', format='csv', columns=COLUMNS, **upload_profile(COLUMNS, rows))})
        followup = ['같은 열을 JSON으로 저장해줘.'] if fmt == 'json' else []
        for request in [REGIONAL_REQUEST, *followup]:
            result = handle({**base, 'action':'message', 'id':session, 'revision':result['revision'], 'content':request},
                conversation=lambda state: dict(draft=controlled_draft(state)))
            assert result['status'] == 'ready', result.get('issues')
        result = handle({**base, 'action':'confirm', 'id':session, 'revision':result['revision']})
        assert result['status'] == 'saved', result.get('issues')
        return {**base, 'action':'test', 'id':result['saved_agent_id'], 'content':''}

    return save


def replacement(run_request, rows):
    uploaded = handle({**run_request, 'action':'run_upload', 'rows':rows,
        'asset':dict(name='새 주문.json', kind='table', format='json', columns=COLUMNS, **upload_profile(COLUMNS, rows))})
    return {**run_request, 'file_ids':[uploaded['asset']['id']]}


def assert_result(run, fmt, expected):
    assert run['status'] == 'succeeded', run.get('error')
    assert run['artifact']['name'] == '지역별매출.' + fmt
    content = base64.b64decode(run['artifact']['content'])
    if fmt == 'json':
        assert json.loads(content) == expected
    else:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
        assert reader.fieldnames == RESULT_COLUMNS
        assert list(reader) == [{k: '' if v is None else str(v) for k, v in row.items()} for row in expected]
    return content


@pytest.mark.parametrize('fmt', ['csv', 'json'])
def test_saved_grouped_output_can_run_repeatedly_and_concurrently(save_grouped_workflow, tmp_path, fmt):
    request = save_grouped_workflow(fmt)
    first = handle(request)
    original = assert_result(first, fmt, [dict(지역='서울', 총매출=6, 주문건수=1)])
    assert assert_result(handle(request), fmt, [dict(지역='서울', 총매출=6, 주문건수=1)]) == original
    variants = [('부산', 7), ('제주', 11)]
    requests = [replacement(request, orders((region, 1, price))) for region, price in variants]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(handle, requests))
    for result, (region, price) in zip(results, variants):
        content = assert_result(result, fmt, [dict(지역=region, 총매출=price, 주문건수=1)])
        assert (tmp_path / result['report_path']).read_bytes() == content
    assert (tmp_path / first['report_path']).read_bytes() == original


def test_grouped_output_retry_after_a_write_that_was_not_checkpointed(save_grouped_workflow, monkeypatch):
    from workflow_engine.executors import canonical_operation

    request = save_grouped_workflow()
    original = canonical_operation.file_artifact
    written_paths = []

    def interrupted_once(asset, context):
        artifact = original(asset, context)
        written_paths.append(asset['path'])
        if len(written_paths) == 1:
            raise OSError('interrupted after export file write')
        return artifact

    monkeypatch.setattr(canonical_operation, 'file_artifact', interrupted_once)
    first = handle(request)
    assert first['status'] == 'failed' and first['error_type'] == 'OSError'
    retried = handle({**request, 'action':'retry_run', 'id':first['id']})
    assert retried['id'] == first['id']
    assert_result(retried, 'csv', [dict(지역='서울', 총매출=6, 주문건수=1)])
    assert len(set(written_paths)) == 2


@pytest.mark.parametrize('fmt', ['csv', 'json'])
def test_null_group_on_a_replacement_input_keeps_its_sum_and_count(save_grouped_workflow, fmt):
    request = save_grouped_workflow(fmt)
    rows = orders((None, 2, 3), ('서울', 2, 5), (None, 0, 100))
    run = handle(replacement(request, rows))
    assert_result(run, fmt, [dict(지역='서울', 총매출=10, 주문건수=1), dict(지역=None, 총매출=6, 주문건수=2)])


def test_grouped_join_matches_distinct_scalar_types_and_keeps_regular_null_policy():
    from workflow_engine.unified_tools.local_operations import join

    keys = [None, '', True, 1, 1.0]
    left = [dict(key=key, total=index) for index, key in enumerate(keys)]
    right = [dict(key=key, count=index + 1) for index, key in enumerate(keys)]
    with pytest.raises(ValueError, match='Null join key'):
        join(left, right, ['key'], 'inner', 'one_to_one', 'error', 100)
    result = join(left, right, ['key'], 'inner', 'one_to_one', 'match', 100)
    assert result['table']['rows'] == [dict(row, count=row['total'] + 1) for row in left]


@pytest.mark.parametrize('literal', [False, True], ids=['asset-binding', 'literal-path'])
def test_export_remains_readable_by_a_downstream_canonical_operation(tmp_path, literal):
    writer = node('write', 'export_result', operation='write_csv',
                  arguments_json=dict(path='결과.CSV', columns=['id'], rows=[{'id':'A'}]))
    reader = (node('read', 'read_table', operation='read_csv', arguments_json=dict(path='결과.CSV')) if literal
              else node('read', 'read_table', [bind('path', 'write', 'asset')], operation='read_csv'))
    existing = tmp_path / 'operation-files' / '결과.csv'
    existing.parent.mkdir()
    existing.write_bytes(b'previous export')
    store, identifier, _ = saved(tmp_path, dict(entry='write', nodes=[writer, reader], edges=[edge('write', 'read')]))
    try:
        results = [run_workflow(store, identifier, '실행', '') for _ in range(2)]
        for run in results:
            assert run['status'] == 'succeeded', run.get('error')
            assert run['outputs']['write']['artifact']['name'] == '결과.CSV'
            assert run['outputs']['read']['records'] == [{'id':'A'}]
        assert len({run['outputs']['write']['asset']['path'] for run in results}) == 2
        assert existing.read_bytes() == b'previous export'
    finally:
        store.close()


def test_retry_reads_a_completed_export_by_literal_path_after_reopening_storage(tmp_path):
    from workflow_engine.executors.dispatch import execute_node

    path = 'result.csv'
    writer = node('write', 'export_result', operation='write_csv',
                  arguments_json=dict(path=path, columns=['id'], rows=[{'id':'A'}]))
    reader = node('read', 'read_table', operation='read_csv', arguments_json=dict(path=path))
    store, identifier, _ = saved(tmp_path, dict(entry='write', nodes=[writer, reader], edges=[edge('write', 'read')]))

    def stop_before_read(current, state, storage, config, llm):
        if current['id'] == 'read':
            raise OSError('interrupted after checkpointing the export')
        return execute_node(current, state, storage, config, llm)

    try:
        first = run_workflow(store, identifier, '실행', '', executor=stop_before_read)
        assert first['status'] == 'failed' and first['error_type'] == 'OSError'
    finally:
        store.close()
    retried = handle(dict(directory=str(tmp_path), action='retry_run', id=first['id']))
    assert retried['status'] == 'succeeded', retried.get('error')
    assert retried['outputs']['write']['asset'] == first['outputs']['write']['asset']
    assert retried['outputs']['read']['records'] == [{'id':'A'}]


def test_explicit_overwrite_retains_shared_location_and_replaces_the_run_alias(tmp_path):
    path = 'result.csv'
    first = node('first', 'export_result', operation='write_csv',
                 arguments_json=dict(path=path, columns=['id'], rows=[{'id':'isolated'}]))
    overwrite = node('overwrite', 'export_result', operation='write_csv',
                     arguments_json=dict(path=path, columns=['id'], rows=[{'id':'shared'}], overwrite=True))
    reader = node('read', 'read_table', operation='read_csv', arguments_json=dict(path=path))
    later = node('later', 'export_result', operation='write_csv',
                 arguments_json=dict(path=path, columns=['id'], rows=[{'id':'later isolated'}]))
    read_later = node('read_later', 'read_table', operation='read_csv', arguments_json=dict(path=path))
    read_shared = node('read_shared', 'read_table', [bind('path', 'overwrite', 'asset')], operation='read_csv')
    existing = tmp_path / 'operation-files' / path
    existing.parent.mkdir()
    existing.write_text('id\nold\n', encoding='utf-8')
    nodes = [first, overwrite, reader, later, read_later, read_shared]
    workflow = dict(entry='first', nodes=nodes, edges=[edge(a['id'], b['id']) for a, b in zip(nodes, nodes[1:])])
    store, identifier, _ = saved(tmp_path, workflow)
    try:
        run = run_workflow(store, identifier, '실행', '')
        assert run['status'] == 'succeeded', run.get('error')
        assert run['outputs']['overwrite']['asset']['path'] == path
        assert run['outputs']['read']['records'] == [{'id':'shared'}]
        assert run['outputs']['read_later']['records'] == [{'id':'later isolated'}]
        assert run['outputs']['read_shared']['records'] == [{'id':'shared'}]
        artifact = run['outputs']['overwrite']['artifact']
        assert base64.b64decode(artifact['content']) == existing.read_bytes()
    finally:
        store.close()
    store, identifier, _ = saved(tmp_path, dict(entry='read', nodes=[reader], edges=[]))
    try:
        other = run_workflow(store, identifier, '실행', '')
        assert other['status'] == 'succeeded', other.get('error')
        assert other['rows'] == [{'id':'shared'}]
    finally:
        store.close()
