import pytest
from tests.conditions.test_clarification_choices import vague,answer_value
from tests.conditions.test_condition_readiness import Fake
from agent_builder.conditions.input_resolution import numeric_answer,value_answer
from agent_builder.conditions.condition_readiness import judge_conditions


@pytest.mark.parametrize('text,number,operator',[('100만원 이상',1000000,'gte'),('50만원 초과',500000,'gt'),('1,000,000원 이하',1000000,'lte'),('20만원 미만',200000,'lt')])
def test_complete_numeric_comparison_resolves_both_pending_slots(text,number,operator):
    s,ledger=vague();s=answer_value(s,ledger,text)
    assert value_answer(s) is None  # original plain-number contract stays strict
    assert numeric_answer(s)['value']==number
    fake=Fake([]);result=judge_conditions(s,fake)['ledger']
    assert fake.calls==0
    assert result['slots']['filter.value']['status']=='specified'
    assert result['slots']['filter.value']['value']==number
    assert result['slots']['filter.operator']['value']==operator
    assert 'filter.value' not in [q['key'] for q in result['questions']]


@pytest.mark.parametrize('text',['약 100만원 이상','100만원 미만은 제외','100만원에서 200만원 이상','100만원 정도 이상','-100만원 이상'])
def test_ambiguous_comparison_keeps_model_path(text):
    s,ledger=vague()
    assert numeric_answer(answer_value(s,ledger,text)) is None
