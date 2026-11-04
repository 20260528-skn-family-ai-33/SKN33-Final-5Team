import pytest
from tests.conditions.test_condition_readiness import checked, proposal, state
from agent_builder.conditions.condition_readiness import source_catalog, explicit_policies, literal_condition_issues


@pytest.mark.parametrize('tail',['정렬하지 말고 JSON으로 저장해.','빈 비용은 제외해.','정렬은 하지 않아.'])
def test_other_policy_negation_does_not_erase_explicit_duplicate_column(tail):
    s=state('비용 100만원 초과인 행만 남겨줘. 중복 거래ID는 마지막 행만 남기고 '+tail)
    s['resources']=[{'id':'sales','kind':'table','columns':['거래ID','비용']}]
    assert explicit_policies(s,source_catalog(s)[0])['duplicate.field']['value']=='거래ID'


@pytest.mark.parametrize('text',['중복 거래ID 말고 비용으로 판단해','중복 거래ID는 아니야','중복 거래ID는 제외'])
def test_negated_duplicate_column_is_not_selected(text):
    s=state(text);s['resources']=[{'id':'sales','kind':'table','columns':['거래ID','비용']}]
    assert 'duplicate.field' not in explicit_policies(s,source_catalog(s)[0])


@pytest.mark.parametrize('text', [
    '중복 거래ID, 아니 주문번호 기준으로 마지막 행만 남겨줘.',
    '중복 거래ID는 누락이 많아서 제외하고 주문번호 기준으로 판단해. 중복은 마지막 행만 남겨줘.',
])
@pytest.mark.parametrize('proposed_field', ['주문번호', '거래ID', None])
@pytest.mark.parametrize('resumed', [False, True])
def test_retracted_duplicate_key_cannot_override_the_proposal(text, proposed_field, resumed):
    s = state(text)
    s['resources'] = [{'id': 'sales', 'kind': 'table', 'columns': ['거래ID', '주문번호']}]
    if resumed:
        s['messages'][0]['content'] = '중복 거래ID는 마지막 행만 남겨줘.'
        s['condition_ledger'] = checked(s, proposal(s, {}, ['deduplicate']))
        assert s['condition_ledger']['slots']['duplicate.field']['value'] == '거래ID'
        s['messages'].append({'role': 'user', 'content': text})
    ledger = checked(s, proposal(s, {'duplicate.field': proposed_field}, ['deduplicate']))
    expected = '주문번호' if proposed_field == '주문번호' else None
    assert ledger['slots']['duplicate.field']['value'] == expected
    assert ledger['decision'] == ('ready' if expected else 'clarify')


def test_retracted_duplicate_key_invalidates_saved_ready_ledger_until_reconfirmed():
    s = state('중복 거래ID는 마지막 행만 남겨줘.')
    s['resources'] = [{'id': 'sales', 'kind': 'table', 'columns': ['거래ID', '주문번호']}]
    s['condition_required'] = True
    s['condition_ledger'] = checked(s, proposal(s, {}, ['deduplicate']))
    s['messages'].append({'role': 'user', 'content': '중복 거래ID, 아니 주문번호 기준으로 바꿔줘.'})
    assert any('duplicate.field' in issue for issue in literal_condition_issues(s))

    s['messages'].append({'role': 'user', 'content': '거래ID로 다시 할게.'})
    s['condition_ledger'] = checked(s, proposal(s, {'duplicate.field': '거래ID'}, ['deduplicate']))
    assert s['condition_ledger']['decision'] == 'ready'
    assert not literal_condition_issues(s)


@pytest.mark.parametrize('resume_before_cancellation', [False, True])
@pytest.mark.parametrize('new_field', ['주문번호', None])
def test_new_dedup_does_not_inherit_key_from_cancelled_operation(resume_before_cancellation, new_field):
    s = state('중복 거래ID는 마지막 행만 남겨.')
    s['resources'] = [{'id': 'sales', 'kind': 'table', 'columns': ['거래ID', '주문번호']}]
    s['condition_ledger'] = checked(s, proposal(s, {}, ['deduplicate']))
    s['messages'].append({'role': 'user', 'content': '중복 제거는 취소해줘.'})
    if not resume_before_cancellation:
        s['condition_ledger'] = checked(s, proposal(s, {}, []))
        assert 'duplicate.field' not in s['condition_ledger']['slots']
    s['messages'].append({'role': 'user', 'content': '이제 주문번호를 기준으로 중복은 첫 행만 남겨.'})
    ledger = checked(s, proposal(s, {'duplicate.field': new_field}, ['deduplicate']))
    assert ledger['slots']['duplicate.field']['value'] == new_field
    assert ledger['decision'] == ('ready' if new_field else 'clarify')


@pytest.mark.parametrize('text,expected', [
    ('중복 거래ID 말고 중복 주문번호 기준으로 마지막 행만 남겨.', '주문번호'),
    ('중복 거래ID를 쓰고, 주문번호는 기준에서 제외해. 중복은 마지막 행만 남겨.', '거래ID'),
])
def test_negation_belongs_to_the_named_key(text, expected):
    s = state(text)
    s['resources'] = [{'id': 'sales', 'kind': 'table', 'columns': ['거래ID', '주문번호']}]
    ledger = checked(s, proposal(s, {'duplicate.field': '거래ID'}, ['deduplicate']))
    assert ledger['decision'] == 'ready'
    assert ledger['slots']['duplicate.field']['value'] == expected
