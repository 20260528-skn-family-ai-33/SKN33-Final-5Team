import pytest
from tests.conditions.test_condition_readiness import state, proposal, checked, source_catalog
from agent_builder.conditions.condition_readiness import literal_condition_issues, plan_issues


def filtered():
    s = state('비용 100만원 초과인 행만 남겨줘. 빈 값은 제외하고 중복은 모두 유지해서 JSON으로 저장해줘.')
    s['resources'] = [{'id': 'sales', 'kind': 'table', 'name': 'sales.csv', 'columns': ['거래ID', '비용']}]
    s['condition_ledger'] = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt',
        'filter.value': '1000000', 'missing.policy': 'exclude', 'duplicate.policy': 'keep', 'output.format': 'json'}))
    return s


def next_dedup(s, text):
    s['messages'] += [{'role': 'assistant', 'content': '조건 확인'}, {'role': 'user', 'content': text}]
    catalog, _ = source_catalog(s)
    evidence = next(e['id'] for e in reversed(catalog) if e['source_id'] == 'user')
    return checked(s, {'operation': 'deduplicate', 'conditions': [
        {'key': key, 'status': 'specified', 'value': value, 'evidence_ids': [evidence]}
        for key, value in {'duplicate.field': '거래ID', 'duplicate.policy': 'first'}.items()], 'unsupported_actions': []})


@pytest.mark.parametrize('cancel', ['비용 선별 조건은 취소하고', '선별은 취소하고', '필터는 빼고'])
def test_cancelled_filter_does_not_return_when_only_dedup_requested(cancel):
    s = filtered()
    ledger = next_dedup(s, cancel + ' 거래ID 중복만 첫 행을 남겨줘.')
    assert ledger['decision'] == 'ready'
    assert ledger['operations'] == ['deduplicate']
    assert not any(key.startswith('filter.') for key in ledger['slots'])
    assert ledger['slots']['output.format']['value'] == 'json'
    assert ledger['slots']['missing.policy']['value'] == 'exclude'
    assert plan_issues(ledger, {'questions': [], 'unsupported': [], 'workflow': {'nodes': [
        {'tool': 'filter_sort', 'settings': {'execution_plan': {'filters': [{'field': '비용'}]}}}]}})


@pytest.mark.parametrize('prefix', ['비용 조건은 취소하지 말고', '선별은 취소하지 마.', ''])
def test_uncancelled_filter_is_preserved(prefix):
    s = filtered()
    ledger = next_dedup(s, prefix + ' 거래ID 중복은 첫 행을 남겨줘.')
    assert ledger['decision'] == 'ready'
    assert set(ledger['operations']) == {'filter', 'deduplicate'}
    assert ledger['slots']['filter.value']['value'] == 1000000


def test_old_ready_checkpoint_is_rejected_after_cancellation():
    s = filtered(); s['condition_enforced'] = True
    s['messages'].append({'role': 'user', 'content': '비용 선별 조건은 취소해줘.'})
    assert any('철회' in issue for issue in literal_condition_issues(s))


def test_cancelled_filter_stays_absent_on_later_reply_but_new_filter_can_be_added():
    s = filtered()
    s['condition_ledger'] = next_dedup(s, '선별은 취소하고 거래ID 중복만 첫 행을 남겨줘.')
    s['condition_ledger'] = next_dedup(s, '거래ID 중복은 첫 행을 남겨줘.')
    assert s['condition_ledger']['operations'] == ['deduplicate']
    s['messages'].append({'role': 'user', 'content': '비용 200만원 이상인 행만 다시 선별해줘.'})
    ledger = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gte', 'filter.value': '2000000'}))
    assert ledger['decision'] == 'ready'
    assert ledger['slots']['filter.value']['value'] == 2000000
    assert ledger['slots']['filter.operator']['value'] == 'gte'


def test_dedup_cancellation_removes_old_policy_and_retains_filter():
    s = filtered(); s['condition_ledger'] = next_dedup(s, '거래ID 중복은 첫 행을 남겨줘.')
    s['messages'].append({'role': 'user', 'content': '중복 제거는 취소해줘. 비용 조건은 유지해.'})
    ledger = checked(s, {'operation': 'filter', 'conditions': [], 'unsupported_actions': []})
    assert ledger['decision'] == 'ready'
    assert ledger['operations'] == ['filter']
    assert not any(key.startswith('duplicate.') for key in ledger['slots'])


@pytest.mark.parametrize('text', ['메모가 선별 취소인 행을 찾아줘.', '메모에 "선별 취소해줘"가 있는 행을 찾아줘.',
    '선별 취소할 기준은 아직 정하지 않았어.', '선별은 취소하지 말고 유지해줘.'])
def test_cancellation_text_as_data_or_negation_does_not_retire_filter(text):
    from agent_builder.conditions.revisions import cancellation_turns
    s = filtered(); s['messages'].append({'role': 'user', 'content': text})
    assert not cancellation_turns(s)


def test_nonstandard_field_cancellation_is_persisted_across_later_replies():
    s = filtered()
    s['condition_ledger']['slots']['filter.field']['value'] = '판매액'
    ledger = next_dedup(s, '판매액 조건은 취소하고 거래ID 중복만 첫 행을 남겨줘.')
    assert ledger['retired_operation_turns']['filter'] == 2
    assert ledger['operations'] == ['deduplicate']
    s['condition_ledger'] = ledger
    ledger = next_dedup(s, '거래ID 중복은 첫 행을 남겨줘.')
    assert ledger['operations'] == ['deduplicate']


@pytest.mark.parametrize('text', [
    '비용 선별 조건은 취소하고 비용 200만원 이상으로 다시 선별해줘.',
    '중복 제거는 취소하고 거래ID 중복은 마지막 행을 남겨줘.',
])
def test_same_turn_replacement_is_reviewed_instead_of_silently_omitted(text):
    s = filtered()
    ledger = next_dedup(s, text)
    assert ledger['decision'] == 'manual_review'
