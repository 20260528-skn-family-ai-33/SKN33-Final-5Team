"""Delivery-note regression: parser table -> field answer -> save -> actual CSV.

The cell fixture is transcribed from the supplied synthetic delivery PDF. These
are parser-contract tests, not a claim that production OCR was run locally.
"""
from copy import deepcopy
import csv
import io
import unicodedata

import pytest
from langgraph.types import Command

from agent_builder.design.document_rows_template import document_rows_applies, document_rows_reply, QUESTION
from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.validation import validate_node
from documents.resources.document_resources import chunks_for_asset
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial
from workflow_engine.executors.document.extract_information import execute as extract, EVIDENCE_KEYS, MAX_ROWS
from workflow_engine.executors.document.table_rows import native_records
from workflow_engine.executors.output.export_result import execute as export
from workflow_engine.runner import run_workflow
from workflow_engine.executors.schemas import Facts, Rows

REQUEST = '이 납품서 내용을 CSV로 정리해줘.'
HEADERS = ['품목코드', '품명', '발주', '실입고', '파손']
SOURCE = [HEADERS,
          ['H101', 'A4 복사용지 80g', '50', '48', '2'],
          ['H102', '검정 보드마카 12입', '20', '20', '0'],
          ['H103', '보관함 중형', '12', '8', '0'],
          ['H104', '포장 테이프 48mm', '30', '30', '3']]
EXPECTED = [{'품명': row[1], '실입고': row[3]} for row in SOURCE[1:]]


def block(grid, identifier='table-1', page=1):
    return dict(id=identifier, selected=True, content_types=['table'],
                text='\n'.join(str(value) for row in grid for value in row),
                native_location={'page': page}, engine='mineru',
                data={'cells': [dict(row=r, column=c, row_span=1, col_span=1, text=value)
                                for r, row in enumerate(grid) for c, value in enumerate(row)]})


def document(blocks=None, name='납품서_한장_테스트.pdf'):
    blocks = [block(SOURCE)] if blocks is None else blocks
    text = '\n'.join(item['text'] for item in blocks)
    return dict(id='delivery', name=name, kind='document', format='pdf', text=text, pages=[text], version=1,
                parser_metadata={'document_analysis': dict(contract_version='a4b.document_analysis.v1',
                                                          review_required=False, blocks=blocks)})


def state(asset=None, instruction=REQUEST):
    return {**initial(), 'messages': [dict(role='user', content=instruction)], 'resources': [asset or document()]}


class NoModel:
    def invoke(self, *_args, **_kwargs):
        pytest.fail('the native table path must not invoke an LLM')
    def with_structured_output(self, *_args, **_kwargs):
        pytest.fail('the native table path must not invoke an LLM')


def no_planner(_state):
    pytest.fail('simple document CSV extraction must not use the planning model')


def output_rows(artifact):
    return list(csv.DictReader(io.StringIO(artifact['content'].lstrip('\ufeff'))))


@pytest.mark.parametrize('append_total', [False, True])
def test_delivery_note_field_answer_save_and_run_export_all_four_rows(tmp_path, append_total):
    grid = [*SOURCE, ['', '합계', '', '106', '']] if append_total else SOURCE
    asset = document([block(grid)], name=unicodedata.normalize('NFD', '납품서_한장_테스트.pdf'))
    s = state(asset); store = Storage(tmp_path); store.add_asset(asset, [])
    def field_question_planner(current):
        # Stand-in for the normal planner's field question, not for OCR/execution.
        from agent_builder.design.document_rows_template import _ask
        return _ask(current, REQUEST)
    graph, connection = build_graph(store, conversation=field_question_planner,
                                    condition_mode='off', capability_mode='off')
    config = {'configurable': {'thread_id': s['session_id']}}
    try:
        asked = graph.invoke(s, config)
        assert asked['status'] == 'needs_clarification' and asked['draft']['questions'] == [QUESTION]
        ready = graph.invoke(Command(resume=dict(type='message', content='품명, 실입고')), config)
        assert ready['status'] == 'ready', ready.get('issues')
        extract_node = ready['draft']['workflow']['nodes'][1]
        assert extract_node['settings']['extraction_mode'] == 'rows'
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        # Reload the saved configuration, not the in-memory draft.
        result = run_workflow(store, saved['saved_agent_id'], '워크플로우 실행', '', file_ids=[asset['id']], llm=NoModel())
        assert result['status'] == 'succeeded', result.get('error')
        assert output_rows(result['artifact']) == EXPECTED
        assert list(output_rows(result['artifact'])[0]) == ['품명', '실입고']
        records = result['outputs']['extract']['records']
        assert len(records) == 4 and all(record['row_coverage'] == 'complete' for record in records)
        assert records[2]['evidence']['실입고']['table_row'] == 3
        assert records[2]['evidence']['실입고']['quote'] == '8'
    finally:
        connection.close(); store.close()


@pytest.mark.parametrize('title', ['납품서', '매입명세서', '자재인수증', '문서'])
@pytest.mark.parametrize('nfd', [False, True])
def test_source_names_do_not_change_extraction_granularity(title, nfd):
    name = title + '_테스트.pdf'
    if nfd:
        name = unicodedata.normalize('NFD', name)
    s = state(document(name=name), f'이 {title} 내용을 CSV로 정리해줘.')
    s['messages'] += [dict(role='assistant', content=QUESTION), dict(role='user', content='품명, 실입고')]
    reply = document_rows_reply(s)
    assert reply['draft']['workflow']['nodes'][1]['settings']['extraction_mode'] == 'rows'


@pytest.mark.parametrize('extra', ['실입고 10 이상만', '품명으로 정렬해서', '총합계를 계산해서', '별도 메일로도 보내고'])
def test_source_reference_does_not_hide_additional_operations(extra):
    s = state(instruction=f'이 납품서에서 {extra} CSV로 정리해줘.')
    assert not document_rows_applies(s)
    assert document_rows_reply(s) is None


def native(asset=None, fields=None):
    return native_records(list(chunks_for_asset(asset or document())), fields or ['품명', '실입고'], EVIDENCE_KEYS, MAX_ROWS)


def test_multiple_pages_repeat_headers_preserve_same_name_and_blank_cells():
    grid = [HEADERS, SOURCE[1], ['H105', SOURCE[1][1], '2', '', '0']]
    records = native(document([block(SOURCE[:3]), block(grid, 'table-2', 2)]))
    assert [record['품명'] for record in records] == [SOURCE[1][1], SOURCE[2][1], SOURCE[1][1], SOURCE[1][1]]
    assert records[-1]['실입고'] is None
    assert records[-1]['evidence']['실입고']['page'] == 2
    assert [record['row'] for record in records] == [1, 2, 3, 4]


def test_repeated_header_inside_table_is_not_an_item():
    records = native(document([block([HEADERS, SOURCE[1], HEADERS, SOURCE[2]])]))
    assert [record['품명'] for record in records] == [SOURCE[1][1], SOURCE[2][1]]


def test_cell_iteration_order_and_docling_offsets_do_not_change_rows():
    asset = document()
    cells = asset['parser_metadata']['document_analysis']['blocks'][0]['data']['cells']
    for cell in cells:
        cell['start_row_offset_idx'] = cell.pop('row')
        cell['start_col_offset_idx'] = cell.pop('column')
    cells.reverse()
    assert [{field: record[field] for field in ('품명', '실입고')} for record in native(asset)] == EXPECTED


@pytest.mark.parametrize('change', ['missing_cell', 'merged_cell', 'duplicate_cell', 'missing_header', 'unmatched_page', 'review_required', 'too_many'])
def test_uncertain_table_coverage_is_never_exported_as_complete(change):
    asset = document()
    analysis = asset['parser_metadata']['document_analysis']; table = analysis['blocks'][0]; cells = table['data']['cells']
    if change == 'missing_cell':
        table['data']['cells'] = [cell for cell in cells if not (cell['row'] == 2 and cell['column'] == 3)]
    elif change == 'merged_cell':
        next(cell for cell in cells if cell['row'] == 2 and cell['column'] == 1)['row_span'] = 2
    elif change == 'duplicate_cell':
        cells.append(deepcopy(cells[-1]))
    elif change == 'missing_header':
        next(cell for cell in cells if cell['row'] == 0 and cell['column'] == 3)['text'] = '다른 열'
    elif change == 'unmatched_page':
        analysis['blocks'].append(block(SOURCE[1:], 'continued', 2))
    elif change == 'review_required':
        analysis['review_required'] = True
    else:
        analysis['blocks'] = [block([HEADERS] + [SOURCE[1]] * (MAX_ROWS + 1))]
    with pytest.raises(ValueError):
        native(asset)


def test_long_table_is_not_truncated_or_duplicated_by_overlapping_text_chunks():
    asset = document([block([HEADERS] + [SOURCE[1]] * MAX_ROWS)])
    parts = list(chunks_for_asset(asset))
    assert len(parts) > 1
    assert sum('table_cells' in part for part in parts) == 1
    assert len(native_records(parts, ['품명', '실입고'], EVIDENCE_KEYS, MAX_ROWS)) == MAX_ROWS


class FirstRowModel:
    def with_structured_output(self, schema, **_kwargs):
        assert schema is Rows
        return self
    def invoke(self, _messages):
        return Rows.model_validate(dict(rows=[dict(cells=[
            dict(field='품명', value='A4 복사용지 80g', source_id='delivery:1:0', quote='A4 복사용지 80g'),
            dict(field='실입고', value='48', source_id='delivery:1:0', quote='48')])]))


def test_grounded_first_row_without_native_coverage_cannot_be_exported_as_finished_csv(tmp_path):
    asset = document(); asset.pop('parser_metadata')
    s = state(asset); store = Storage(tmp_path); store.add_asset(asset, [])
    s['messages'] += [dict(role='assistant', content=QUESTION), dict(role='user', content='품명, 실입고')]
    s.update(document_rows_reply(s)); s.update(status='ready', confirmed=True)
    try:
        result = run_workflow(store, store.save_agent(s), '워크플로우 실행', '', file_ids=[asset['id']], llm=FirstRowModel())
        assert result['status'] == 'failed' and not result.get('artifact')
        assert '전체 행' in str(result.get('error'))
    finally:
        store.close()


@pytest.mark.parametrize('change', ['scalar', 'drop_field', 'metadata_export'])
def test_generation_and_save_reject_a_downgrade_to_single_values(tmp_path, change):
    s = state(); s['messages'] += [dict(role='assistant', content=QUESTION), dict(role='user', content='품명, 실입고')]
    s.update(document_rows_reply(s)); s.update(status='ready', confirmed=True)
    nodes = s['draft']['workflow']['nodes']
    if change == 'scalar':
        nodes[1]['settings']['extraction_mode'] = None
    elif change == 'drop_field':
        nodes[1]['settings']['fields'] = ['품명']
    else:
        nodes[2]['settings']['fields'] = []
    assert validate_node(s)['status'] != 'ready'
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='항목|행'):
            store.save_agent(s)
    finally:
        store.close()


@pytest.mark.parametrize('coordinate_table', [False, True])
def test_scalar_field_extraction_keeps_its_original_record_contract(coordinate_table):
    if coordinate_table:
        asset = document([block([['문서번호', '발행일'], ['DLV-H-11011', '2026-11-17']])])
        parts = list(chunks_for_asset(asset))
    else:
        parts = [dict(source_id='s1', file_id='delivery', file_name='납품서.pdf', text='문서번호 DLV-H-11011')]

    class ScalarModel:
        def with_structured_output(self, schema, **_kwargs):
            assert schema is Facts; return self
        def invoke(self, _messages):
            return Facts.model_validate(dict(facts=[dict(field='문서번호', value='DLV-H-11011',
                                                        source_id=parts[0]['source_id'], quote='DLV-H-11011')]))
    node = dict(tool='extract_information', bindings=[dict(input='documents', node_id='read', output='documents')],
                settings=dict(fields=['문서번호'], extraction_mode='fields', instructions=None))
    result = extract(node, dict(outputs=dict(read=dict(documents=parts)), request='문서번호를 뽑아줘'), None, {}, ScalarModel())
    assert result['table']['rows'] == [{'문서번호': 'DLV-H-11011'}]
    assert result['records'][0]['field'] == '문서번호' and result['records'][0]['status'] == 'verified'


def test_schema_driven_projection_works_with_arbitrary_columns_and_row_counts():
    grid = [['custom key', 'arbitrary metric'], ['x', '7'], ['x', ''], ['y', '19']]
    asset = document([block(grid)], name='사용자자료.pdf')
    records = native(asset, ['arbitrary metric', 'custom key'])
    artifact = export(dict(tool='export_result', settings=dict(format='csv'),
                           bindings=[dict(input='records', node_id='extract', output='records')]),
                      dict(outputs=dict(extract=dict(records=records))), None, {}, None)['artifact']
    assert output_rows(artifact) == [{'arbitrary metric': '7', 'custom key': 'x'},
                                     {'arbitrary metric': '', 'custom key': 'x'},
                                     {'arbitrary metric': '19', 'custom key': 'y'}]


def test_native_table_shape_also_protects_the_general_planner():
    from agent_builder.design.document_rows_template import _draft, document_row_contract_issues
    s = state()
    # No fixed-recipe field question: this request uses the full planning path.
    draft = _draft(REQUEST, '품명, 실입고', ['품명', '실입고'], ['delivery'])
    draft['workflow']['nodes'][1]['settings']['extraction_mode'] = 'fields'
    assert document_row_contract_issues(s, draft)


def test_general_planner_allows_scalar_extraction_from_one_matching_native_row():
    from agent_builder.design.document_rows_template import _draft, document_row_contract_issues
    asset = document([block([['문서번호', '발행일'], ['DLV-H-11011', '2026-11-17']])])
    request = '이 문서의 문서번호 한 값을 CSV로 저장해줘.'
    draft = _draft(request, request, ['문서번호'], [asset['id']])
    draft['workflow']['nodes'][1]['settings']['extraction_mode'] = 'fields'
    draft['workflow']['nodes'][2]['settings']['fields'] = []
    assert not document_row_contract_issues(state(asset, request), draft)


@pytest.mark.parametrize('request_text', [
    '이 자료의 모든 품목에서 요청한 값인 품명을 한 값씩 뽑아줘.',
    '품명만 뽑아서 내림차순으로 정렬해 CSV로 저장해줘.',
])
def test_general_planner_rejects_scalar_extraction_from_continued_table(tmp_path, request_text):
    from agent_builder.design.document_rows_template import _draft, document_row_contract_issues
    asset = document([block(SOURCE[:2]), block([HEADERS, SOURCE[2]], 'continued', 2)])
    draft = _draft(request_text, request_text, ['품명'], [asset['id']])
    draft['workflow']['nodes'][1]['settings']['extraction_mode'] = 'fields'
    draft['workflow']['nodes'][2]['settings']['fields'] = []
    current = dict(state(asset, request_text), draft=draft, status='ready', confirmed=True)
    assert any('반복 행' in issue for issue in document_row_contract_issues(current, draft))
    assert validate_node(current)['status'] != 'ready'
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError, match='반복 행'):
            store.save_agent(current)
    finally:
        store.close()


def test_saved_scalar_workflow_checks_continue_table_on_a_new_execution_file(tmp_path):
    from agent_builder.design.document_rows_template import _draft
    asset = document([block(SOURCE[:2])])
    replacement = document([block(SOURCE[:2]), block([HEADERS, SOURCE[2]], 'continued', 2)])
    replacement['id'] = 'replacement'
    request = '이 자료에서 모든 품목의 품명을 뽑아줘.'
    draft = _draft(request, request, ['품명'], [asset['id']])
    draft['workflow']['nodes'][1]['settings']['extraction_mode'] = 'fields'
    draft['workflow']['nodes'][2]['settings']['fields'] = []
    current = dict(state(asset, request), draft=draft, status='ready', confirmed=True)
    store = Storage(tmp_path)
    try:
        store.add_asset(asset, [])
        store.add_asset(replacement, [])
        saved = store.save_agent(current)
        result = run_workflow(store, saved, '실행', '', file_ids=[replacement['id']], llm=NoModel())
        assert result['status'] == 'failed' and not result.get('artifact')
        assert '여러 행으로 반복' in result['error']
    finally:
        store.close()


@pytest.mark.parametrize('request_text', [
    '문서당 문서번호 하나를 CSV로 저장해줘.',
    '문서별로 문서번호 한 값을 CSV로 저장해줘.',
])
def test_explicit_single_metadata_request_allows_same_table_on_two_pages(request_text):
    from agent_builder.design.document_rows_template import _draft, document_row_contract_issues
    grid = [['문서번호', '발행일'], ['DLV-H-11011', '2026-11-17']]
    asset = document([block(grid), block(grid, 'continued', 2)])
    draft = _draft(request_text, request_text, ['문서번호'], [asset['id']])
    node = draft['workflow']['nodes'][1]
    node['settings']['extraction_mode'] = 'fields'
    draft['workflow']['nodes'][2]['settings']['fields'] = []
    assert not document_row_contract_issues(state(asset, request_text), draft)

    class ScalarModel:
        def with_structured_output(self, schema, **_kwargs):
            assert schema is Facts
            return self

        def invoke(self, _messages):
            return Facts.model_validate(dict(facts=[dict(field='문서번호', value='DLV-H-11011',
                source_id='delivery:table-1:0', quote='DLV-H-11011')]))

    result = extract(node, {'outputs': {'read': {'documents': list(chunks_for_asset(asset))}},
                            'request': '실행'}, None, draft, ScalarModel())
    assert result['table']['rows'] == [{'문서번호': 'DLV-H-11011'}]


def test_selected_table_cells_survive_even_without_flattened_parser_text():
    asset = document()
    asset['parser_metadata']['document_analysis']['blocks'][0]['text'] = ''
    assert [{field: row[field] for field in ('품명', '실입고')} for row in native(asset)] == EXPECTED


@pytest.mark.parametrize('form', ['typed', 'whole_label', 'fragment_label', 'translated_label'])
def test_field_answer_uses_question_identity_and_keeps_csv_in_the_workflow(tmp_path, form):
    from agent_builder.design.document_rows_template import QUESTION_KEY
    asset = document(); s = state(asset, '이 문서 csv 로 저장해줘')
    store = Storage(tmp_path); store.add_asset(asset, [])
    graph, connection = build_graph(store, conversation=no_planner, condition_mode='off', capability_mode='off')
    config = {'configurable': {'thread_id': s['session_id']}}
    try:
        asked = graph.invoke(s, config)
        assert len(asked['pending_questions']) == 1
        q = asked['pending_questions'][0]
        assert q['key'] == QUESTION_KEY and q['response'] == {'type': 'text'}
        if form == 'typed':
            answer = dict(type='message', content='품명, 실입고')
        else:
            label = (QUESTION if form == 'whole_label' else QUESTION.split('? ', 1)[1]
                     if form == 'fragment_label' else 'changed display label')
            answer = dict(type='message', content=label + ' → 품명, 실입고',
                          question_answers=[dict(question_id=q['id'], value='품명, 실입고')])
        ready = graph.invoke(Command(resume=answer), config)
        assert ready['status'] == 'ready', ready.get('issues')
        assert not ready['pending_questions']
        assert [node['tool'] for node in ready['draft']['workflow']['nodes']] == ['read_document', 'extract_information', 'export_result']
        assert ready['draft']['workflow']['nodes'][1]['settings']['fields'] == ['품명', '실입고']
        saved = graph.invoke(Command(resume=dict(type='confirm')), config)
        result = run_workflow(store, saved['saved_agent_id'], '실행', '', file_ids=[asset['id']], llm=NoModel())
        assert result['status'] == 'succeeded' and output_rows(result['artifact']) == EXPECTED
    finally:
        connection.close(); store.close()


def test_legacy_question_fragment_answer_is_accepted_without_a_second_question():
    from tests.design.test_document_rows_template import asked_state
    content = QUESTION.split('? ', 1)[1] + ' → 품명, 실입고'
    reply = document_rows_reply(asked_state(content, request='이 문서 CSV로 저장해줘', doc=document()))
    assert not reply['draft']['questions']
    assert reply['draft']['workflow']['nodes'][1]['settings']['fields'] == ['품명', '실입고']
    assert reply['draft']['workflow']['nodes'][2]['settings']['format'] == 'csv'


def test_preview_selection_resolves_actual_aliases_without_losing_original_export(tmp_path):
    from tests.design.test_document_rows_template import asked_state
    asset = document()
    content = asset['name'] + '에서 「표 1」의 품명, 「표 1」의 실입고 항목을 추출해 주세요.'
    s = asked_state(content, request='이 문서 CSV로 저장해줘', doc=asset)
    s.update(document_rows_reply(s)); s.update(status='ready', confirmed=True)
    assert [node['tool'] for node in s['draft']['workflow']['nodes']] == ['read_document', 'extract_information', 'export_result']
    assert s['draft']['workflow']['nodes'][1]['settings']['fields'] == ['품명', '실입고']
    store = Storage(tmp_path); store.add_asset(asset, [])
    try:
        result = run_workflow(store, store.save_agent(s), '실행', '', file_ids=[asset['id']], llm=NoModel())
        assert result['status'] == 'succeeded' and output_rows(result['artifact']) == EXPECTED
    finally:
        store.close()


def test_preview_selection_cannot_combine_columns_from_multiple_tables():
    from agent_builder.design.document_rows_template import _preview_field_selection
    asset = document([block(SOURCE), block(SOURCE, 'second', 2)])
    content = asset['name'] + '에서 「표 1」의 품명, 「표 2」의 실입고 항목을 추출해 주세요.'
    assert _preview_field_selection(content, [asset]) is None
