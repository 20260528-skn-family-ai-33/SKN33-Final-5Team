"""PR #39 review: written comparisons are bound to the filter's field and the latest turn, and the
capability judgment checks quotes against the same task window it judges.

Each rule is also tried on the five axes the earlier measurement sets never covered: the same number
for another column, negation/exclusion, a later correction, several conditions, a different turn.
"""
import json
from decimal import Decimal
import pytest
from agent_builder.capabilities.capability_judgement import assess_capability
from agent_builder.conditions.condition_readiness import canonical_written_operators, written_comparison_issues
from tests.conditions.test_condition_readiness import Fake
from tests.conditions.test_written_comparisons import drafted, flt


def talk(*turns):
    roles = ['user', 'assistant'] * len(turns)
    return {'messages': [{'role': r, 'content': c} for r, c in zip(roles, turns)]}


def corrected(s, *filters):
    fixed, changes = canonical_written_operators(s, drafted(*filters))
    plan = (fixed or drafted(*filters))['execution_plan']['filters']
    return [f['operator'] for f in plan], changes


# Review 1: the same number for a different column must not be changed.
def test_same_number_for_another_column_is_not_changed():
    s = talk('qty가 30 이상인 행은 제외하고 price가 30 미만인 행만 저장해줘.')
    ops, changes = corrected(s, flt('lt', 30.0, field='qty'), flt('lt', 30.0, field='price'))
    assert ops == ['lt', 'lt'] and changes == []
    assert written_comparison_issues(s, drafted(flt('lt', 30.0, field='qty'), flt('lt', 30.0, field='price'))) == []


def test_field_named_elsewhere_in_the_clause_is_not_the_subject():
    s = talk('qty와 price를 곱한 amount 열을 만들고 amount가 30 이상, qty가 30 미만인 행만 저장해줘.')
    ops, changes = corrected(s, flt('gt', 30.0), flt('lt', 30.0, field='qty'))
    assert ops == ['gte', 'lt'] and changes == ['amount 30.0: 초과→이상']


def test_comparison_after_the_operator_names_its_field():
    s = talk('100만원 초과인 비용만 남기고, 수량은 100만 이상인 것만.')
    ops, _ = corrected(s, flt('gte', 1000000.0, field='비용'), flt('gte', 1000000.0, field='수량'))
    assert ops == ['gt', 'gte']


def test_field_with_column_wording_is_the_subject():
    s = talk('점수 열 기준으로 90점 이상인 학생만 CSV로 저장해줘.')
    assert corrected(s, flt('gt', 90.0, field='점수'))[0] == ['gte']


def test_unbound_number_is_left_to_the_ledger():
    s = talk('30 이상인 행만 저장해줘.')
    assert corrected(s, flt('gt', 30.0))[0] == ['gt']
    assert written_comparison_issues(s, drafted(flt('gt', 30.0))) == []


def test_excluded_clause_is_never_used_for_correction():
    s = talk('amount가 30 이상인 행은 제외해줘.')
    assert corrected(s, flt('lt', 30.0))[0] == ['lt']
    assert written_comparison_issues(s, drafted(flt('lt', 30.0))) == []


def test_explicit_conflict_for_the_same_field_is_still_blocked():
    s = talk('amount가 30 이상이거나 30 미만인 행')
    assert canonical_written_operators(s, drafted(flt('gt', 30.0))) == (None, [])
    assert written_comparison_issues(s, drafted(flt('gt', 30.0)))


# Review 2: a later turn replaces the earlier comparison for that field.
def test_later_correction_replaces_the_earlier_operator():
    s = talk('amount가 30 이상인 행만 저장해줘.', '저장할까요?', '아니, amount가 30 초과로 바꿔줘.')
    assert corrected(s, flt('gte', 30.0))[0] == ['gt']
    assert written_comparison_issues(s, drafted(flt('gte', 30.0)))
    assert written_comparison_issues(s, drafted(flt('gt', 30.0))) == []


def test_later_correction_keeps_other_fields():
    s = talk('amount가 30 이상이고 price가 10 이하인 행만 저장해줘.', '저장할까요?', '아니, amount는 30 초과로.')
    ops, _ = corrected(s, flt('gte', 30.0), flt('lt', 10.0, field='price'))
    assert ops == ['gt', 'lte']


def test_replaced_threshold_left_in_the_draft_is_reported():
    s = talk('amount가 30 이상인 행만 저장해줘.', '저장할까요?', '아니, amount가 40 이상으로.')
    issues = written_comparison_issues(s, drafted(flt('gte', 30.0)))
    assert issues and '바뀐 이전 조건' in issues[0]
    assert written_comparison_issues(s, drafted(flt('gte', 40.0))) == []


def test_original_a3_case_still_corrected():
    s = talk('qty와 price를 곱한 amount 열을 추가하고 amount가 30 이상인 행만 남겨서 amount가 큰 순서로 정렬한 뒤 JSON으로 저장해줘.')
    assert corrected(s, flt('gt', 30.0)) == (['gte'], ['amount 30.0: 초과→이상'])


# Review 3: the quote list and quote check follow the task window.
RECEIPT = '올려준 영수증 이미지 파일 내용을 엑셀파일로 정리해줘'
SALES = '10월 26일부터 11월 1일까지 지점 매출을 그 전주와 비교해서 회의용 표로 만들어줘.'


def answer(quote):
    return {'decision': 'existing_tools', 'tool_ids': ['read_table'], 'missing_conditions': [], 'missing_actions': [],
            'reason': '검증용', 'user_quotes': [quote]}


def quote_enum(transport):
    return transport.schema['anyOf'][0]['properties']['user_quotes']['items']['enum']


def test_new_task_rejects_quotes_from_the_earlier_request():
    s = talk(RECEIPT, '분석할 파일을 먼저 올려 주세요.', SALES)
    transport = Fake([answer(RECEIPT)])
    with pytest.raises(ValueError, match='원문 근거'):
        assess_capability(s, transport)
    assert quote_enum(transport) == [SALES]


def test_new_task_accepts_its_own_quote_and_sends_one_catalog():
    s = talk(RECEIPT, '분석할 파일을 먼저 올려 주세요.', SALES)
    class Recording(Fake):
        def __init__(self, packets): super().__init__(packets); self.messages = []
        def invoke(self, messages): self.messages.append(messages); return super().invoke(messages)
    transport = Recording([answer(SALES)])
    assert assess_capability(s, transport)['decision'] == 'existing_tools'
    payload = json.loads(transport.messages[0][1]['content'])
    assert payload['user_quote_catalog'] == quote_enum(transport) == [SALES]


def test_answers_to_questions_keep_the_whole_conversation():
    s = {**talk('비용 큰 것들만 골라서 파일로 만들어줘.', '기준 값은 얼마인가요?', '100만원 초과로 해줘. CSV로 저장해.'),
         'pending_questions': [{'id': 'q', 'key': 'filter.value'}]}
    transport = Fake([answer('비용 큰 것들만 골라서 파일로 만들어줘.')])
    assert assess_capability(s, transport)['decision'] == 'existing_tools'
    assert len(quote_enum(transport)) == 2


# Second review (cde7e55): two axes at once. Each case checks both that the right draft is kept
# and that the wrong draft (old or dropped condition) is still stopped.
def blocked(s, *filters):
    return bool(written_comparison_issues(s, drafted(*filters)))


def test_include_changed_to_exclude_takes_the_old_condition_back():
    s = talk('amount가 30 이상인 행만 남겨 JSON으로 저장해줘.', '저장할까요?', '아니, amount가 30 이상인 행은 제외하고 저장해줘.')
    assert corrected(s, flt('lt', 30.0)) == (['lt'], [])
    assert not blocked(s, flt('lt', 30.0))
    assert corrected(s, flt('gte', 30.0)) == (['gte'], []) and blocked(s, flt('gte', 30.0))


def test_audit_keeps_the_right_draft_after_include_becomes_exclude():
    from agent_builder.conditions.condition_check import audit_conditions
    s = {**talk('amount가 30 이상인 행만 남겨 JSON으로 저장해줘.', '저장할까요?', '아니, amount가 30 이상인 행은 제외하고 저장해줘.'),
         'draft': drafted(flt('lt', 30.0)), 'trace': []}
    assert audit_conditions(s) == {}


def test_dropped_range_in_the_same_turn_is_not_kept():
    s = talk('amount가 30 이상인 행은 제외해줘.')
    assert blocked(s, flt('gte', 30.0)) and not blocked(s, flt('lt', 30.0))


@pytest.mark.parametrize('text,fields,right,wrong,fixed_in_code', [
    # amount's kept range is the opposite of a dropped one: not guessed, so the wrong draft is stopped.
    ('total_amount가 30 이상인 행만 남기고, amount가 30 이상인 행은 제외해서 JSON으로 저장해줘.',
     ['total_amount', 'amount'], ['gte', 'lt'], ['gte', 'gte'], False),
    ('30 이상인 amount_total만 남기고 amount는 30 미만만.', ['amount_total', 'amount'], ['gte', 'lt'], ['gte', 'gte'], True),
    ('총매출이 30 이상인 행만 남기고 매출이 30 미만인 행만.', ['총매출', '매출'], ['gte', 'lt'], ['gte', 'gte'], True),
])
def test_partly_overlapping_column_names_are_different_columns(text, fields, right, wrong, fixed_in_code):
    s = talk(text)
    good = [flt(o, 30.0, field=f) for f, o in zip(fields, right)]
    bad = [flt(o, 30.0, field=f) for f, o in zip(fields, wrong)]
    assert corrected(s, *good) == (right, []) and not blocked(s, *good)
    if fixed_in_code:
        assert corrected(s, *bad)[0] == right
    else:
        assert corrected(s, *bad)[0] == wrong and blocked(s, *bad)


def test_column_name_inside_a_longer_word_is_not_the_subject():
    assert not blocked(talk('매출액이 30 이상인 행만 저장해줘.'), flt('gt', 30.0, field='매출'))
    assert blocked(talk('매출은 30 이상인 행만 저장해줘.'), flt('gt', 30.0, field='매출'))


def test_changing_the_upper_bound_keeps_the_lower_bound():
    s = talk('amount가 30 이상이고 amount가 100 이하인 행만 남겨 JSON으로 저장해줘.', '저장할까요?',
             '하한은 그대로 두고 amount가 200 이하인 행까지 남겨줘.')
    assert not blocked(s, flt('gte', 30.0), flt('lte', 200.0))
    assert blocked(s, flt('gte', 30.0), flt('lte', 100.0))


def test_changing_the_lower_bound_keeps_the_upper_bound():
    s = talk('amount가 30 이상이고 amount가 100 이하인 행만.', '저장할까요?', 'amount가 50 이상으로 바꿔줘.')
    assert not blocked(s, flt('gte', 50.0), flt('lte', 100.0))
    assert blocked(s, flt('gte', 30.0), flt('lte', 100.0))


def test_amount_with_eok_unit_is_read_as_one_number():
    from agent_builder.conditions.condition_readiness import written_comparisons
    assert written_comparisons('비용이 3억원 이상인 행만') == [(Decimal(300000000), 'gte')]
    s = talk('비용이 3억원 이상인 행만 저장해줘.')
    assert corrected(s, flt('gt', 300000000.0, field='비용'))[0] == ['gte']


def test_header_unit_is_applied_like_the_ledger():
    field = '매출(백만원)'
    s = talk(f'{field}이 3억원 이상인 행만 저장해줘.')
    assert corrected(s, flt('gt', 300.0, field=field))[0] == ['gte']
    assert not blocked(s, flt('gte', 300.0, field=field))
