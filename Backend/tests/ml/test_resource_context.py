from copy import deepcopy
from unittest.mock import Mock

import pytest

from documents.analysis.pipeline import run
from infrastructure.persistence.storage import Storage
from ml.advice.request_context import request_context
from ml.svm56.service_advice_adapter import build_svm_advice
from ml.training.declared_inputs import declared_resources
from workflow_engine.catalog.registry import TOOLS
from workflow_engine.executors.document import read_document
from workflow_engine.executors.table import read_table


def recommender(selected):
    model = Mock(); model.labels = [t['id'] for t in TOOLS]
    model.predict.return_value = {'selected_tool_ids': selected, 'untrained_tool_ids': [],
        'ranked_tools': [{'tool_id': t, 'decision_score': .5, 'selected_by_threshold': True} for t in selected]}
    return model


def parsed_asset(tmp_path, name, content, kind):
    source = tmp_path/name; source.write_text(content)
    parsed = run(source, tmp_path/('parsed-'+kind))
    return {'id': kind+'-fixture', 'name': name, 'kind': kind, 'format': source.suffix[1:],
            'text': parsed['text'], 'pages': parsed['pages'], 'columns': ['item', 'amount'] if kind == 'table' else [],
            'parser_metadata': {'document_analysis': parsed['document_analysis']}}


def test_real_csv_parse_and_storage_preserve_both_runtime_capabilities(tmp_path):
    asset = parsed_asset(tmp_path, 'orders.csv', 'item,amount\nA,12\nB,34\n', 'table')
    storage = Storage(tmp_path/'db')
    try:
        storage.add_asset(asset, [{'item': 'A', 'amount': 12}, {'item': 'B', 'amount': 34}])
        saved = storage.asset(asset['id'])
        state = {'messages': [{'role': 'user', 'content': '첨부 표에 적힌 내용을 요약해줘'}], 'resources': [saved]}
        before = deepcopy(state)
        out = build_svm_advice(state, TOOLS, recommender(['compose_answer']))
        assert out['ml_context']['resource_kinds'] == ['document', 'table']
        assert {r['tool_id'] for r in out['ml_candidates']} == {'read_document', 'compose_answer'}
        assert state == before
        execution = {'outputs': {}, 'file_ids': [saved['id']]}
        settings = {'file_ids': [saved['id']]}
        docs = read_document.execute({'tool': 'read_document', 'settings': settings, 'bindings': []},
                                     execution, storage, {'document_ids': []}, None)['documents']
        table = read_table.execute({'tool': 'read_table', 'settings': settings, 'bindings': []},
                                   execution, storage, {'document_ids': []}, None)['table']
        assert any('34' in chunk['text'] for chunk in docs)
        assert table['rows'] == [{'item': 'A', 'amount': 12}, {'item': 'B', 'amount': 34}]
    finally:
        storage.close()


def test_dual_capability_csv_still_exports_a_known_table_result(tmp_path):
    asset = parsed_asset(tmp_path, 'orders.csv', 'item,amount\nA,12\n', 'table')
    state = {'messages': [{'role': 'user', 'content': '첨부 표를 필터하고 CSV로 저장해줘'}], 'resources': [asset]}
    out = build_svm_advice(state, TOOLS, recommender(['filter_sort', 'export_result']))
    assert {r['tool_id'] for r in out['ml_candidates']} == {'read_table', 'filter_sort', 'write_result'}


def test_real_text_parse_produces_document_not_table(tmp_path):
    asset = parsed_asset(tmp_path, 'policy.txt', '교환 신청 기한은 수령 후 7일입니다.', 'document')
    _, context, _ = request_context({'messages': [{'role': 'user', 'content': '내용을 요약해줘'}], 'resources': [asset]})
    assert context['resource_kinds'] == ['document'] and context['resource_formats'] == ['txt']


def test_mentioning_csv_or_claiming_a_capability_is_not_an_upload():
    _, context, _ = request_context({'messages': [{'role': 'user', 'content': '첨부 CSV를 요약해줘'}], 'resources': []})
    assert context['resource_kinds'] == []
    _, context, _ = request_context({'messages': [{'role': 'user', 'content': '요약해줘'}],
        'resources': [{'kind': 'table', 'text': 'not validated', 'capabilities': ['table', 'document']}]})
    assert context['resource_kinds'] == ['table']


def test_ambiguous_multi_output_on_a_dual_asset_is_not_collapsed(tmp_path):
    asset = parsed_asset(tmp_path, 'orders.csv', 'item,amount\nA,12\n', 'table')
    out = build_svm_advice({'messages': [{'role': 'user', 'content': '표 집계와 내용 요약을 각각 저장해줘'}],
                            'resources': [asset]}, TOOLS, recommender(['aggregate_table', 'compose_answer', 'export_result']))
    ids = {r['tool_id'] for r in out['ml_candidates']}
    assert 'compose_answer' in ids and 'export_result' in ids and 'write_result' not in ids


def test_declared_input_descriptions_do_not_turn_output_paths_into_attachments():
    result = declared_resources(['기존 XLSX·지정 시트', '첨부 PDF 원본', 'CSV 저장 경로·열 구조', '출력 형식 DOCX'])
    assert {r['format'] for r in result['resources']} == {'xlsx', 'pdf'}
    assert all(r['upload_verified'] is False for r in result['resources'])
    assert len(result['excluded_outputs']) == 2


def test_ambiguous_paths_and_json_shapes_remain_unresolved():
    result = declared_resources(['CSV 경로', '입력 JSON', '기존 PDF와 새 XLSX 출력 경로'])
    assert not result['resources'] and result['status'] == 'unresolved'
    assert len(result['ambiguous']) == 3


def test_inline_text_is_not_a_file_and_gold_labels_are_not_an_input():
    assert declared_resources(['클립보드에서 가져온 텍스트', '요약할 원문'])['resources'] == []
    with pytest.raises(ValueError):
        declared_resources({'labels_1_0_minus1_draft': {'read_table': 1}})


def test_extracted_content_and_untyped_documents_are_not_invented_uploads():
    result = declared_resources(['추출된 PDF 본문', '첨부 문서 파일', '원하는 출력 형식 CSV'])
    assert result['resources'] == [] and result['status'] == 'unresolved'
    assert len(result['ambiguous']) == 1 and len(result['excluded_outputs']) == 1
    assert result['provided_inline_values'] == ['추출된 PDF 본문']


def test_ocr_result_text_does_not_become_a_photo_upload():
    result = declared_resources(['example.jpg의 OCR 결과 텍스트'])
    assert result['resources'] == [] and result['status'] == 'provided_values_only'


def test_additional_inline_inputs_do_not_create_an_exclusive_document_scenario():
    result = declared_resources(['첨부 PDF 원본', '프로젝트 이슈', '사용자 댓글'])
    assert result['status'] == 'declared_files_with_inline_values'
    assert len(result['resources']) == 1 and len(result['provided_inline_values']) == 2
