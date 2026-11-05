"""Second PR #54 review: bind comparison evidence to its instruction and column."""
from copy import deepcopy

import pytest

from agent_builder.conditions.condition_readiness import source_catalog, validate_conditions
from tests.conditions.test_condition_readiness import checked, proposal, state
from tests.conditions.test_filter_revision_regressions import audited, result_rows
from tests.conditions.test_pr54_review import answer, EMPTY, ROWS


@pytest.mark.parametrize('phrase', ['30 이상이면 안 돼', '30 이상인 행은 안 돼'])
@pytest.mark.parametrize('suggested', ['gte', 'lt'])
def test_prohibited_range_requires_confirmation_and_executes_the_answer(phrase, suggested):
    s = state(f'amount가 {phrase}. 나머지 행만 남겨줘.')
    first = checked(s, proposal(s, {'filter.field': 'amount', 'filter.operator': suggested, 'filter.value': '30'}))
    assert first['decision'] == 'clarify'
    assert any(q['key'] == 'filter.operator' for q in first['questions'])
    answer(s, first, 'filter.operator', '미만')
    second = checked(s, EMPTY)
    assert second['decision'] == 'ready'
    s.update(condition_ledger=second, condition_enforced=True)
    assert result_rows(s, ROWS, [('amount', 'lt', 30)]) == ROWS[:1]
    # An already generated opposite draft must not pass audit/validation either.
    assert audited(s, ROWS, [('amount', 'gte', 30)])['issues']


@pytest.mark.parametrize('phrase', ['30 이상이면 안 돼', '30 이상인 행은 안 돼'])
def test_prohibition_also_blocks_a_wrong_draft_without_a_condition_ledger(phrase):
    s = state(f'amount가 {phrase}. 나머지 행만 남겨줘.')
    assert result_rows(s, ROWS, [('amount', 'lt', 30)]) == ROWS[:1]
    assert audited(s, ROWS, [('amount', 'gte', 30)])['issues']


def document_proposal(text, field='amount', value='30', operator='gte'):
    s = state(f'첨부 문서에 정한 {field} 기준으로 행을 골라줘.')
    s['resources'] = [{'id': 'doc', 'kind': 'document', 'name': 'policy.txt', 'text': text}]
    catalog = source_catalog(s)[0]
    evidence_id = next(e['id'] for e in catalog if e['source_id'] == 'doc')
    raw = proposal(s, {'filter.field': field, 'filter.operator': operator, 'filter.value': value})
    for condition in raw['conditions']:
        condition['evidence_ids'] = [evidence_id]
    return s, raw, catalog


@pytest.mark.parametrize('positive', ['qty가 30 이상인 행만 남긴다.', 'amount가 100 이상인 행만 남긴다.'])
@pytest.mark.parametrize('reverse', [False, True])
def test_another_column_or_threshold_cannot_ground_the_rejected_comparison(positive, reverse):
    sentences = ['amount가 30 이상인 행은 제외한다.', positive]
    s, raw, catalog = document_proposal(' '.join(reversed(sentences) if reverse else sentences))
    ledger = validate_conditions(raw, s, catalog)
    assert ledger['decision'] == 'clarify'
    assert ledger['slots']['filter.operator']['status'] != 'specified'
    # The same check must reject an older, wrongly confirmed ledger on audit.
    stale = deepcopy(ledger)
    stale.update(decision='ready', questions=[], unresolved=[])
    stale['slots']['filter.operator'].update(status='specified', value='gte')
    s.update(condition_ledger=stale, condition_enforced=True)
    assert audited(s, ROWS, [('amount', 'gte', 30)])['issues']
    answer(s, ledger, 'filter.operator', '미만')
    s['condition_ledger'] = checked(s, EMPTY)
    assert s['condition_ledger']['decision'] == 'ready'
    assert result_rows(s, ROWS, [('amount', 'lt', 30)]) == ROWS[:1]


@pytest.mark.parametrize('field', ['amount', 'qty'])
def test_document_accepts_only_the_selected_columns_positive_rule(field):
    text = 'amount가 30 이상인 행만 남긴다. qty가 30 미만인 행만 남긴다.'
    operator = 'gte' if field == 'amount' else 'lt'
    s, raw, catalog = document_proposal(text, field=field, operator=operator)
    ledger = validate_conditions(raw, s, catalog)
    assert ledger['decision'] == 'ready'
    assert ledger['slots']['filter.operator']['value'] == operator


@pytest.mark.parametrize('legacy_event', [False, True])
@pytest.mark.parametrize('model_proposes_operator', [False, True])
def test_numeric_comparison_in_a_column_choice_does_not_set_the_operator(model_proposes_operator, legacy_event):
    s = state('30 이상이 아닌 행을 남겨줘.')
    s['resources'] = [{'id': 'table', 'kind': 'table', 'name': 'table.csv',
                       'columns': ['30점 이상', '정상'], 'profile': {}}]
    first = checked(s, proposal(s, {'filter.field': None, 'filter.operator': 'lt', 'filter.value': '30'}))
    answer(s, first, 'filter.field', '30점 이상')
    if legacy_event:
        s['condition_answers'][-1].pop('candidate_keys')
    raw = proposal(s, {'filter.operator': 'gte'}, []) if model_proposes_operator else EMPTY
    second = checked(s, raw)
    assert second['decision'] == 'clarify'
    assert second['slots']['filter.field']['value'] == '30점 이상'
    assert second['slots']['filter.operator']['status'] != 'specified'
    assert any(q['key'] == 'filter.operator' for q in second['questions'])
    s.update(condition_ledger=second, pending_questions=second['questions'])
    s['messages'].extend([{'role': 'assistant', 'content': '비교 방식을 확인해 주세요.'},
                          {'role': 'user', 'content': 'JSON으로 저장해줘.'}])
    assert checked(s, EMPTY)['slots']['filter.operator']['status'] != 'specified'
    answer(s, second, 'filter.operator', '미만')
    s['condition_ledger'] = checked(s, EMPTY)
    s['condition_enforced'] = True
    assert s['condition_ledger']['decision'] == 'ready'
    rows = [{'30점 이상': n, '정상': 0} for n in (20, 30, 40)]
    assert result_rows(s, rows, [('30점 이상', 'lt', 30)]) == rows[:1]


@pytest.mark.parametrize(('opening', 'closing'), [('“', '”'), ('"', '"'), ("'", "'"), ('`', '`')])
def test_quoted_real_criterion_survives_validation_audit_and_execution(opening, closing):
    s = state(f'amount가 {opening}30 이상{closing}인 행만 남겨줘.')
    ledger = checked(s, proposal(s, {'filter.field': 'amount', 'filter.operator': 'gte', 'filter.value': '30'}))
    assert ledger['decision'] == 'ready'
    assert ledger['slots']['filter.operator']['value'] == 'gte'
    s.update(condition_ledger=ledger, condition_enforced=True)
    assert result_rows(s, ROWS, [('amount', 'gte', 30)]) == ROWS[1:]
    # The same instruction must also repair a draft that drops the equal row.
    assert result_rows(s, ROWS, [('amount', 'gt', 30)]) == ROWS[1:]


def test_quoted_example_boundary_is_not_an_emphasized_instruction():
    s = state('예시는 “30 이상”인 행만 남기는 것이야. 실제로는 amount가 30 미만인 행만 남겨줘.')
    ledger = checked(s, proposal(s, {'filter.field': 'amount', 'filter.operator': 'lt', 'filter.value': '30'}))
    assert ledger['decision'] == 'ready'
    s.update(condition_ledger=ledger, condition_enforced=True)
    assert result_rows(s, ROWS, [('amount', 'lt', 30)]) == ROWS[:1]


@pytest.mark.parametrize(('field', 'amount', 'value'), [
    ('금액', '백만원', '1000000'), ('금액(만원)', '백만원', '100'),
    ('금액(만원)', '100만원', '100'), ('amount', '30.5', '30.5'),
])
def test_scoped_document_evidence_keeps_korean_numerals_column_units_and_decimals(field, amount, value):
    s, raw, catalog = document_proposal(f'{field}가 {amount} 이상인 행만 남긴다.', field=field, value=value)
    assert validate_conditions(raw, s, catalog)['decision'] == 'ready'


def test_an_unbound_document_comparison_is_not_assigned_to_the_requested_column():
    s, raw, catalog = document_proposal('amount와 qty를 점검한다. 30 이상인 행만 남긴다.')
    ledger = validate_conditions(raw, s, catalog)
    assert ledger['decision'] == 'clarify'
    assert any(q['key'] == 'filter.operator' for q in ledger['questions'])


def test_later_field_options_do_not_reclassify_an_earlier_numeric_request():
    s = state('30점 이상')
    s['resources'] = [{'id': 'table', 'kind': 'table', 'columns': ['30점 이상', '정상'], 'profile': {}}]
    first = checked(s, proposal(s, {'filter.field': None, 'filter.operator': 'gte', 'filter.value': '30'}))
    answer(s, first, 'filter.field', '정상')
    second = checked(s, EMPTY)
    assert second['decision'] == 'ready'
    assert second['slots']['filter.field']['value'] == '정상'
    assert second['slots']['filter.operator']['value'] == 'gte'
