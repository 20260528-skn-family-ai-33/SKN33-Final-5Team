"""Captured public 9B R04 candidate and cases where its effects are not equivalent."""
import pytest

from agent_builder.design.condition_requirements import project_owned
from agent_builder.design.requirement_ledger import extract_plan
from tests.design.test_condition_requirement_ownership import validated_state
from tests.conditions.test_condition_readiness import checked, proposal, state as new_state


REQUEST = '연결한 표에서 customer_id가 중복이면 첫 행만 남겨 JSON으로 저장해줘. 빈 값은 오류.'


def other(action, description='customer_id 중복 제거 및 JSON 저장'):
    return dict(source_message=0, description=description, spec=dict(kind='other', action=action))


def test_live_r04_compound_candidate_is_covered_by_typed_policy_and_export():
    state = validated_state(REQUEST)
    plan = extract_plan(dict(tasks=[other('deduplicate_and_export_json')], unresolved=[]), state)
    assert [task['spec'] for task in plan['tasks']] == [
        dict(kind='policy', missing_policy='error', duplicate_policy='first', duplicate_field='customer_id'),
        dict(kind='export', format='json'),
    ]
    assert not plan['unresolved']


@pytest.mark.parametrize('action', ['deduplicate_and_export_csv', 'deduplicate_and_export_json_and_notify',
                                     'export_json_and_deduplicate'])
def test_different_format_side_effect_or_artifact_order_is_kept(action):
    state = validated_state(REQUEST)
    plan = extract_plan(dict(tasks=[other(action)], unresolved=[]), state)
    assert any(t['spec'] == dict(kind='other', action=action) for t in plan['tasks'])


def test_extra_delivery_in_the_candidate_description_is_kept():
    state = validated_state(REQUEST)
    candidate = other('deduplicate_and_export_json', 'customer_id 중복 제거 및 JSON 저장 후 장부를 전달')
    plan = extract_plan(dict(tasks=[candidate], unresolved=[]), state)
    assert any(t['description'] == candidate['description'] for t in plan['tasks'])


@pytest.mark.parametrize('retention', [None, 'keep'])
def test_a_missing_value_or_keep_policy_does_not_cover_a_deduplication_action(retention):
    candidates = [other('deduplicate_and_export')]
    expected = {'policy': dict(source_message=0, description='빈값 유지',
                               spec=dict(kind='policy', duplicate_policy=retention, duplicate_field=None, missing_policy='keep')),
                'export': dict(source_message=0, description='CSV 저장', spec=dict(kind='export', format='csv'))}
    state = {'messages':[dict(role='user', content='빈값을 유지하고 CSV로 저장해줘.')],
             'resources':[dict(id='sales', kind='table', columns=['customer_id'])]}
    assert candidates[0] in project_owned(candidates, expected, state)


def test_same_column_on_two_input_tables_cannot_prove_the_target_of_a_compound_action():
    candidates = [other('deduplicate_and_export_json')]
    expected = {'policy': dict(source_message=0, description='중복 정책',
                               spec=dict(kind='policy', duplicate_policy='first', duplicate_field='customer_id',
                                         missing_policy='error')),
                'export': dict(source_message=0, description='JSON 저장', spec=dict(kind='export', format='json'))}
    state = {'messages': [dict(role='user', content='매출표와 활동표를 조합해 JSON으로 저장해줘.')],
             'resources': [dict(id=name, kind='table', columns=['customer_id']) for name in ('sales', 'activity')]}
    assert candidates[0] in project_owned(candidates, expected, state)


@pytest.mark.parametrize('description,covered', [
    ('amount 가 20 이상인 행을 필터링하여 JSON 형식으로 저장', True),  # R02 live
    ('amount >= 20.0인 행만 JSON으로 저장', True),
    ('amount 가 200 이상인 행을 필터링하여 JSON 형식으로 저장', False),
    ('amount 가 20 초과인 행을 필터링하여 JSON 형식으로 저장', False),
    ('cost 가 20 이상인 행을 필터링하여 JSON 형식으로 저장', False),
    ('amount 가 20 이상인 행을 제외하고 JSON 형식으로 저장', False),
    ('amount 가 20 이상인 행을 필터링하여 CSV 형식으로 저장', False),
    ('amount 가 20 이상인 행을 필터링하여 JSON 형식으로 저장 후 팀에 전송', False),
    ('JSON 저장 후 amount >= 20인 행만 선별', False),
])
def test_compound_filter_compares_field_number_operator_and_output(description, covered):
    request = '연결된 표에서 amount가 20 이상인 행만 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
    current = new_state(request)
    current['resources'] = [dict(id='sales', name='sales.json', kind='table', columns=['amount', 'cost'])]
    facts = {'filter.field':'amount', 'filter.operator':'gte', 'filter.value':'20',
             'missing.policy':'error', 'duplicate.policy':'keep', 'output.format':'json'}
    current.update(condition_enforced=True, condition_required=True,
                   condition_ledger=checked(current, proposal(current, facts, ['filter'])))
    plan = extract_plan(dict(tasks=[other('filter_and_export', description)], unresolved=[]), current)
    assert not plan['unresolved']
    assert ('other' not in [task['spec']['kind'] for task in plan['tasks']]) == covered


@pytest.mark.parametrize('description,covered', [
    ('customer_id 중복 제거 및 빈값 처리 후 JSON 저장', True),  # historical R04
    ('customer_id 중복 행 정리 및 JSON 저장', True),  # historical M02
    ('customer_id 중복 제거 및 빈값 제외 후 JSON 저장', False),
])
def test_legacy_compound_recognizes_only_covered_missing_policy(description, covered):
    plan = extract_plan(dict(tasks=[other('deduplicate_and_export', description)], unresolved=[]),
                        validated_state(REQUEST))
    assert ('other' not in [task['spec']['kind'] for task in plan['tasks']]) == covered


@pytest.mark.parametrize('action,description', [
    ('clarify_data_source', '매출 보고서 데이터 소스 및 형식 확인'),
    ('email_delivery_with_analysis', '지난주 매출 분석 및 이메일 발송'),
])
def test_unrelated_historical_actions_remain_visible(action, description):
    plan = extract_plan(dict(tasks=[other(action, description)], unresolved=[]), validated_state(REQUEST))
    assert any(task['spec'] == dict(kind='other', action=action) for task in plan['tasks'])
