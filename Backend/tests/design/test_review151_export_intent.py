"""Review 151 items 5/6/7. Controlled extraction packets, never live-model results."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.condition_requirements import multiple_outputs
from agent_builder.design.export_intent import explicit_export_intent
from agent_builder.design.output_selection_request import literal_output_format, output_selection
from agent_builder.design.processing_guard import validate_processing_presence
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import (
    extract_plan, ledger_fingerprint, make_requirement_check, plan_digest,
)
from tests.design.test_feedback_requirement_extraction import packet, state
from tests.design.test_feedback_service_scenarios import order_draft


def followup(*messages):
    current = state()
    current['messages'].extend(dict(role='user', content=text) for text in messages)
    return current


def exported(plan):
    return [task['spec']['format'] for task in plan['tasks'] if task['spec']['kind'] == 'export']


@pytest.mark.parametrize('reply', [
    '파일 저장은 취소해줘.',
    '파일 저장을 생략해줘.',
    '파일을 저장하지 마.',
])
@pytest.mark.parametrize('resume', [False, True])
def test_cancel_does_not_restore_named_csv_export(reply, resume):
    current = followup(reply, *(['네.'] if resume else []))
    raw = packet()
    raw['tasks'] = [task for task in raw['tasks'] if task['spec']['kind'] != 'export']
    assert literal_output_format(current) is None
    assert output_selection(current) is None
    raw['tasks'] = [task for task in raw['tasks'] if task['spec']['kind'] != 'select_fields']
    result = extract_plan(raw, current)
    assert exported(result) == []
    assert not any(task['spec']['kind'] == 'select_fields' for task in result['tasks'])
    with pytest.raises(ValueError, match='취소한 파일 저장'):
        extract_plan(packet(), current)


@pytest.mark.parametrize('reply', [
    '같은 열을 JSON으로도 저장해줘.',
    '같은 열을 json으로도 저장해주세요.',
])
@pytest.mark.parametrize('resume', [False, True])
def test_additive_format_preserves_csv_and_json_and_rejects_csv_only(reply, resume):
    current = followup(reply, *(['네.'] if resume else []))
    assert multiple_outputs(current)
    assert output_selection(current) == ['주문번호', '주문금액']
    with pytest.raises(ValueError, match='추가로 저장'):
        extract_plan(packet(), current)
    raw = packet()
    raw['tasks'].append(dict(source_message=2, description='같은 결과 JSON 저장',
                             spec=dict(kind='export', format='json')))
    assert exported(extract_plan(raw, current)) == ['csv', 'json']


@pytest.mark.parametrize('text', [
    '파일 저장은 취소하지 마.',
    '파일 저장 취소는 하지 말아줘.',
    '“파일 저장은 취소해줘.”는 예시야.',
    '‘파일 저장은 취소해줘.’는 예시야.',
    '`파일 저장은 취소해줘.`라는 문구를 확인했어.',
    '파일 저장은 취소해줘라는 문구의 뜻이 뭐야?',
    '예를 들어 파일 저장은 취소해줘.',
    '같은 열을 JSON으로도 저장하지 마.',
    '“같은 열을 JSON으로도 저장해줘.”는 예시야.',
    '예시로 같은 열을 JSON으로도 저장해줘.',
    'CSV와 JSON은 무슨 뜻인가요?',
    '“CSV와 JSON 각각 저장해줘”라는 예시야.',
])
def test_negative_explanation_and_quoted_examples_preserve_existing_export(text):
    current = followup(text, '네.')
    intent = explicit_export_intent(current)
    assert not intent['cancelled']
    assert not intent['additional']
    assert not multiple_outputs(current)
    assert exported(extract_plan(packet(), current)) == ['csv']


@pytest.mark.parametrize('text', [
    'JSON 저장은 취소해줘.',
    'JSON 파일 저장은 취소해줘.',
    'JSON으로 저장하지 마.',
    '참고 파일 저장은 취소해줘.',
])
def test_scoped_cancellation_cannot_cancel_all_files(text):
    current = followup(text, '확인했어.')
    assert not explicit_export_intent(current)['cancelled']
    assert exported(extract_plan(packet(), current)) == ['csv']


def test_cancel_then_new_affirmative_save_and_additional_format():
    current = followup('파일 저장은 취소해줘.', '같은 열을 JSON으로 저장해줘.',
                       '같은 열을 CSV로도 저장해줘.', '네.')
    intent = explicit_export_intent(current)
    assert not intent['cancelled']
    assert intent['formats'] == {'csv', 'json'}
    assert multiple_outputs(current)


def test_format_replacement_clears_old_multiple_output_scope():
    current = followup('같은 열을 JSON으로도 저장해줘.', '이제 CSV로 저장해줘.', '네.')
    assert explicit_export_intent(current)['formats'] == {'csv'}
    assert not multiple_outputs(current)
    assert exported(extract_plan(packet(), current)) == ['csv']


def test_cancel_all_clears_old_multiple_output_scope():
    current = followup('같은 열을 JSON으로도 저장해줘.', '파일 저장은 취소해줘.', '네.')
    assert not multiple_outputs(current)
    raw = packet()
    raw['tasks'].pop()
    assert exported(extract_plan(raw, current)) == []


@pytest.mark.parametrize('reply', [
    '정렬은 빼고 원본을 CSV로 저장해줘.',
    '정렬을 제외하고 원본을 CSV로 저장해줘.',
    '정렬은 생략하고 원본을 CSV로 저장해줘.',
])
@pytest.mark.parametrize('resume', [False, True])
def test_cancel_sort_allows_real_read_and_export_extraction(reply, resume):
    current = state()
    current['messages'] = [dict(role='user', content='주문내역.csv를 정렬해줘.'),
                           dict(role='assistant', content='어느 열로 정렬할까요?'),
                           dict(role='user', content=reply)]
    if resume:
        current['messages'].append(dict(role='user', content='네.'))
    raw = dict(tasks=[
        dict(source_message=1, description='원본 읽기',
             spec=dict(kind='read', file_ids=['table-source'])),
        dict(source_message=1, description='CSV 저장', spec=dict(kind='export', format='csv')),
    ], unresolved=[])
    assert exported(extract_plan(raw, current)) == ['csv']


@pytest.mark.parametrize('text', [
    '정렬은 무슨 뜻인가요?',
    '정렬해줘라는 말은 무슨 뜻인가요?',
    '예시로 정렬해줘.',
    '‘정렬해줘.’라는 예시를 확인했어.',
])
def test_processing_examples_do_not_require_processing(text):
    current = state()
    current['messages'] = [dict(role='user', content=text)]
    raw = dict(tasks=[dict(spec=dict(kind='read', file_ids=['table-source'])),
                      dict(spec=dict(kind='export', format='csv'))], unresolved=[])
    validate_processing_presence(raw, current)


def test_sort_cancellation_preserves_other_requested_processing():
    current = state()
    current['messages'] = [dict(role='user', content='주문금액을 계산하고 정렬해줘.'),
                           dict(role='user', content='정렬은 빼고 원본을 CSV로 저장해줘.')]
    raw = dict(tasks=[dict(spec=dict(kind='read', file_ids=['table-source'])),
                      dict(spec=dict(kind='export', format='csv'))], unresolved=[])
    with pytest.raises(ValueError, match='표 처리'):
        validate_processing_presence(raw, current)


def test_additive_formats_survive_requirement_check_and_later_acknowledgement():
    current = followup('같은 열을 JSON으로도 저장해줘.')
    raw = packet()
    raw['tasks'].append(dict(source_message=2, description='JSON 추가 저장',
                             spec=dict(kind='export', format='json')))

    class Transport:
        def invoke(self, messages):
            return {'content': json.dumps(raw, ensure_ascii=False)}

    check = make_requirement_check('fixture', Transport(), 'enforce')
    result = check(current)
    assert result['requirement_route'] == 'plan'
    assert exported(result['requirement_ledger']) == ['csv', 'json']
    resumed = {**current, **result}
    resumed['messages'] = deepcopy(current['messages']) + [dict(role='user', content='네.')]
    again = check(resumed)
    assert again['requirement_route'] == 'plan'
    assert exported(again['requirement_ledger']) == ['csv', 'json']


def test_multiple_artifact_runtime_coverage_still_blocks_save():
    current = followup('같은 열을 JSON으로도 저장해줘.')
    raw = packet()
    raw['tasks'].append(dict(source_message=2, description='JSON 추가 저장',
                             spec=dict(kind='export', format='json')))
    plan = extract_plan(raw, current)
    current.update(requirement_required=True, requirement_ledger=plan,
                   requirement_fingerprint=ledger_fingerprint(current),
                   requirement_digest=plan_digest(plan))
    issues = coverage_report(current, order_draft('table-source'))['issues']
    assert any('복수 결과 파일' in issue for issue in issues)



def test_same_turn_additional_json_preserves_both_exports():
    current = state()
    current['messages'][-1]['content'] += ' 같은 열을 JSON으로도 저장해줘.'
    raw = packet()
    raw['tasks'].append(dict(source_message=1, description='동일한 열 JSON 추가 저장',
                             spec=dict(kind='export', format='json')))
    assert output_selection(current) == ['주문번호', '주문금액']
    assert multiple_outputs(current)
    assert exported(extract_plan(raw, current)) == ['csv', 'json']
    with pytest.raises(ValueError, match='추가로 저장'):
        extract_plan(packet(), current)


def test_exact_sort_cancellation_reaches_plan_with_one_transport_call():
    current = state()
    current['messages'] = [
        dict(role='user', content='주문번호 오름차순으로 정렬해줘.'),
        dict(role='assistant', content='CSV로 저장할까요?'),
        dict(role='user', content='정렬은 빼고 원본을 CSV로 저장해줘.'),
    ]
    raw = dict(tasks=[
        dict(source_message=1, description='원본 읽기',
             spec=dict(kind='read', file_ids=['table-source'])),
        dict(source_message=1, description='CSV 저장', spec=dict(kind='export', format='csv')),
    ], unresolved=[])

    class Transport:
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            return {'content': json.dumps(raw, ensure_ascii=False)}

    transport = Transport()
    result = make_requirement_check('fixture', transport, 'enforce')(current)
    assert transport.calls == 1
    assert result['requirement_route'] == 'plan'
    assert result['requirement_failure'] is None
    assert [task['spec']['kind'] for task in result['requirement_ledger']['tasks']] == ['read', 'export']
