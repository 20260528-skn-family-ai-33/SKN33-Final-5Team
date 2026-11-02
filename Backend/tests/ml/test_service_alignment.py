from copy import deepcopy
from unittest.mock import Mock

import pytest

from ml.svm56.service_advice_adapter import build_svm_advice
from workflow_engine.catalog.registry import TOOLS


def model(selected, untrained=()):
    result = Mock()
    result.labels = [r['id'] for r in TOOLS]
    result.predict.return_value = {
        'selected_tool_ids': list(selected), 'untrained_tool_ids': list(untrained),
        'ranked_tools': [{'tool_id': t, 'decision_score': .5, 'selected_by_threshold': True} for t in selected]}
    return result


def state(query, kind='table'):
    return {'messages': [{'role': 'user', 'content': query}],
            'resources': [{'kind': kind, 'columns': ['금액', '지역']}] if kind else []}


@pytest.mark.parametrize('query', [
    '문서를 요약만 해줘. 파일로 저장하지 마.', '저장은 취소해줘',
    'CSV 말고 요약만', 'save without exporting a file',
    '파일로 저장하지 않고 화면에 요약해줘', '저장 없이 요약만 해줘',
])
def test_negated_request_abstains_without_model_or_unsupported_decision(query):
    m = model(['export_result'])
    out = build_svm_advice(state(query), TOOLS, m)
    assert out['ml_status'] == 'negation_requires_planner'
    assert not out['ml_candidates'] and 'unsupported' not in out
    m.predict.assert_not_called()


@pytest.mark.parametrize('answer', ['10000원', '상품명, 금액', '아니, 저장 취소하고 요약해줘'])
def test_followup_does_not_recommend_stale_task_or_treat_answer_as_new_task(answer):
    s = state('CSV를 필터해서 저장해줘')
    s['messages'] += [{'role': 'assistant', 'content': '기준은?'}, {'role': 'user', 'content': answer}]
    m = model(['filter_sort', 'export_result'])
    out = build_svm_advice(s, TOOLS, m)
    assert out['ml_status'] == 'conversation_requires_planner' and not out['ml_candidates']
    m.predict.assert_not_called()


def test_table_writer_is_a_contract_hint_with_no_fabricated_svm_score():
    before = deepcopy(TOOLS)
    out = build_svm_advice(state('금액을 필터하고 CSV로 저장해줘'), TOOLS, model(['filter_sort', 'export_result']))
    candidates = {r['tool_id']: r for r in out['ml_candidates']}
    assert set(candidates) == {'read_table', 'filter_sort', 'write_result'}
    assert candidates['write_result']['source'] == 'service_contract'
    assert 'decision_score' not in candidates['write_result']
    assert candidates['filter_sort']['source'] == 'svm56'
    assert TOOLS == before


def test_document_records_export_is_not_aliased_to_table_writer():
    out = build_svm_advice(state('상품명과 금액을 추출해 CSV로 저장해줘', 'document'), TOOLS,
                           model(['extract_information', 'export_result', 'read_table']))
    assert {r['tool_id'] for r in out['ml_candidates']} == {'extract_information', 'export_result', 'read_document'}


@pytest.mark.parametrize(('query', 'expected'), [
    ('고객 응대 초안을 정책 기준으로 검수해줘', 'reply_review'),
    ('응대 검수 결과를 보고서 표로 정리해줘', 'review_summary'),
])
def test_untrained_review_tools_get_explicit_rule_provenance(query, expected):
    out = build_svm_advice(state(query, None), TOOLS, model([], ['reply_review', 'review_summary']))
    row = next(r for r in out['ml_candidates'] if r['tool_id'] == expected)
    assert row['source'] == 'service_contract' and 'decision_score' not in row
    assert expected in out['untrained_registered_tool_ids']


def test_no_save_request_does_not_gain_a_writer_from_an_attachment():
    out = build_svm_advice(state('금액 순서대로 정렬해줘'), TOOLS, model(['filter_sort', 'export_result']))
    assert {r['tool_id'] for r in out['ml_candidates']} == {'read_table', 'filter_sort'}


def test_mixed_resources_are_not_forced_into_a_table_writer():
    s = state('자료를 처리해 저장해줘')
    s['resources'].append({'kind': 'document'})
    out = build_svm_advice(s, TOOLS, model(['export_result']))
    assert 'write_result' not in {r['tool_id'] for r in out['ml_candidates']}


@pytest.mark.parametrize('tool', ['compose_answer', 'reply_review', 'search_document', 'extract_information'])
def test_document_consumers_get_a_document_reader_hint(tool):
    out = build_svm_advice(state('첨부 자료를 처리해줘', 'document'), TOOLS, model([tool]))
    byid = {r['tool_id']: r for r in out['ml_candidates']}
    assert 'read_document' in byid
    assert byid['read_document']['source'] == 'service_contract'
    assert 'decision_score' not in byid['read_document']


def test_new_review_then_report_uses_intermediate_table_writer_not_original_file_type():
    out = build_svm_advice(state('고객 응대 초안을 정책으로 검수하고 검수 결과를 보고서 표로 정리해 CSV로 저장해줘.', 'document'),
                           TOOLS, model(['review_summary', 'export_result']))
    ids = {r['tool_id'] for r in out['ml_candidates']}
    assert ids == {'read_document', 'reply_review', 'review_summary', 'write_result'}


@pytest.mark.parametrize('kind', [None, 'table', 'document'])
def test_existing_review_report_does_not_invent_a_new_review_or_original_table_reader(kind):
    out = build_svm_advice(state('이미 검수한 고객 응대 초안의 검수 결과를 보고서 표로 정리해 CSV로 저장해줘.', kind),
                           TOOLS, model(['review_summary', 'export_result']))
    assert {r['tool_id'] for r in out['ml_candidates']} == {'review_summary', 'write_result'}


def test_mixed_report_and_text_outputs_are_left_for_the_planner_to_bind():
    out = build_svm_advice(state('검수 결과 보고서와 원문 요약을 각각 파일로 저장해줘.', 'document'),
                           TOOLS, model(['review_summary', 'compose_answer', 'export_result']))
    ids = {r['tool_id'] for r in out['ml_candidates']}
    assert 'export_result' in ids and 'write_result' not in ids


@pytest.mark.parametrize('query', [
    '고객 응대 초안이 승인 없이 정보를 공개하는지 검수해줘.',
    '고객 응대 초안에 확인 절차를 거치지 않고 보상한다는 내용이 있는지 검수해줘.',
    '첨부 지침을 오류 없이 요약해줘.',
    '문서의 "저장하지 마"라는 문구를 검색해줘.',
    '취소 금액 열을 합산해줘.',
    'Check the reply draft for disclosure without consent.',
])
def test_negative_material_or_literal_does_not_cancel_the_requested_work(query):
    m = model(['compose_answer'])
    out = build_svm_advice(state(query, 'document'), TOOLS, m)
    assert out['ml_status'] != 'negation_requires_planner'
    m.predict.assert_called_once()


@pytest.mark.parametrize('query', [
    '문서를 요약하되 파일 생성은 하지 마.',
    '보관할 생각 없어. 문서를 화면에서만 확인할게.',
    '문서를 요약하지만 저장은 안 할래.',
    '문서는 읽어줘. 보관할 계획 없어.',
    'Do not export the summary.',
    'Summarize without saving the document.',
    "Don't save the file. It's only for an on-screen preview.",
    '"저장하지 마"',
])
def test_action_scoped_negative_still_hands_off_before_prediction(query):
    m = model(['export_result'])
    out = build_svm_advice(state(query, 'document'), TOOLS, m)
    assert out['ml_status'] == 'negation_requires_planner' and not out['ml_candidates']
    m.predict.assert_not_called()


@pytest.mark.parametrize(('query', 'selected', 'required', 'excluded'), [
    ('고객 응대 초안을 검수하고 검수 결과를 보고서 표로 만든 뒤 판정별 건수를 집계해 CSV로 저장해줘.',
     ['reply_review', 'review_summary', 'aggregate_table', 'export_result'],
     {'reply_review', 'review_summary', 'aggregate_table', 'write_result'}, {'read_table', 'export_result'}),
    ('첨부 문서에서 품목과 수량을 추출해 CSV로 만들어줘.',
     ['extract_information', 'export_result'], {'extract_information', 'export_result'}, {'write_result'}),
    ('"첨부 문서를 요약해서 Markdown으로 저장해줘."',
     ['compose_answer', 'export_result'], {'compose_answer', 'export_result'}, set()),
])
def test_review_regressions_preserve_requested_intermediate_outputs(query, selected, required, excluded):
    out = build_svm_advice(state(query, 'document'), TOOLS, model(selected))
    ids = {r['tool_id'] for r in out['ml_candidates']}
    assert required <= ids and not ids & excluded


@pytest.mark.parametrize('query', ['save_count 열을 기준으로 정렬해줘.', '저장일 열을 기준으로 정렬해줘.', '다운로드수 열을 기준으로 정렬해줘.'])
def test_identifier_containing_save_is_not_a_file_creation_request(query):
    out = build_svm_advice(state(query), TOOLS, model(['read_table', 'filter_sort']))
    assert {r['tool_id'] for r in out['ml_candidates']} == {'read_table', 'filter_sort'}


@pytest.mark.parametrize('query', ["Analyze 2026's report. Don't save it.", "Analyze Kim's report. Don't save it."])
def test_possessive_apostrophes_do_not_hide_actual_negative_commands(query):
    m = model(['compose_answer', 'export_result'])
    out = build_svm_advice(state(query, 'document'), TOOLS, m)
    assert out['ml_status'] == 'negation_requires_planner'
    m.predict.assert_not_called()


def test_table_review_can_feed_a_text_answer_without_a_document_upload():
    out = build_svm_advice(state('표의 고객 응대를 검수하고 검수 결과를 설명해줘.'), TOOLS,
                           model(['reply_review', 'compose_answer']))
    assert {'reply_review', 'compose_answer'} <= {r['tool_id'] for r in out['ml_candidates']}


def test_saved_workflow_intermediate_table_is_not_blocked_by_document_upload():
    out = build_svm_advice(state('기존 워크플로우 결과 표를 집계해줘.', 'document'), TOOLS,
                           model(['saved_workflow', 'aggregate_table']))
    assert 'aggregate_table' in {r['tool_id'] for r in out['ml_candidates']}


@pytest.mark.parametrize('query', ['첨부 표를 CSV로 내보내기', '첨부 표의 필터 및 CSV 저장 에이전트를 만들어줘.'])
def test_save_nouns_and_export_inflections_still_request_a_writer(query):
    out = build_svm_advice(state(query), TOOLS, model(['filter_sort', 'export_result']))
    assert 'write_result' in {r['tool_id'] for r in out['ml_candidates']}



def test_table_to_agent_text_keeps_a_records_or_text_exporter():
    out = build_svm_advice(state('첨부 표의 추세를 분석해 설명을 파일로 저장해줘.'), TOOLS,
                           model(['agent_node', 'export_result']))
    assert {'agent_node', 'export_result'} <= {r['tool_id'] for r in out['ml_candidates']}
    assert 'write_result' not in {r['tool_id'] for r in out['ml_candidates']}
