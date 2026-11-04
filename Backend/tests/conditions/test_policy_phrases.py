"""Missing/duplicate policy wording users actually write (빈칸·빈 셀·결측치 제거·남겨두기,
중복 항목 제거·첫 번째만 남기고 삭제). Live baseline 2026-11-02 (qwen3.5:4b, 82 phrases):
39/82 correct and 11 wrong confirmations; with these rules 80/82 and 0 wrong confirmations."""
import pytest
from agent_builder.conditions.condition_readiness import explicit_policies, policy_clauses, source_catalog
from agent_builder.capabilities.capability_judgement import assess_capability
from tests.conditions.test_condition_readiness import state, proposal, checked, Fake

COLUMNS = ['거래ID', '비용', '상태']
TABLE = {'id': 'r1', 'kind': 'table', 'name': '거래.csv', 'columns': COLUMNS, 'row_count': 7,
         'profile': {c: {'types': ['string']} for c in COLUMNS}}

MISSING = [
    ('빈 비용은 제외해', 'exclude'), ('빈 비용은 빼줘', 'exclude'), ('빈 비용은 삭제해', 'exclude'), ('빈 비용은 지워줘', 'exclude'),
    ('결측치는 제거해', 'exclude'), ('비용이 비어 있는 행은 빼줘', 'exclude'), ('비용이 빈칸인 행은 지워줘', 'exclude'),
    ('비용 값이 없는 행은 제외해', 'exclude'), ('비용이 누락된 행은 빼', 'exclude'), ('빈 셀이 있는 행은 지워', 'exclude'),
    ('빈 비용은 그대로 남겨', 'keep'), ('빈 값은 그대로 둬', 'keep'), ('빈 비용도 포함해', 'keep'), ('결측치는 남겨둬', 'keep'),
    ('빈 비용은 지우지 말고 그대로 둬', 'keep'), ('빈 비용은 빼지 말고 그대로 둬', 'keep'), ('빈칸은 그대로 두세요', 'keep'),
    ('빈 비용이 있으면 오류로 처리해', 'error'), ('빈 값이 있으면 에러로 처리해', 'error'), ('빈 비용이 있으면 멈춰', 'error'),
    ('빈 값이 하나라도 있으면 실행을 멈춰줘', 'error'), ('빈칸이 있으면 실패로 처리해', 'error'), ('결측치가 있으면 오류로 멈춰', 'error'),
    ('빈 값이 포함된 행은 제외해줘', 'exclude'), ('빈 값은 제외해둬', 'exclude'),
    ('빈 값은 빼둬', 'exclude'), ('빈 값은 제외하고 나머지는 유지해', 'exclude'),
]
DUPLICATE = [
    ('중복 거래ID는 첫 행만 남겨', 'first'), ('중복 거래ID는 첫 번째만 남기고 지워', 'first'), ('같은 거래ID는 처음 것만 남겨', 'first'),
    ('거래ID가 같은 행은 맨 위 행만 남겨', 'first'), ('중복 거래ID는 먼저 나온 행만 남겨줘', 'first'), ('거래ID가 겹치면 첫 행만 남겨', 'first'),
    ('같은 거래ID는 마지막 것만 남겨', 'last'), ('거래ID가 같은 행은 맨 아래 행만 남겨', 'last'), ('중복 거래ID는 나중에 나온 행만 남겨줘', 'last'),
    ('중복 거래ID는 유지해', 'keep'), ('중복 거래ID도 모두 그대로 유지해', 'keep'), ('중복 거래ID는 지우지 마', 'keep'), ('중복된 행도 다 남겨', 'keep'),
    ('중복 거래ID가 있으면 오류로 멈춰', 'error'), ('같은 거래ID가 있으면 중단해', 'error'), ('거래ID가 겹치면 실패로 처리해', 'error'),
]
# Negated, vague, fill/merge or other-criterion wording is never confirmed; it becomes a question.
UNRESOLVED = [
    ('missing.policy', '빈 비용은 제외하지 마'), ('missing.policy', '빈 값은 오류로 하지 마'), ('missing.policy', '빈 값은 오류가 아니야'),
    ('missing.policy', '빈 값 처리는 알아서 해'), ('missing.policy', '빈 값은 나중에 정할게'), ('missing.policy', '빈 비용은 0으로 채워줘'),
    ('missing.policy', '빈 값은 평균으로 채워'), ('missing.policy', '빈 값은 무시해'), ('missing.policy', '빈 값은 상관없어'),
    ('duplicate.policy', '중복은 알아서 정리해'), ('duplicate.policy', '중복 거래ID는 최신 것만 남겨'), ('duplicate.policy', '중복 처리는 나중에 정할게'),
    ('duplicate.policy', '중복 거래ID는 합쳐줘'), ('duplicate.policy', '중복 거래ID는 첫 행만 남기지 마'), ('duplicate.policy', '중복은 상관없어'),
    ('duplicate.policy', '중복 거래ID는 금액이 큰 행만 남겨'),
    ('duplicate.policy', '중복 거래ID는 첫 행을 삭제해'),
    ('duplicate.policy', '중복 거래ID는 마지막 행을 지워줘'),
    ('duplicate.policy', '중복 거래ID는 첫 행만 남기고 오류로 중단해'),
]


def literal(text):
    s = {**state(text), 'resources': [TABLE]}
    return explicit_policies(s, source_catalog(s)[0])


@pytest.mark.parametrize('phrase,value', MISSING)
def test_missing_policy_wording(phrase, value):
    slot = literal(f'비용이 100만원 초과인 행만 남겨줘. {phrase}. JSON으로 저장해.')['missing.policy']
    assert (slot['status'], slot['value']) == ('specified', value)


@pytest.mark.parametrize('phrase,value', DUPLICATE)
def test_duplicate_policy_wording(phrase, value):
    slot = literal(f'비용이 100만원 초과인 행만 남겨줘. 빈 비용은 제외해. {phrase}. JSON으로 저장해.')['duplicate.policy']
    assert (slot['status'], slot['value']) == ('specified', value)


@pytest.mark.parametrize('key,phrase', UNRESOLVED)
def test_negated_or_vague_policy_is_a_question(key, phrase):
    slot = literal(f'비용이 100만원 초과인 행만 남겨줘. {phrase}. JSON으로 저장해.').get(key)
    assert slot is None or slot['status'] == 'conflict'


def test_one_sentence_with_both_policies_keeps_each_clause():
    assert {(k, v) for k, v, _ in policy_clauses('빈 값은 제외하고 중복 거래ID는 마지막 행만 남겨.', COLUMNS)} == {
        ('missing.policy', 'exclude'), ('duplicate.policy', 'last')}
    assert literal('빈 값은 제외, 빈 값은 오류.')['missing.policy']['status'] == 'conflict'


@pytest.mark.parametrize('phrase', ['중복 거래ID가 있으면 오류로 멈춰', '중복 거래ID도 모두 유지해', '같은 거래ID는 처음 것만 남겨',
                                    '거래ID가 같은 행은 마지막 것만 남겨', '거래ID가 겹치면 첫 행만 남겨'])
def test_duplicate_column_is_read_with_particles_and_synonyms(phrase):
    assert literal(f'빈 비용은 제외해. {phrase}.')['duplicate.field']['value'] == '거래ID'


@pytest.mark.parametrize('phrase', ['정렬은 하지 마', '정렬은 하지 말고', '정렬 없이'])
def test_no_sort_wording(phrase):
    assert literal(f'비용이 100만원 초과인 행만 남겨줘. {phrase}. JSON으로 저장해.')['sort.policy']['value'] == 'none'


@pytest.mark.parametrize('reply', ['슬랙과 중복은 취소', '중복 제거와 슬랙 전송을 취소해줘.'])
def test_withdrawn_dedup_is_not_reapplied_from_history_or_checkpoint(reply):
    from agent_builder.design.condition_templates import compile_conditions

    s = {**state('비용 100만원 초과인 행만 JSON으로 저장해. 중복 거래ID는 마지막 행만 남겨. 슬랙으로 보내줘.'),
         'resources': [TABLE]}
    raw = proposal(s, {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000'})
    s['condition_ledger'] = checked(s, raw)
    assert 'deduplicate' in s['condition_ledger']['operations']
    s['messages'].append({'role': 'user', 'content': reply})
    for followup in (None, '네'):
        if followup:
            s['messages'].append({'role': 'user', 'content': followup})
        ledger = checked(s, raw)
        assert ledger['decision'] == 'ready' and ledger['operations'] == ['filter']
        assert 'duplicate.field' not in ledger['slots']
        s['condition_ledger'] = ledger
        plan = compile_conditions(s)['draft']['execution_plan']
        assert plan['duplicate_policy'] == 'keep' and plan['filters'][0]['field'] == '비용'

    s['messages'].append({'role': 'user', 'content': '중복 거래ID는 다시 첫 행만 남겨.'})
    assert checked(s, raw)['slots']['duplicate.policy']['value'] == 'first'


@pytest.mark.parametrize('reply', ['중복 제거는 취소하지 마', '중복 취소 건수를 알려줘'])
def test_cancel_word_alone_does_not_change_dedup_policy(reply):
    s = state('중복 거래ID는 마지막 행만 남겨.')
    s['messages'].append({'role': 'user', 'content': reply})
    assert explicit_policies(s, source_catalog(s)[0])['duplicate.policy']['value'] == 'last'


def test_order_or_error_duplicate_policy_without_a_column_asks_for_the_column():
    s = state('비용이 100만원 초과인 행만 남겨줘. 빈 값은 제외해. 중복은 오류로 처리해. JSON으로 저장해.')
    ledger = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000',
                                     'missing.policy': 'exclude', 'duplicate.policy': 'error', 'output.format': 'json'}))
    assert ledger['decision'] == 'clarify' and 'duplicate.field' in ledger['unresolved']


def test_supported_operation_name_is_not_an_unsupported_action():
    s = state('비용이 100만원 초과인 행만 남겨줘. 빈 값은 제외해. 중복은 유지해. JSON으로 저장해.')
    raw = proposal(s, {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000',
                       'missing.policy': 'exclude', 'duplicate.policy': 'keep', 'output.format': 'json'})
    ledger = checked(s, {**raw, 'unsupported_actions': ['deduplicate', 'sort']})
    assert ledger['decision'] == 'ready' and ledger['unsupported_actions'] == []
    ledger = checked(s, {**raw, 'unsupported_actions': ['외부 이메일 발송']})
    assert ledger['decision'] == 'unsupported'


def capability(text, decision, items):
    s = {**state(text), 'resources': [TABLE]}
    key = 'missing_actions' if decision == 'missing_capability' else 'missing_conditions'
    packet = {'decision': decision, 'tool_ids': ['read_table', 'filter_sort'], 'missing_conditions': [], 'missing_actions': [],
              'reason': '검증용', 'user_quotes': [text], key: items}
    return assess_capability(s, Fake([packet, packet]))


DUP_ERROR = '비용이 100만원 초과인 행만 남겨줘. 빈 비용은 제외해. 중복 거래ID가 있으면 오류로 멈춰. JSON으로 저장해.'


@pytest.mark.parametrize('decision,items', [('missing_capability', ['중복 거래ID 오류 처리']),
                                            ('missing_capability', ['중복 거래ID 검출 및 오류 처리', '빈 비용 오류 처리']),
                                            ('clarify', ['중복 거래ID 처리 정책 (오류로 중단)'])])
def test_registered_policy_refusal_is_corrected_by_code(decision, items):
    result = capability(DUP_ERROR, decision, items)
    assert result['decision'] == 'existing_tools' and 'filter_sort' in result['tool_ids']
    assert result['model_decision'] == decision and result['code_registered_policy']


@pytest.mark.parametrize('text,decision,items', [
    (DUP_ERROR, 'missing_capability', ['중복 거래ID 오류 처리', '외부 이메일 발송']),       # a real gap stays
    (DUP_ERROR, 'clarify', ['중복 기준 상충 조건 해결 (첫 행 vs 마지막 행)']),               # a conflict is the user's
    ('비용이 100만원 초과인 행만 남겨줘. JSON으로 저장해.', 'missing_capability', ['중복 오류 처리']),  # policy not stated
    ('비용 100만원 초과만 골라줘. 빈 비용은 0으로 채워줘.', 'missing_capability', ['빈 비용 0으로 채우기'])])  # fill is not a policy
def test_other_refusals_are_unchanged(text, decision, items):
    assert capability(text, decision, items)['decision'] == decision


@pytest.mark.parametrize('order', ['첫', '마지막'])
def test_deleting_an_ordered_duplicate_is_not_confirmed_as_retaining_it(order):
    text = f'비용이 100만원 초과인 행만 골라줘. 빈 값은 제외해. 중복 거래ID는 {order} 행을 삭제해. CSV로 저장해.'
    s = {**state(text), 'resources': [TABLE]}
    ledger = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000',
                                    'missing.policy': 'exclude', 'duplicate.policy': None, 'output.format': 'csv'}))
    assert ledger['decision'] == 'clarify'
    assert ledger['slots']['duplicate.policy']['status'] == 'conflict'


def test_comparison_does_not_invent_duplicate_conditions():
    text = '금액이 같거나 큰 행만 골라줘. 기준은 100만원 이상이야. 빈 값은 제외해. CSV로 저장해.'
    s = {**state(text), 'resources': [{**TABLE, 'columns': ['금액', '거래ID']}]}
    ledger = checked(s, proposal(s, {'filter.field': '금액', 'filter.operator': 'gte', 'filter.value': '1000000',
                                    'missing.policy': 'exclude', 'output.format': 'csv'}))
    assert ledger['decision'] == 'ready'
    assert not any(k.startswith('duplicate.') for k in ledger['slots'])


@pytest.mark.parametrize('wording', ['빈 값은 절대 안 지워', '빈 값은 안 빼', '빈 값은 삭제 안 해',
                                    '빈 값은 안 유지해', '빈 값은 오류로 안 해'])
def test_negated_policy_is_not_forced_over_model_or_confirmed(wording):
    s = {**state('비용이 100만원 초과인 행을 JSON으로 저장해줘. ' + wording), 'resources': [TABLE]}
    ledger = checked(s, proposal(s, {'filter.field': '비용', 'filter.operator': 'gt',
                                    'filter.value': '1000000', 'missing.policy': 'keep', 'output.format': 'json'}))
    assert ledger['decision'] == 'clarify'
    assert ledger['slots']['missing.policy']['status'] == 'conflict'
