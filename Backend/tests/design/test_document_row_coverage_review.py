import pytest

from agent_builder.design.document_rows_template import QUESTION, document_rows_reply
from documents.resources.document_resources import chunks_for_asset
from infrastructure.persistence.storage import Storage
from tests.design.test_document_row_integrity import EXPECTED, NoModel, SOURCE, block, document, native, output_rows, state
from workflow_engine.runner import run_workflow


def test_preserving_document_extraction_conditions_does_not_edit_an_attached_csv(tmp_path):
    asset = document()
    draft_state = state(asset)
    draft_state['messages'] += [{'role': 'assistant', 'content': QUESTION},
                                {'role': 'user', 'content': '품명, 실입고'}]
    draft_state.update(document_rows_reply(draft_state), status='ready', confirmed=True)
    table = dict(id='other', kind='table', name='별도.csv', format='csv', columns=['품명', '실입고'], version=1)
    draft_state['resources'].append(table)
    draft_state['messages'].append({'role': 'user', 'content': '기존 문서 추출 조건은 유지해줘.'})

    storage = Storage(tmp_path)
    try:
        storage.add_asset(asset, [])
        storage.add_asset(table, [{'품명': '별도 자료', '실입고': 99}])
        saved_id = storage.save_agent(draft_state)
        result = run_workflow(storage, saved_id, '워크플로우 실행', '', file_ids=[asset['id']], llm=NoModel())
        assert result['status'] == 'succeeded', result.get('error')
        assert output_rows(result['artifact']) == EXPECTED
    finally:
        storage.close()


@pytest.mark.parametrize('has_text', [True, False])
def test_reviewed_document_cannot_export_only_its_structured_page(tmp_path, has_text, monkeypatch):
    first = block(SOURCE[:3])
    second = block([SOURCE[0], *SOURCE[3:]], 'second', 2)
    second['data'] = {}
    if not has_text:
        second['text'] = ''
    asset = document([first, second])
    asset['parser_metadata']['document_analysis']['review_required'] = True
    asset['parser_metadata']['document_review'] = {'status': 'completed', 'revision': 1}

    parts = list(chunks_for_asset(asset))
    assert len(parts) == 2
    assert parts[1]['page'] == 2 and parts[1]['table_cells'] == []
    assert not parts[1]['review_required']

    draft_state = state(asset)
    draft_state['messages'] += [{'role': 'assistant', 'content': QUESTION},
                                {'role': 'user', 'content': '품명, 실입고'}]
    draft_state.update(document_rows_reply(draft_state), status='ready', confirmed=True)
    storage = Storage(tmp_path)
    try:
        storage.add_asset(asset, [])
        with pytest.raises(ValueError, match='셀 위치'):
            storage.save_agent(draft_state)
        with monkeypatch.context() as legacy:
            legacy.setattr('agent_builder.capabilities.capability_gate.require_save_ready', lambda _state: None)
            saved_id = storage.save_agent(draft_state)
        result = run_workflow(storage, saved_id, '워크플로우 실행', '', file_ids=[asset['id']], llm=NoModel())
        assert result['status'] == 'failed'
        assert not result.get('artifact')
        assert '표' in str(result['error'])
    finally:
        storage.close()


def test_selected_structured_child_covers_empty_layout_region():
    layout_region = block([], 'layout')
    layout_region['engine'] = 'layout'
    selected_table = block(SOURCE)
    selected_table['engine'] = 'docling'
    selected_table['parent_object_id'] = 'layout'
    selected_table['text'] = ' ' * 3500 + selected_table['text'] + ' ' * 1700 + '주석'
    deselected = block(SOURCE, 'alternative')
    deselected.update(selected=False, data={})
    prose = dict(block([['납품 주의사항']], 'prose'), content_types=['text'], data={})
    asset = document([layout_region, selected_table, deselected, prose])

    parts = list(chunks_for_asset(asset))
    assert len(parts) > 2
    assert {part['block_id'] for part in parts} == {'table-1', 'prose'}
    assert parts[0]['offset'] > 0 and parts[0]['table_cells'] == selected_table['data']['cells']
    assert all('table_cells' not in part for part in parts[1:])
    assert [{field: record[field] for field in ('품명', '실입고')} for record in native(asset)] == EXPECTED


@pytest.mark.parametrize('repeat_header', [False, True])
def test_single_column_export_preserves_item_named_like_header(tmp_path, repeat_header):
    header = ['코드', '품명', '수량']
    grid = [header, ['A01', '종이', '2']]
    if repeat_header:
        grid.append(header)
    grid.append(['A02', '품명', '3'])
    asset = document([block(grid)])
    draft_state = state(asset)
    draft_state['messages'] += [{'role': 'assistant', 'content': QUESTION},
                                {'role': 'user', 'content': '품명'}]
    draft_state.update(document_rows_reply(draft_state), status='ready', confirmed=True)
    storage = Storage(tmp_path)
    try:
        storage.add_asset(asset, [])
        saved_id = storage.save_agent(draft_state)
        result = run_workflow(storage, saved_id, '워크플로우 실행', '', file_ids=[asset['id']], llm=NoModel())
        assert result['status'] == 'succeeded', result.get('error')
        assert output_rows(result['artifact']) == [{'품명': '종이'}, {'품명': '품명'}]
        records = result['outputs']['extract']['records']
        assert records[-1]['evidence']['품명']['table_row'] == len(grid) - 1
        assert records[-1]['row_coverage'] == 'complete'
    finally:
        storage.close()
