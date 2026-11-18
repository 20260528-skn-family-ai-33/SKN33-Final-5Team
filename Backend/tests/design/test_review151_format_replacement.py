"""Same-turn output corrections must replace the named export's old format."""
import pytest

from agent_builder.design.condition_requirements import multiple_outputs
from agent_builder.design.export_intent import explicit_export_intent
from agent_builder.design.output_selection_request import literal_output_format, output_selection
from agent_builder.design.requirement_ledger import extract_plan
from tests.design.test_feedback_requirement_extraction import packet, state


@pytest.mark.parametrize('correction, expected', [
    ('CSV 말고 JSON으로 저장해줘.', 'json'),
    ('csv 말고 json으로 저장해주세요.', 'json'),
    ('CSV 말고 XLSX로 저장해줘.', 'xlsx'),
    ('CSV 말고 JSON으로 저장해줘. JSON 말고 CSV로 저장해줘.', 'csv'),
])
@pytest.mark.parametrize('acknowledge', [False, True])
def test_same_turn_replacement_accepts_new_format_and_rejects_stale_export(correction, expected, acknowledge):
    current = state()
    current['messages'][-1]['content'] += ' ' + correction
    if acknowledge:
        current['messages'].append(dict(role='user', content='네.'))
    assert explicit_export_intent(current)['formats'] == {expected}
    assert not multiple_outputs(current)
    assert literal_output_format(current) == expected
    assert output_selection(current) == ['주문번호', '주문금액']

    raw = packet()
    raw['tasks'][-1]['spec']['format'] = expected
    plan = extract_plan(raw, current)
    assert [task['spec'] for task in plan['tasks']] == [task['spec'] for task in raw['tasks']]
    raw['tasks'][-1]['spec']['format'] = 'csv' if expected != 'csv' else 'json'
    with pytest.raises(ValueError, match='결과 파일 저장'):
        extract_plan(raw, current)


def test_missing_export_is_projected_in_replacement_format():
    current = state()
    current['messages'][-1]['content'] += ' CSV 말고 JSON으로 저장해줘.'
    raw = packet()
    raw['tasks'].pop()
    plan = extract_plan(raw, current)
    assert plan['tasks'][-1]['spec'] == dict(kind='export', format='json')


@pytest.mark.parametrize('text', [
    '“CSV 말고 JSON으로 저장해줘.”라는 예시야.',
    '예를 들어 CSV 말고 JSON으로 저장해줘.',
    'CSV 말고 JSON으로 저장해줘라는 문구를 확인했어.',
    'CSV 말고 JSON으로 저장하지 마.',
])
def test_non_request_correction_does_not_replace_csv(text):
    current = state()
    current['messages'][-1]['content'] += ' ' + text
    assert literal_output_format(current) == 'csv'
    assert extract_plan(packet(), current)['tasks'][-1]['spec']['format'] == 'csv'


def test_additional_save_after_correction_requires_both_formats():
    current = state()
    current['messages'][-1]['content'] += ' CSV 말고 JSON으로 저장해줘. 같은 열을 XLSX로도 저장해줘.'
    intent = explicit_export_intent(current)
    assert intent['formats'] == {'json', 'xlsx'}
    assert intent['additional']
    raw = packet()
    raw['tasks'][-1]['spec']['format'] = 'json'
    with pytest.raises(ValueError, match='추가로 저장'):
        extract_plan(raw, current)
    raw['tasks'].append(dict(source_message=1, description='XLSX 추가 저장',
                             spec=dict(kind='export', format='xlsx')))
    plan = extract_plan(raw, current)
    assert [task['spec']['format'] for task in plan['tasks'] if task['spec']['kind'] == 'export'] == ['json', 'xlsx']
