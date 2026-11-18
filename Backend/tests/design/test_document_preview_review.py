import pytest
from langgraph.types import Command

from agent_builder.design.document_rows_template import document_rows_applies, document_rows_reply
from agent_builder.design.graph import build_graph
from documents.resources.file_inspection import inspect_file
from infrastructure.persistence.storage import Storage
from tests.design.test_document_row_integrity import (
    EXPECTED, SOURCE, NoModel, block, document, no_planner, output_rows, state,
)
from tests.design.test_document_rows_template import asked_state
from workflow_engine.runner import run_workflow


def preview_message(asset, aliases):
    return f"{asset['name']}에서 {', '.join(aliases)} 항목을 추출해 주세요."


def run_preview(tmp_path, asset, aliases, *, followup=None):
    initial = state(asset, '이 문서 CSV로 저장해줘')
    store = Storage(tmp_path)
    store.add_asset(asset, [])
    graph, connection = build_graph(store, conversation=no_planner, condition_mode='off', capability_mode='off')
    config = {'configurable': {'thread_id': initial['session_id']}}
    try:
        asked = graph.invoke(initial, config)
        assert asked['status'] == 'needs_clarification'
        ready = graph.invoke(Command(resume=dict(type='message', content=preview_message(asset, aliases))), config)
        assert ready['status'] == 'ready', ready.get('issues')
        if followup:
            ready = graph.invoke(Command(resume=dict(type='message', content=followup)), config)
            assert ready['status'] == 'ready', ready.get('issues')
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[asset['id']], llm=NoModel())
        assert result['status'] == 'succeeded', result.get('error')
        return ready['draft']['workflow']['nodes'], output_rows(result['artifact'])
    finally:
        connection.close()
        store.close()


@pytest.mark.parametrize('second_table', ['visible', 'blank-header', 'text-only'])
def test_csv_from_preview_reads_only_the_selected_parser_block(tmp_path, second_table):
    extra_header = ['', *SOURCE[0][1:]] if second_table == 'blank-header' else SOURCE[0]
    extra = block([extra_header, ['H999', '다른 표 품목', '100', '99', '1']], 'other-table', 2)
    if second_table == 'text-only':
        extra['data']['cells'] = []
    asset = document([block(SOURCE, 'selected-table'), extra], name='a.pdf')
    visible = inspect_file(asset)['tables']
    assert len(visible) == (2 if second_table == 'visible' else 1)
    assert visible[0]['block_id'] == 'selected-table'

    nodes, rows = run_preview(tmp_path, asset, ['「표 1」의 품명', '「표 1」의 실입고'])
    assert nodes[0]['settings']['file_ids'] == [asset['id']]
    assert nodes[0]['settings']['document_table_id'] == 'selected-table'
    assert rows == EXPECTED


@pytest.mark.parametrize('with_comma', [False, True])
def test_preview_keeps_source_labels_with_commas_and_slashes(tmp_path, with_comma):
    headers = ['품명', '수량/박스']
    table_rows = [['테이프', '4'], ['복사용지', '12']]
    fields = headers.copy()
    if with_comma:
        headers.extend(['금액', '금액, 원'])
        table_rows[0].extend(['30', '32'])
        table_rows[1].extend(['40', '42'])
        fields.append('금액, 원')
    asset = document([block([headers, *table_rows])], name='a.pdf')
    nodes, rows = run_preview(tmp_path, asset, [f'「표 1」의 {field}' for field in fields])
    assert nodes[1]['settings']['fields'] == fields
    assert list(rows[0]) == fields
    assert rows == [{field: row[headers.index(field)] for field in fields} for row in table_rows]


def test_field_addition_preserves_preview_scope(tmp_path):
    extra = block([SOURCE[0], ['other', '다른 표 품목', '999', '999', '0']], 'other-table', 2)
    asset = document([block(SOURCE, 'selected-table'), extra], name='a.pdf')
    nodes, rows = run_preview(tmp_path, asset, ['「표 1」의 품명'], followup='실입고도 추가해줘')
    assert nodes[0]['settings']['document_table_id'] == 'selected-table'
    assert rows == EXPECTED


@pytest.mark.parametrize('replacement', [None, 'other-table'])
def test_save_rejects_a_preview_workflow_with_changed_table_scope(tmp_path, replacement):
    asset = document([block(SOURCE, 'selected-table'), block(SOURCE, 'other-table', 2)], name='a.pdf')
    content = preview_message(asset, ['「표 1」의 품명', '「표 1」의 실입고'])
    current = asked_state(content, request='이 문서 CSV로 저장해줘', doc=asset)
    current.update(document_rows_reply(current))
    current.update(status='ready', confirmed=True)
    current['draft']['workflow']['nodes'][0]['settings']['document_table_id'] = replacement
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='미리보기에서 선택한 원본 문서와 표'):
            store.save_agent(current)
    finally:
        store.close()


def test_run_rebinds_a_same_named_document_by_unique_header_not_table_id(tmp_path):
    asset = document([block(SOURCE, 'selected-table')], name='a.pdf')
    replacement = dict(asset, id='replacement')
    content = preview_message(asset, ['「표 1」의 품명', '「표 1」의 실입고'])
    current = asked_state(content, request='이 문서 CSV로 저장해줘', doc=asset)
    current.update(document_rows_reply(current), status='ready', confirmed=True)
    store = Storage(tmp_path)
    try:
        store.add_asset(asset, [])
        store.add_asset(replacement, [])
        result = run_workflow(store, store.save_agent(current), '실행', '', file_ids=[replacement['id']], llm=NoModel())
        assert result['status'] == 'succeeded', result.get('error')
        assert output_rows(result['artifact']) == EXPECTED
        assert all(record['evidence']['품명']['file_id'] == 'replacement'
                   for record in result['outputs']['extract']['records'])
    finally:
        store.close()


@pytest.mark.parametrize('aliases', [
    ['「표 1」의 품명', '「표 2」의 실입고'],
    ['「표 1」의 품명', '「표 1」의 존재하지 않는 열'],
])
def test_scoped_selection_outside_fixed_recipe_does_not_become_plain_field_names(aliases):
    asset = document([block(SOURCE), block(SOURCE, 'other-table', 2)], name='a.csv.pdf')
    content = preview_message(asset, aliases)
    for current in (asked_state(content, request='이 문서 CSV로 저장해줘', doc=asset), state(asset, content)):
        assert not document_rows_applies(current)
        assert document_rows_reply(current) is None


def test_legacy_document_draft_can_still_add_fields():
    asset = document()
    current = asked_state('품명', request='이 문서 CSV로 저장해줘', doc=asset)
    current.update(document_rows_reply(current))
    for node in current['draft']['workflow']['nodes']:
        node['settings'].pop('document_table_id', None)
    current['messages'].append({'role': 'user', 'content': '실입고도 추가해줘'})
    revised = document_rows_reply(current)
    assert revised['draft']['workflow']['nodes'][1]['settings']['fields'] == ['품명', '실입고']
