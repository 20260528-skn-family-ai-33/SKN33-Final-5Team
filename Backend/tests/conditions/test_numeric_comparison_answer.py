import pytest
from tests.conditions.test_clarification_choices import vague,answer_value,judge,state
from tests.conditions.test_condition_readiness import Fake
from agent_builder.conditions.input_resolution import numeric_answer,value_answer
from agent_builder.conditions.condition_readiness import judge_conditions, literal_condition_issues
from workflow_engine.executors.legacy_tools.filter_sort import filter_sort_node


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


def pending_amount(field):
    s = state(f'{field} 열에서 큰 행만 골라줘.')
    s['resources'] = [{'id': 't', 'kind': 'table', 'columns': [field],
                       'profile': {field: {'types': ['number']}}}]
    ledger = judge(s, {'filter.field': (field, field), 'filter.operator': (None, ''), 'filter.value': (None, '')})
    s.update(condition_ledger=ledger, pending_questions=ledger['questions'])
    return s, ledger


@pytest.mark.parametrize('field,answer,threshold', [
    ('비용(만원)', '100만원 이상', 100), ('매출액 [천원]', '1.5만원 이상', 15),
    ('비용(만원)', '100 이상', 100), ('비용(만원)', '1,000원 이상', 0.1),
    ('비용(원)', '100만원 이상', 1000000),
])
def test_answer_is_executed_in_units_of_column(field, answer, threshold):
    s, ledger = pending_amount(field)
    result = judge_conditions(answer_value(s, ledger, answer), Fake([]))
    slots = result['ledger']['slots']
    assert result['model_calls'] == 0 and result['ledger']['decision'] == 'ready'
    assert slots['filter.value']['value'] == threshold
    rows = [{field: threshold - 1}, {field: threshold}, {field: threshold + 1}]
    context = {'columns': [field], 'rows': rows, 'file_id': 't', 'trace': []}
    filter_sort_node(context, [{'field': field, 'value_type': 'number',
                                'operator': slots['filter.operator']['value'], 'value': slots['filter.value']['value']}], [])
    assert context['rows'] == rows[1:]


@pytest.mark.parametrize('amount', ['9007199254740993', '1.00000000000000000000000000001', '9' * 310])
def test_inexact_threshold_stays_missing(amount):
    s, ledger = pending_amount('비용')
    result = judge_conditions(answer_value(s, ledger, amount + ' 이상'), Fake([]))
    assert result['model_calls'] == 0 and result['ledger']['decision'] == 'clarify'
    assert result['ledger']['slots']['filter.value']['status'] == 'missing'
    assert '정확히 표현' in result['ledger']['questions'][0]['text']


@pytest.mark.parametrize('field,answer', [('비용(만원)', '100명'), ('비용(USD)', '100만원'),
                                          ('비용[단위: 포인트]', '100만원')])
def test_incompatible_explicit_units_ask_for_the_column_unit(field, answer):
    s, ledger = pending_amount(field)
    result = judge_conditions(answer_value(s, ledger, answer + ' 이상'), Fake([]))['ledger']
    assert result['slots']['filter.value']['status'] == 'missing'
    assert field in result['questions'][0]['text']


@pytest.mark.parametrize('model_value', ['1000000', '100만원'])
def test_model_cannot_pass_a_won_threshold_on_a_dollar_column(model_value):
    field = '비용(USD)'
    s = state(f'{field} 값이 100만원 이상인 행만 골라줘.')
    ledger = judge(s, {'filter.value': (model_value, '100만원'), 'filter.operator': ('gte', '이상'),
                       'filter.field': (field, field)})
    assert ledger['slots']['filter.value']['status'] == 'missing'


@pytest.mark.parametrize('raw_value,expected', [('100', 'specified'), ('1000000', 'missing')])
def test_model_value_must_be_grounded_in_column_units(raw_value, expected):
    field = '매출액(만원)'
    s = state(f'{field} 값이 100만원 이상인 행만 골라줘.')
    ledger = judge(s, {'filter.value': (raw_value, '100만원'), 'filter.operator': ('gte', '이상'),
                       'filter.field': (field, field)})
    assert ledger['slots']['filter.value']['status'] == expected


def test_model_cannot_treat_the_digits_in_a_compound_amount_as_column_units():
    field = '매출액(만원)'
    s = state(f'{field} 값이 100억 원 이상인 행만 골라줘.')
    ledger = judge(s, {'filter.value': ('100', '100억'), 'filter.operator': ('gte', '이상'),
                       'filter.field': (field, field)})
    assert ledger['slots']['filter.value']['status'] == 'missing'


@pytest.mark.parametrize('field,answer,stale', [
    ('비용(만원)', '100만원 이상', 1000000.0),
    ('비용', '9007199254740993 이상', 9007199254740992.0),
])
def test_saved_threshold_requires_correct_units_and_precision(field, answer, stale):
    s, ledger = pending_amount(field)
    s = answer_value(s, ledger, answer)
    ledger = judge_conditions(s, Fake([]))['ledger']
    threshold = ledger['slots']['filter.value']
    threshold.update(value=stale, status='specified', evidence=[{'source_id': 'user', 'quote': answer}])
    s.update(condition_ledger={**ledger, 'decision': 'ready'}, condition_enforced=True)
    assert any('filter.value' in issue for issue in literal_condition_issues(s))
