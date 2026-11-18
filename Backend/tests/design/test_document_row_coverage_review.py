import pytest

from agent_builder.design.document_rows_template import QUESTION, document_rows_reply
from documents.resources.document_resources import chunks_for_asset
from infrastructure.persistence.storage import Storage
from tests.design.test_document_row_integrity import EXPECTED, NoModel, SOURCE, block, document, native, state
from workflow_engine.runner import run_workflow


@pytest.mark.parametrize('has_text', [True, False])
def test_reviewed_document_cannot_export_only_its_structured_page(tmp_path, has_text):
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
