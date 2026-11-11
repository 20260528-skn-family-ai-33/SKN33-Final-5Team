"""Regression at the requirement/cached-plan boundary for a final grouped artifact."""
from copy import deepcopy
import pytest

from agent_builder.design.requirement_ledger import extract_plan, validate_plan
from tests.conditions.test_condition_readiness import state
from tests.design.test_condition_requirement_ownership import packet


def request():
    current = state('첨부한 표에서 team별 revenue의 합계로 집계한 뒤 sum의 값이 130 미만인 행만 CSV로 내보내줘.')
    current['resources'] = [dict(id='source', kind='table', columns=['team', 'revenue'])]
    raw = packet({'kind':'export', 'format':'csv'},
                 {'kind':'aggregate', 'fields':['team'], 'aggregation':'sum', 'sum_field':'revenue'},
                 {'kind':'filter', 'match':'all', 'filters':[{'field':'model_alias', 'operator':'lt',
                   'value':130, 'value_type':'number', 'compare_field':None}]},
                 {'kind':'rename', 'source':'model_alias', 'target':'sum'},
                 {'kind':'select_fields', 'fields':['team','sum']})
    return current, raw


def test_final_export_depends_on_grounded_aggregate_filter():
    current, raw = request()
    saved = deepcopy(raw)
    plan = extract_plan(raw, current)
    assert raw == saved
    assert [t['spec']['kind'] for t in plan['tasks']] == ['aggregate','filter','export']
    agg, selection, export = plan['tasks']
    assert selection['spec']['filters'][0]['field'] == 'sum'
    assert export['depends_on'] == [selection['id']] and selection['depends_on'] == [agg['id']]
    assert all(t['quote']==current['messages'][0]['content'] for t in plan['tasks'])


def test_cache_cannot_accept_disconnected_final_export():
    current, raw = request()
    raw['tasks'] = raw['tasks'][1:3] + raw['tasks'][:1]
    raw['tasks'][1]['spec']['filters'][0]['field'] = 'sum'
    plan = extract_plan(raw, current)
    plan['tasks'][-1]['depends_on'] = []
    with pytest.raises(ValueError):
        validate_plan(plan, current)


def test_proven_duplicate_exports_and_input_read_keep_one_connected_final_artifact():
    current, raw = request()
    raw['tasks'].insert(0, {'source_message':0, 'description':'원본 읽기',
                            'spec':{'kind':'read','file_ids':['source']}})
    raw['tasks'].append(deepcopy(raw['tasks'][1]))
    plan = extract_plan(raw,current)
    assert [t['spec']['kind'] for t in plan['tasks']] == ['read','aggregate','filter','export']
    assert [t['depends_on'] for t in plan['tasks']] == [[],['r1'],['r2'],['r3']]


@pytest.mark.parametrize('extra', [
    ' 추가로 sum 열 이름을 annual_sum으로 바꿔줘.', ' 마지막에 team, sum 열만 뽑아.',
    ' 원본도 별도 저장해.', ' CSV와 JSON 각각 만들어줘.', ' 보조 표와도 결합해.',
    ' 이어서 다른 표에도 적용해.',
])
def test_additional_request_is_preserved_by_extraction(extra):
    current, raw = request()
    current['messages'][0]['content'] += extra
    plan = extract_plan(raw, current)
    assert [t['spec'] for t in plan['tasks']] == [t['spec'] for t in raw['tasks']]
