"""PR #54 review: negation scope, answer ownership and newer comparison evidence."""
from copy import deepcopy

import pytest

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.conditions.condition_readiness import source_catalog, validate_conditions
from tests.conditions.test_condition_readiness import checked, proposal, state
from tests.conditions.test_filter_revision_regressions import audited, result_rows


ROWS = [{'amount': value} for value in (20, 30, 40)]
EMPTY = {'operations': [], 'conditions': [], 'unsupported_actions': []}


def answer(s, ledger, key, label):
    question = next(q for q in ledger['questions'] if q['key'] == key)
    s.update(condition_ledger=ledger, pending_questions=ledger['questions'])
    s['messages'].extend([{'role': 'assistant', 'content': question['text']},
                          {'role': 'user', 'content': label}])
    s.setdefault('condition_answers', []).append({
        'turn': len(s['messages']) - 1, 'answer': label,
        'question_ids': [question['id']], 'candidate_keys': [key],
    })


@pytest.mark.parametrize(('phrase', 'suggested'), [
    ('초과하지는 않는', 'lte'), ('초과하지는 않는', 'gt'),
    ('초과하진 않는', 'lte'), ('초과하진 않는', 'gt'),
    ('이상도 아닌', 'lt'), ('이상도 아닌', 'gte'),
])
def test_emphasized_and_contracted_negations_require_confirmation(phrase, suggested):
    s = state(f'amount가 30 {phrase} 행만 남겨줘.')
    ledger = checked(s, proposal(s, {'filter.field': 'amount', 'filter.operator': suggested, 'filter.value': '30'}))
    assert ledger['decision'] == 'clarify'
    assert ledger['slots']['filter.operator']['value'] is None
    assert any(q['key'] == 'filter.operator' for q in ledger['questions'])


def test_newer_inclusive_correction_survives_audit_save_and_later_turns():
    s = state('amount가 30 이상이 아닌 행을 남겨줘.')
    s['condition_enforced'] = True
    first = checked(s, proposal(s, {'filter.field': 'amount', 'filter.operator': 'lt', 'filter.value': '30'}))
    answer(s, first, 'filter.operator', '미만')
    second = checked(s, EMPTY)
    s.update(condition_ledger=second, pending_questions=[])
    s['messages'].extend([{'role': 'assistant', 'content': '30 미만인 행을 남깁니다.'},
                          {'role': 'user', 'content': '아니, 경계값도 포함해서 이하로 바꿔줘'}])
    third = checked(s, proposal(s, {'filter.operator': 'lte'}, []))
    assert third['decision'] == 'ready'
    assert third['slots']['filter.operator']['value'] == 'lte'
    s['condition_ledger'] = third
    assert result_rows(s, ROWS, [('amount', 'lte', 30)]) == ROWS[:2]
    validated = audited(s, ROWS, [('amount', 'lte', 30)])
    require_save_ready({**s, **validated, 'confirmed': True})
    s['messages'].extend([{'role': 'assistant', 'content': '경계값도 포함합니다.'},
                          {'role': 'user', 'content': '그리고 JSON으로 저장해줘.'}])
    fourth = checked(s, EMPTY)
    assert fourth['decision'] == 'ready'
    assert fourth['slots']['filter.operator']['value'] == 'lte'
    s['condition_ledger'] = fourth
    assert result_rows(s, ROWS, [('amount', 'lte', 30)]) == ROWS[:2]


@pytest.mark.parametrize('label', ['이상', '이상 .'])
@pytest.mark.parametrize('model_selects_operator', [False, True])
def test_column_choice_cannot_answer_the_operator_question(model_selects_operator, label):
    s = state('30 이상이 아닌 행을 남겨줘.')
    s['resources'] = [{'id': 'table', 'kind': 'table', 'name': 'table.csv',
                       'columns': ['이상', '정상'], 'profile': {}}]
    first = checked(s, proposal(s, {'filter.field': None, 'filter.operator': 'lt', 'filter.value': '30'}))
    answer(s, first, 'filter.field', label)
    raw = proposal(s, {'filter.operator': 'gte'}, []) if model_selects_operator else EMPTY
    second = checked(s, raw)
    assert second['decision'] == 'clarify'
    assert second['slots']['filter.field']['value'] == '이상'
    assert second['slots']['filter.operator']['status'] != 'specified'
    assert any(q['key'] == 'filter.operator' for q in second['questions'])
    # The event remains attached to the field even when its pending question is gone.
    s.update(condition_ledger=second, pending_questions=second['questions'])
    assert checked(s, EMPTY)['slots']['filter.operator']['status'] != 'specified'


def test_legacy_column_answer_cannot_remain_a_confirmed_operator():
    s = state('30 이상이 아닌 행을 남겨줘.')
    s['resources'] = [{'id': 'table', 'kind': 'table', 'name': 'table.csv',
                       'columns': ['이상', '정상'], 'profile': {}}]
    first = checked(s, proposal(s, {'filter.field': None, 'filter.operator': 'lt', 'filter.value': '30'}))
    answer(s, first, 'filter.field', '이상')
    # Reproduce a persisted ready ledger written by the previous PR revision.
    stale = deepcopy(first)
    evidence = next(e for e in source_catalog(s)[0] if e['id'] == 'user:2:0')
    for key, value in [('filter.field', '이상'), ('filter.operator', 'gte')]:
        stale['slots'][key].update(status='specified', value=value, evidence=[evidence],
                                    evidence_ids=[evidence['id']], extraction='literal_policy_rule')
    stale.update(decision='ready', questions=[], validated_user_turn=2)
    s.update(condition_ledger=stale, pending_questions=[])
    s['messages'].extend([{'role': 'assistant', 'content': '조건을 확인했습니다.'},
                          {'role': 'user', 'content': 'JSON으로 저장해줘.'}])
    ledger = checked(s, EMPTY)
    assert ledger['decision'] == 'clarify'
    assert ledger['slots']['filter.operator']['status'] != 'specified'


def test_unrelated_recent_evidence_cannot_make_an_old_answer_newer():
    s = state('amount가 30 이상이 아닌 행을 남겨줘.')
    first = checked(s, proposal(s, {'filter.field': 'amount', 'filter.operator': 'lt', 'filter.value': '30'}))
    answer(s, first, 'filter.operator', '미만')
    second = checked(s, EMPTY)
    s.update(condition_ledger=second, pending_questions=[], condition_enforced=True)
    s['messages'].extend([{'role': 'assistant', 'content': '30 미만인 행을 남깁니다.'},
                          {'role': 'user', 'content': '아니, amount가 30 이하인 행을 남겨줘.'},
                          {'role': 'assistant', 'content': '조건을 변경합니다.'},
                          {'role': 'user', 'content': 'JSON으로 저장해줘.'}])
    raw = proposal(s, {'filter.operator': 'lt'}, [])
    raw['conditions'][0]['evidence_ids'] = ['user:2:0', 'user:6:0']
    ledger = checked(s, raw)
    assert ledger['decision'] == 'ready'
    assert ledger['slots']['filter.operator']['value'] == 'lte'
    s['condition_ledger'] = ledger
    assert result_rows(s, ROWS, [('amount', 'lte', 30)]) == ROWS[:2]


def test_operator_choice_identity_survives_repeated_model_proposals():
    s = state('amount가 30 이상이 아닌 행을 남겨줘.')
    first = checked(s, proposal(s, {'filter.field': 'amount', 'filter.operator': 'lt', 'filter.value': '30'}))
    answer(s, first, 'filter.operator', '미만')
    # Older answer events recorded question IDs without candidate_keys.
    s['condition_answers'][0].pop('candidate_keys')
    second = checked(s, EMPTY)
    s.update(condition_ledger=second, pending_questions=[])
    for _ in range(2):
        s['messages'].extend([{'role': 'assistant', 'content': '조건을 확인했습니다.'},
                              {'role': 'user', 'content': 'JSON으로 저장해줘.'}])
        raw = proposal(s, {'filter.operator': 'lt'}, [])
        raw['conditions'][0]['evidence_ids'] = ['user:2:0']
        ledger = checked(s, raw)
        assert ledger['decision'] == 'ready'
        assert ledger['slots']['filter.operator']['value'] == 'lt'
        s['condition_ledger'] = ledger


@pytest.mark.parametrize(('opening', 'closing'), [('“', '”'), ("'", "'"), ('`', '`'), ('「', '」')])
def test_quoted_negated_example_does_not_conflict_with_the_instruction(opening, closing):
    s = state(f'메모의 예시는 {opening}30 이상이 아닌 행{closing}이야. 실제로는 amount가 30 이상인 행만 남겨줘.')
    ledger = checked(s, proposal(s, {'filter.field': 'amount', 'filter.operator': 'gte', 'filter.value': '30'}))
    assert ledger['decision'] == 'ready'
    assert ledger['slots']['filter.operator']['value'] == 'gte'
    s.update(condition_ledger=ledger, condition_enforced=True)
    assert result_rows(s, ROWS, [('amount', 'gte', 30)]) == ROWS[1:]


@pytest.mark.parametrize('reverse_sentences', [False, True])
def test_document_keeps_its_positive_evidence_beside_a_negated_occurrence(reverse_sentences):
    s = state('첨부 문서에 정한 amount 기준으로 행을 골라줘.')
    sentences = ['amount가 30 이상인 행을 남긴다.', '30 이상이 아닌 값은 대상이 아니다.']
    if reverse_sentences:
        sentences.reverse()
    s['resources'] = [{'id': 'doc', 'kind': 'document', 'name': 'policy.txt', 'text': ' '.join(sentences)}]
    catalog = source_catalog(s)[0]
    evidence_id = next(e['id'] for e in catalog if e['source_id'] == 'doc')
    raw = proposal(s, {'filter.field': 'amount', 'filter.operator': 'gte', 'filter.value': '30'})
    for condition in raw['conditions']:
        condition['evidence_ids'] = [evidence_id]
    ledger = validate_conditions(raw, s, catalog)
    assert ledger['decision'] == 'ready'
    assert ledger['slots']['filter.operator']['value'] == 'gte'
    s.update(condition_ledger=ledger, condition_enforced=True)
    assert result_rows(s, ROWS, [('amount', 'gte', 30)]) == ROWS[1:]
