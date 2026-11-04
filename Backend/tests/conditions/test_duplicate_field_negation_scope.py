import pytest
from tests.conditions.test_condition_readiness import state
from agent_builder.conditions.condition_readiness import source_catalog,explicit_policies


@pytest.mark.parametrize('tail',['정렬하지 말고 JSON으로 저장해.','빈 비용은 제외해.','정렬은 하지 않아.'])
def test_other_policy_negation_does_not_erase_explicit_duplicate_column(tail):
    s=state('비용 100만원 초과인 행만 남겨줘. 중복 거래ID는 마지막 행만 남기고 '+tail)
    s['resources']=[{'id':'sales','kind':'table','columns':['거래ID','비용']}]
    assert explicit_policies(s,source_catalog(s)[0])['duplicate.field']['value']=='거래ID'


@pytest.mark.parametrize('text',['중복 거래ID 말고 비용으로 판단해','중복 거래ID는 아니야','중복 거래ID는 제외'])
def test_negated_duplicate_column_is_not_selected(text):
    s=state(text);s['resources']=[{'id':'sales','kind':'table','columns':['거래ID','비용']}]
    assert 'duplicate.field' not in explicit_policies(s,source_catalog(s)[0])


@pytest.mark.parametrize('text', ['거래ID 중복만 첫 행을 남겨줘.', '중복 거래ID만 첫 행을 남겨줘.'])
def test_named_duplicate_column_with_only_particle_is_grounded(text):
    s=state(text);s['resources']=[{'id':'sales','kind':'table','columns':['거래ID','비용']}]
    assert explicit_policies(s,source_catalog(s)[0])['duplicate.field']['value']=='거래ID'
