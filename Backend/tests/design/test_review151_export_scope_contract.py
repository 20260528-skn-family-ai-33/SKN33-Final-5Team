"""Scope file counts to delivery and keep evidence for each surviving format."""
import pytest

from agent_builder.design.export_intent import explicit_export_intent
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from tests.design.test_builder import initial
from tests.design.test_review151_intent_contract import simple_plan


def conversation(*texts):
    return dict(messages=[dict(role='user', content=text) for text in texts])


@pytest.mark.parametrize('request_text', [
    '첨부 표를 두 개의 CSV 파일로 저장해줘.',
    '첨부 표의 원본과 복사본을 두 개의 CSV 파일로 저장해줘.',
    '첨부 표를 2개의 CSV 파일로 저장해줘.',
    '첨부 표를 CSV 파일 세 개로 만들어줘.',
    '첨부 표를 10개 CSV 파일로 내보내줘.',
])
def test_explicit_file_count_blocks_sealing_one_writer(request_text):
    source = dict(id='source', name='input.csv', kind='table', format='csv', columns=['amount', 'note'])
    current = {**initial(), **conversation(request_text), 'resources': [source]}
    specs, candidate = simple_plan('source', request_text, 'csv')
    raw = dict(tasks=[dict(source_message=0, description=spec['kind'], spec=spec) for spec in specs], unresolved=[])
    plan = extract_plan(raw, current)
    current.update(requirement_required=True, requirement_ledger=plan,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(plan))
    assert any('복수 결과 파일' in issue for issue in coverage_report(current, candidate)['issues'])


@pytest.mark.parametrize('request_text, expected_format', [
    ('CSV와 JSON을 비교한 뒤 결과를 CSV로 저장해줘.', 'csv'),
    ('입력은 CSV와 JSON이야. 결과는 JSON으로 저장해줘.', 'json'),
    ('원본은 CSV와 JSON 저장 결과야. 결과는 CSV로 저장해줘.', 'csv'),
    ('두 개의 CSV 파일을 읽어 결과를 JSON으로 저장해줘.', 'json'),
    ('두 개의 CSV 파일로 저장한 원본을 읽고 결과만 JSON으로 저장해줘.', 'json'),
    ('표에서 상위 두 개 행을 남긴 뒤 CSV로 저장해줘.', 'csv'),
])
def test_input_formats_and_data_counts_do_not_create_artifacts(request_text, expected_format):
    intent = explicit_export_intent(conversation(request_text))
    assert intent['formats'] == {expected_format}
    assert not intent['multiple']


@pytest.mark.parametrize('request_text', [
    '같은 표를 CSV와 JSON으로 저장해줘.',
    '같은 표를 CSV와 JSON으로 각각 저장해줘.',
    '같은 표를 CSV와 함께 JSON으로 저장해줘.',
    '같은 표를 CSV와 JSON 두 파일로 각각 저장해줘.',
])
def test_format_list_anchored_to_delivery_has_a_source_for_each_format(request_text):
    intent = explicit_export_intent(conversation(request_text))
    assert intent['multiple']
    assert intent['format_sources'] == {'csv': 0, 'json': 0}
    assert intent['format_clauses'] == {'csv': request_text, 'json': request_text}


def test_removing_one_format_keeps_the_survivors_original_evidence():
    initial_save = 'CSV로 저장해줘.'
    addition = '같은 결과를 JSON으로도 저장해줘.'
    cancellation = 'CSV만 취소해줘.'
    intent = explicit_export_intent(conversation(initial_save, addition, 'PDF만 취소해줘.', cancellation, '네.'))
    assert intent['formats'] == {'json'}
    assert intent['format_sources'] == {'json': 1}
    assert intent['format_clauses'] == {'json': addition}
    assert intent['cancellation_evidence'] == {'csv': (3, cancellation)}
    assert intent['global_cancellation'] is None


def test_reactivating_a_format_replaces_only_its_cancellation_evidence():
    initial_save = 'CSV와 JSON으로 저장해줘.'
    cancellation = '파일 저장만 취소해줘.'
    resume = 'JSON으로 저장해줘.'
    cancelled = explicit_export_intent(conversation(initial_save, cancellation))
    assert cancelled['format_sources'] == {}
    assert cancelled['global_cancellation'] == (1, cancellation)
    assert cancelled['cancellation_evidence'] == {'csv': (1, cancellation), 'json': (1, cancellation)}

    active = explicit_export_intent(conversation(initial_save, cancellation, resume, '네.'))
    assert active['format_sources'] == {'json': 2}
    assert active['format_clauses'] == {'json': resume}
    assert active['cancellation_evidence'] == {'csv': (1, cancellation)}
    assert active['global_cancellation'] is None


def test_unsuccessful_cancel_and_explicit_replacement_do_not_share_sources():
    initial_save = 'CSV로 저장해줘.'
    negated_cancel = 'CSV 저장은 취소하고 싶지 않아.'
    unchanged = explicit_export_intent(conversation(initial_save, negated_cancel, '네.'))
    assert unchanged['format_sources'] == {'csv': 0}
    assert unchanged['cancellation_evidence'] == {}

    replacement = 'CSV 말고 JSON으로 저장해줘.'
    changed = explicit_export_intent(conversation(initial_save, negated_cancel, replacement, '네.'))
    assert changed['format_sources'] == {'json': 2}
    assert changed['format_clauses'] == {'json': replacement}
    assert changed['cancellation_evidence'] == {}
