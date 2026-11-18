import pytest
from agent_builder.design.graph import build_graph
from langgraph.types import Command
from infrastructure.persistence.storage import Storage
from tests.design.test_document_row_integrity import document, block, state


@pytest.mark.parametrize('kind', ['missing', 'review', 'merged_header', 'conflicting_header', 'empty_cells'])
def test_parser_structure_failure_stops_without_a_clarification_or_save(tmp_path, kind):
    asset = document([block([['상품명', '수량'], ['우유', '1'], ['초콜릿', '2']])], name='영수증.png')
    analysis = asset['parser_metadata']['document_analysis']
    if kind == 'missing':
        analysis['blocks'][0]['data']['cells'].pop()
    elif kind == 'empty_cells':
        analysis['blocks'][0]['data']['cells'] = []
        analysis['review_required'] = True
    elif kind == 'merged_header':
        analysis['blocks'][0]['data']['cells'][0]['col_span'] = 2
    elif kind == 'conflicting_header':
        analysis['review_required'] = True
        cells = analysis['blocks'][0]['data']['cells']
        cells.extend([dict(row=3, column=0, text='다른상품', column_header=True),
                      dict(row=3, column=1, text='수량', column_header=True)])
    else:
        analysis['review_required'] = True
    current = state(asset, '이 영수증 내용을 CSV로 정리해줘.')
    store = Storage(tmp_path)
    def no_planner(_):
        pytest.fail('source structure errors must not trigger replanning')
    graph, connection = build_graph(store, conversation=no_planner)
    try:
        config = {'configurable': {'thread_id': current['session_id']}}
        graph.invoke(current, config)
        result = graph.invoke(Command(resume={'type': 'message', 'content': '상품명, 수량'}), config)
        assert result['status'] == 'validation_failed'
        assert result['system_failure']['kind'] == 'document_table_structure'
        assert not result['system_failure']['retryable']
        assert not result['draft']['questions']
        assert not result.get('pending_questions')
        assert not result.get('__interrupt__')
        assert not store.agents()
    finally:
        connection.close()
        store.close()
