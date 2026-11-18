"""Compatibility and fail-closed boundaries of native row export."""
from copy import deepcopy
import pytest

from agent_builder.design.document_rows_template import QUESTION, document_rows_reply
from infrastructure.persistence.storage import Storage
from tests.design.test_document_row_integrity import block, document, state, NoModel, output_rows
from workflow_engine.executors.document.extract_information import execute as extract
from workflow_engine.executors.output.export_result import execute as export
from workflow_engine.runner import run_workflow


def export_records(records, fields, format='csv', *, field_records=False):
    return export({'tool': 'export_result', 'settings': {'format': format, 'fields': fields},
                   'bindings': [{'input': 'records', 'node_id': 'extract', 'output': 'records'}]},
                  {'outputs': {'extract': {'records': records}}}, None, {'workflow': {'nodes': [{'id': 'extract', 'tool': 'extract_information',
                      'settings': {'extraction_mode': 'fields' if field_records else 'rows'}}]}}, NoModel())['artifact']


@pytest.mark.parametrize('format', ['csv', 'json'])
def test_projection_accepts_nonrow_records_but_never_unverified_rows(format):
    artifact = export_records([{'상호명': '상점', '합계': '1000', 'status': 'verified'}], ['상호명', '합계'], format)
    assert '상점' in artifact['content'] and 'status' not in artifact['content']
    with pytest.raises(ValueError, match='전체 행'):
        export_records([{'품명': '볼트', 'row_coverage': 'unverified'}], ['품명'], format)


def test_scalar_records_are_pivoted_per_file_not_per_filename():
    records = [{'file_id': id, 'file_name': 'same.pdf', 'field': field, 'value': value}
               for id, name, total in [('a', '상점A', '1000'), ('b', '상점B', '2000')]
               for field, value in [('상호명', name), ('합계', total)]]
    assert output_rows(export_records(records, ['상호명', '합계'], field_records=True)) == [
        {'상호명': '상점A', '합계': '1000'}, {'상호명': '상점B', '합계': '2000'}]
    with pytest.raises(ValueError, match='중복'):
        export_records(records + [records[0]], ['상호명'], field_records=True)
    assert output_rows(export_records([{'field': '원본 열', 'value': '값'}], ['value'])) == [{'value': '값'}]


def mixed_document(value='2026-11-18'):
    return document([block([['품명', '수량'], ['볼트', '2'], ['너트', '3']]),
                     {'id': 'date', 'selected': True, 'content_types': ['text'],
                      'text': '거래일자: ' + value, 'native_location': {'page': 1}, 'engine': 'mineru'}])


@pytest.mark.parametrize('conflict', [False, True])
def test_mixed_document_fact_save_and_run_or_reject_ambiguous_fact(tmp_path, conflict):
    asset = mixed_document()
    if conflict:
        asset['parser_metadata']['document_analysis']['blocks'][1]['text'] += '\n거래일자: 2026-11-19'
    current = state(asset)
    current['messages'] += [{'role': 'assistant', 'content': QUESTION},
                            {'role': 'user', 'content': '거래일자, 품명, 수량'}]
    current.update(document_rows_reply(current), status='ready', confirmed=True)
    store = Storage(tmp_path)
    try:
        store.add_asset(asset, [])
        if conflict:
            with pytest.raises(ValueError, match='요청한 열'):
                store.save_agent(current)
            return
        result = run_workflow(store, store.save_agent(current), '실행', '', llm=NoModel())
        assert result['status'] == 'succeeded', result.get('error')
        assert output_rows(result['artifact']) == [
            {'거래일자': '2026-11-18', '품명': '볼트', '수량': '2'},
            {'거래일자': '2026-11-18', '품명': '너트', '수량': '3'}]
        assert result['outputs']['extract']['records'][1]['evidence']['거래일자']['block_id'] == 'date'
    finally:
        store.close()


@pytest.mark.parametrize('selection,expected', [('첫 품목 하나만', '볼트'), ('마지막 품목 하나만', '너트')])
def test_explicit_scalar_selects_native_row_without_model(selection, expected):
    from documents.resources.document_resources import chunks_for_asset
    node = {'tool': 'extract_information', 'settings': {'fields': ['품명'], 'extraction_mode': 'fields'},
            'bindings': [{'input': 'documents', 'node_id': 'read', 'output': 'documents'}]}
    result = extract(node, {'request': '문서마다 ' + selection,
                           'outputs': {'read': {'documents': list(chunks_for_asset(mixed_document()))}}},
                     None, {'purpose': '문서마다 ' + selection}, NoModel())
    assert output_rows(export_records(result['records'], ['품명'], field_records=True)) == [{'품명': expected}]


def test_scalar_save_run_and_cancellation_does_not_inherit_old_selection(tmp_path):
    from agent_builder.design.document_rows_template import _draft
    from workflow_engine.executors.document.row_contract import scalar_selection
    asset = mixed_document()
    request = '문서마다 첫 품목 하나만 CSV로 저장해줘'
    current = state(asset, request)
    draft = _draft(request, request, ['품명'], [asset['id']])
    draft['workflow']['nodes'][1]['settings']['extraction_mode'] = 'fields'
    current.update(draft=draft, status='ready', confirmed=True)
    store = Storage(tmp_path)
    try:
        store.add_asset(asset, [])
        result = run_workflow(store, store.save_agent(current), '실행', '', llm=NoModel())
        assert result['status'] == 'succeeded', result.get('error')
        assert output_rows(result['artifact']) == [{'품명': '볼트'}]
    finally:
        store.close()
    assert scalar_selection(request, '첫 품목 하나만 말고 모든 품목을 추출해줘') is None
    assert scalar_selection(request, '문서별 합계 하나만') is None
    assert scalar_selection('첫 품목 하나만 제외해줘') is None


def test_receipt_item_projection_and_missing_columns():
    from workflow_engine.executors.document.receipt_items import extract_items
    parts = [{'file_id': 'receipt', 'file_name': 'receipt.pdf', 'source_id': 'receipt:1:0', 'page': 1,
              'text': '상품명 수량 금액\n볼트 2 1000\n너트 3 2000\n합계 3000'}]
    records = extract_items(parts, ['상품명', '수량', '금액'])['records']
    assert output_rows(export_records(records, ['상품명', '금액'])) == [
        {'상품명': '볼트', '금액': '1000'}, {'상품명': '너트', '금액': '2000'}]
    with pytest.raises(ValueError, match='출력할 열'):
        export_records(records, ['없는 열'])
    assert export_records([], ['품명'])['content'].lstrip('\ufeff') == '품명\r\n'


def test_preflight_checks_row_exports_without_blocking_internal_model_extraction():
    from agent_builder.design.document_rows_template import _draft, document_row_source_issues
    asset = document(); asset.pop('parser_metadata')
    current = state(asset)
    draft = _draft('문서를 CSV로 저장', '', ['품명'], [asset['id']])
    assert '표 셀 좌표' in document_row_source_issues(current, draft)[0]
    draft['workflow']['nodes'] = draft['workflow']['nodes'][:2]
    draft['workflow']['edges'] = draft['workflow']['edges'][:1]
    assert document_row_source_issues(current, draft) == []


def test_document_metadata_is_file_scoped_and_table_continuations_are_not_prose():
    from documents.resources.document_resources import chunks_for_asset
    from workflow_engine.executors.document.row_contract import native_rows
    first = mixed_document('2026-11-18')
    second = mixed_document('2026-12-18'); second['id'] = 'second'
    node = {'tool': 'extract_information', 'settings': {'fields': ['거래일자', '품명'], 'extraction_mode': 'rows'},
            'bindings': [{'input': 'documents', 'node_id': 'read', 'output': 'documents'}]}
    parts = [*chunks_for_asset(first), *chunks_for_asset(second)]
    result = extract(node, {'request': '각 품목 추출', 'outputs': {'read': {'documents': parts}}},
                     None, {}, NoModel())
    assert [row['거래일자'] for row in result['records']] == ['2026-11-18'] * 2 + ['2026-12-18'] * 2
    parts = list(chunks_for_asset(first))[:1]
    parts.append({**parts[0], 'text': '거래일자: 2026-11-18', 'source_id': 'continuation'})
    parts[-1].pop('table_cells')
    with pytest.raises(ValueError, match='요청한 열'):
        native_rows(parts, ['거래일자', '품명'], [], 200)


@pytest.mark.parametrize('replacement_kind', ['new-id', 'ambiguous', 'different-header'])
def test_scoped_replacement_uses_unique_full_header(tmp_path, replacement_kind):
    from tests.design.test_document_preview_review import preview_message
    from tests.design.test_document_rows_template import asked_state
    original = mixed_document()
    replacement = deepcopy(original)
    replacement.update(id='new-file', name='다음달.pdf')
    table = replacement['parser_metadata']['document_analysis']['blocks'][0]
    table['id'] = 'new-block'
    table['data']['cells'][2]['text'] = '새 품목'
    if replacement_kind == 'ambiguous':
        duplicate = deepcopy(table); duplicate['id'] = 'second-table'
        replacement['parser_metadata']['document_analysis']['blocks'].append(duplicate)
    elif replacement_kind == 'different-header':
        table['id'] = 'table-1'  # Matching IDs must not override differing schema.
        table['data']['cells'][0]['text'] = '다른 열'
    current = asked_state(preview_message(original, ['「표 1」의 품명', '「표 1」의 수량']),
                          request='이 문서 CSV로 저장해줘', doc=original)
    current.update(document_rows_reply(current), status='ready', confirmed=True)
    store = Storage(tmp_path)
    try:
        store.add_asset(original, []); store.add_asset(replacement, [])
        result = run_workflow(store, store.save_agent(current), '실행', '', file_ids=['new-file'], llm=NoModel())
        if replacement_kind == 'new-id':
            assert result['status'] == 'succeeded', result.get('error')
            assert output_rows(result['artifact'])[0]['품명'] == '새 품목'
        else:
            assert result['status'] == 'failed' and not result.get('artifact')
            assert '하나로 확정' in result['error']
    finally:
        store.close()


@pytest.mark.parametrize('field', ['status', 'evidence', 'file_name', 'field', 'value'])
def test_internal_keys_use_explicit_field_source(field):
    records = [{'file_id': 'f', 'file_name': 'source.pdf', 'field': field, 'value': 'paid',
                'status': 'verified', 'evidence': {}}]
    assert output_rows(export_records(records, [field], field_records=True)) == [{field: 'paid'}]


@pytest.mark.parametrize('text', ['거래일자 :', '거래일자：', '거래일자   :   '])
def test_empty_metadata_is_not_a_separator(text):
    from documents.resources.document_resources import chunks_for_asset
    from workflow_engine.executors.document.row_contract import native_rows
    asset = mixed_document(); asset['parser_metadata']['document_analysis']['blocks'][1]['text'] = text
    with pytest.raises(ValueError, match='요청한 열'):
        native_rows(list(chunks_for_asset(asset)), ['거래일자', '품명'], [], 200)


@pytest.mark.parametrize('prefix', ['첫', '마지막'])
def test_exclusion_is_not_selection(prefix):
    from workflow_engine.executors.document.row_contract import scalar_selection
    assert scalar_selection(prefix + ' 품목만 빼고 CSV로 저장해줘') is None


@pytest.mark.parametrize('label', ['거래일자', '예정 거래일자'])
def test_overlapping_chunks_preserve_original_line(label):
    from documents.resources.document_resources import chunks_for_asset
    from workflow_engine.executors.document.row_contract import native_rows
    parts = list(chunks_for_asset(mixed_document()))[:1]
    text = 'x' * 1397 + '\n' + label + ': 2026-11-18\n' + 'x' * 500
    for offset, end in [(0, 1415), (1390, len(text))]:
        parts.append({'file_id': 'delivery', 'file_name': 'a.pdf', 'source_id': f'delivery:date:{offset}',
                      'block_id': 'date', 'offset': offset, 'text': text[offset:end]})
    if label == '거래일자':
        assert native_rows(parts, ['거래일자', '품명'], [], 200)[0]['거래일자'] == '2026-11-18'
    else:
        with pytest.raises(ValueError, match='요청한 열'):
            native_rows(parts, ['거래일자', '품명'], [], 200)


def test_common_amount_is_not_line_amount():
    from documents.resources.document_resources import chunks_for_asset
    asset = mixed_document()
    asset['parser_metadata']['document_analysis']['blocks'][1]['text'] = '금액: 3000\n결제금액 3000'
    node = {'tool': 'extract_information', 'settings': {'fields': ['품명', '금액'], 'extraction_mode': 'rows'},
            'bindings': [{'input': 'documents', 'node_id': 'read', 'output': 'documents'}]}
    result = extract(node, {'request': '각 품목 추출', 'outputs': {'read': {'documents': list(chunks_for_asset(asset))}}},
                     None, {}, NoModel())
    assert all('합계_검산' not in record for record in result['records'])


def test_preflight_selected_documents_and_zero_rows():
    from agent_builder.design.document_rows_template import _draft, document_row_source_issues
    asset = document([block([['품명', '수량']])])
    current = state(asset)
    current['resources'].append(dict(id='unused', kind='document', name='unused.pdf', text='좌표 없음'))
    draft = _draft('각 품목 CSV', '', ['품명', '수량'], [asset['id']])
    draft['workflow']['nodes'][0]['settings']['file_ids'] = []
    assert document_row_source_issues(current, draft) == []


def test_replacement_compares_header_below_merged_title_and_preflight():
    from workflow_engine.executors.sources import source_file_ids
    from types import SimpleNamespace
    def asset(identifier, last):
        table = block([['Shared title'], ['Code', 'Qty', last], ['A', '2', '3']])
        table['data']['cells'][0]['col_span'] = 3
        return dict(document([table]), id=identifier)
    old, new = asset('old', 'Ordered'), asset('new', 'Returned')
    assets = {'old': old, 'new': new}
    store = SimpleNamespace(asset=lambda identifier: assets[identifier])
    node = {'id': 'read', 'tool': 'read_document', 'settings': {'file_ids': ['old'], 'document_table_id': 'table-1'}}
    with pytest.raises(ValueError, match='하나로 확정'):
        source_file_ids(node, ['new'], store, {'workflow': {'nodes': [node]}})


def test_saved_scalar_status_does_not_export_internal_verification(tmp_path):
    from agent_builder.design.document_rows_template import _draft
    from workflow_engine.executors.schemas import Facts
    asset = document([{'id': 'status', 'selected': True, 'content_types': ['text'],
                       'text': 'status: paid', 'native_location': {'page': 1}, 'engine': 'mineru'}])
    request = '문서마다 status 하나만 추출해줘'
    current = state(asset, request)
    draft = _draft(request, '', ['status'], [asset['id']])
    draft['workflow']['nodes'][1]['settings']['extraction_mode'] = 'fields'
    current.update(draft=draft, status='ready', confirmed=True)
    class Model:
        def with_structured_output(self, schema, **kwargs):
            assert schema is Facts
            return self
        def invoke(self, messages):
            return Facts.model_validate({'facts': [{'field': 'status', 'value': 'paid',
                'source_id': 'delivery:status:0', 'quote': 'status: paid'}]})
    store = Storage(tmp_path)
    try:
        store.add_asset(asset, [])
        result = run_workflow(store, store.save_agent(current), '실행', '', llm=Model())
        assert result['status'] == 'succeeded', result.get('error')
        assert output_rows(result['artifact']) == [{'status': 'paid'}]
    finally:
        store.close()


def test_preflight_accepts_document_compatible_analyzed_asset():
    from agent_builder.design.document_rows_template import _draft, document_row_source_issues
    asset = document([block([['품명'], ['볼트']])]); asset['kind'] = 'table'
    draft = _draft('각 품목 CSV', '', ['품명'], [asset['id']])
    assert document_row_source_issues(state(asset), draft) == []
    asset['parser_metadata']['document_analysis']['blocks'][0]['data']['cells'] = []
    assert document_row_source_issues(state(asset), draft)
