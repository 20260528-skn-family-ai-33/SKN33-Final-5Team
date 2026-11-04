"""A comparison word followed by an exclusion verb names rows to drop, not rows to keep.

Live 2026-11-03 (qwen3.5:4b, B-6 n09): "비용 100만원 미만인 행은 빼고 나머지만 남겨줘" was
confirmed as lt 1000000, keeping the opposite rows. The inverted meaning is asked, never guessed.
"""
import pytest
from agent_builder.conditions.condition_readiness import _grounded_enum, explicit_policies, source_catalog
from tests.conditions.test_condition_readiness import state, proposal, checked

TAIL = ' 빈 비용은 제외해. 중복은 유지해. JSON으로 저장해.'


def plan(operator):
    return {'filter.field': '비용', 'filter.operator': operator, 'filter.value': '1000000',
            'missing.policy': 'exclude', 'duplicate.policy': 'keep', 'output.format': 'json'}


@pytest.mark.parametrize('text', [
    '비용 100만원 미만인 행은 빼고 나머지만 남겨줘.',
    '비용이 100만원 미만인 거래는 제외하고 저장해줘.',
    '100만원 초과 행은 지우고 나머지를 저장해.',
    '비용 100만원 이상은 삭제해줘.',
    '100만원 이하인 건 말고 나머지만.',
])
def test_excluded_range_is_not_confirmed(text):
    s = state(text + TAIL)
    operator = next(op for word, op in (('미만', 'lt'), ('초과', 'gt'), ('이상', 'gte'), ('이하', 'lte')) if word in text)
    assert 'filter.operator' not in explicit_policies(s, source_catalog(s)[0])
    ledger = checked(s, proposal(s, plan(operator)))
    assert ledger['slots']['filter.operator']['status'] != 'specified'
    assert ledger['decision'] == 'clarify'
    assert 'filter.operator' in [q['key'] for q in ledger['questions']]


@pytest.mark.parametrize('text,operator', [
    ('비용이 100만원 미만인 행만 남겨줘.', 'lt'),
    ('비용이 100만원 미만인 행만 남기고 빈 값은 제외해.', 'lt'),
    ('비용 100만원 초과, 빈 비용 제외.', 'gt'),
    ('100만원 이상인 행만 골라서 나머지는 지워줘.', 'gte'),
    ('비용이 100만원 이하인 행을 뽑아줘.', 'lte'),
])
def test_kept_range_is_still_confirmed(text, operator):
    s = state(text + TAIL)
    assert explicit_policies(s, source_catalog(s)[0])['filter.operator']['value'] == operator
    ledger = checked(s, proposal(s, plan(operator)))
    assert ledger['slots']['filter.operator'] == {**ledger['slots']['filter.operator'], 'status': 'specified', 'value': operator}
    assert ledger['decision'] == 'ready'


def test_the_opposite_operator_is_not_inferred():
    # Dropping "미만" rows means gte, but code never flips the operator on its own.
    quote = '비용 100만원 미만인 행은 빼고 나머지만 남겨줘.'
    assert not _grounded_enum('filter.operator', 'lt', quote)
    assert not _grounded_enum('filter.operator', 'gte', quote)


def test_an_answer_to_the_question_confirms_the_operator():
    s = state('비용 100만원 미만인 행은 빼고 나머지만 남겨줘.' + TAIL)
    first = checked(s, proposal(s, plan('lt')))
    question = next(q for q in first['questions'] if q['key'] == 'filter.operator')
    s2 = {**s, 'condition_ledger': first, 'pending_questions': first['questions'],
          'messages': s['messages'] + [{'role': 'assistant', 'content': question['text']}, {'role': 'user', 'content': '이상'}],
          'condition_answers': [{'turn': 2, 'question_ids': [question['id']]}]}
    second = checked(s2, {'operations': [], 'conditions': [], 'unsupported_actions': []})
    assert second['slots']['filter.operator']['value'] == 'gte'


def test_reversing_previous_filter_never_restores_old_comparison():
    s = state('비용 100만원 미만인 행만 남겨줘.' + TAIL)
    s['condition_ledger'] = checked(s, proposal(s, plan('lt')))
    s['messages'].append({'role': 'user', 'content': '이제 비용 100만원 미만인 행은 빼줘.'})
    result = checked(s, proposal(s, plan('gte')))
    assert result['decision'] == 'clarify'
    assert result['slots']['filter.operator']['value'] is None


def test_filter_edit_retires_model_grounded_operator_even_if_replacement_omits_it():
    s = state('비용이 100만원과 같은 행만 남겨줘.' + TAIL)
    s['condition_ledger'] = checked(s, proposal(s, plan('eq')))
    s['messages'].append({'role': 'user', 'content': '이제 비용 100만원 미만인 행은 빼줘.'})
    result = checked(s, proposal(s, {}))
    assert result['decision'] == 'clarify'
    assert result['slots']['filter.operator']['value'] is None
