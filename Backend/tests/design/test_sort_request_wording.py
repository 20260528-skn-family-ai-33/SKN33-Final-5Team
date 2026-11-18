import pytest

from agent_builder.conditions.condition_readiness import sort_request


ORDER_DATE = {'field': '주문일', 'direction': 'ascending', 'value_type': None}
AMOUNT_DESCENDING = {'field': '금액', 'direction': 'descending', 'value_type': 'number'}
PRE_SORT = {**ORDER_DATE, 'direction': 'descending'}
SORT_FIRST = '주문일 내림차순으로 먼저 정렬한 뒤 주문번호가 같은 행은 첫 행만 남겨줘.'
SPLIT_SORT = SORT_FIRST + ' 결과는 금액 오름차순으로 정렬해줘.'


def table_request(*turns, columns=('주문일', '금액', '정렬방식')):
    return {
        'messages': [{'role': 'user', 'content': text} for text in turns],
        'resources': [{'kind': 'table', 'columns': columns}],
    }


@pytest.mark.parametrize('column', ['"주문일"', '‘주문일’', '`주문일`'])
def test_quoted_column_is_a_sort_key(column):
    request = sort_request(table_request(f'{column} 오름차순으로 정렬해서 CSV로 저장해줘'))
    assert request['sorts'] == [ORDER_DATE]


def test_quoted_sort_condition_before_a_sorted_result():
    state = table_request('"주문일 오름차순"으로 정렬된 결과를 CSV로 저장해줘')
    assert sort_request(state)['sorts'] == [ORDER_DATE]


def test_quoted_sort_key_edit_keeps_the_previous_direction():
    state = table_request('주문일 내림차순으로 정렬해줘', '정렬 기준은 "금액"이야')
    assert sort_request(state)['sorts'] == [
        {'field': '금액', 'direction': 'descending', 'value_type': None},
    ]


@pytest.mark.parametrize('withdrawal', ['정렬 없이', '정렬하지 않고', '정렬을 지우고'])
def test_withdrawal_applies_to_a_named_sort(withdrawal):
    state = table_request('주문일 오름차순으로 정렬해줘',
                          f'주문일 오름차순 {withdrawal} 저장해줘')
    assert sort_request(state)['kind'] == 'cancelled'


@pytest.mark.parametrize('key', ['주문일 오름차순', '주문일'])
@pytest.mark.parametrize('particle', ['을', '만'])
def test_withdrawing_one_sort_preserves_other_keys(key, particle):
    state = table_request('주문일 오름차순, 금액 숫자 내림차순으로 정렬해줘',
                          f'{key} 정렬{particle} 빼고 금액 정렬은 그대로 둬')
    assert sort_request(state)['sorts'] == [AMOUNT_DESCENDING]


def test_keeping_only_sorting_does_not_withdraw_it():
    state = table_request('주문일 오름차순 정렬만 해줘')
    assert sort_request(state)['sorts'] == [ORDER_DATE]


@pytest.mark.parametrize('withdrawal', ['정렬을 빼줘', '정렬만 빼줘', '주문일 오름차순 정렬을 전부 빼줘'])
def test_full_withdrawal_clears_multiple_sort_keys(withdrawal):
    state = table_request('주문일 오름차순, 금액 숫자 내림차순으로 정렬해줘', withdrawal)
    assert sort_request(state)['kind'] == 'cancelled'


@pytest.mark.parametrize('filter_text', [
    '정렬방식이 오름차순과 같은 행만 골라',
    '정렬방식이 내림차순인 레코드만 골라',
    '정렬방식이 "주문일 오름차순"인 행만 골라',
    '정렬방식이 오름차순인 것만 남겨줘',
    '정렬방식이 오름차순인 건만 남겨줘',
    '정렬방식이 오름차순인 주문만 남겨줘',
    '정렬방식이 오름차순과 같은 주문 건만 남겨줘',
    '정렬방식이 내림차순과 같은 주문건만 남겨줘',
    '정렬이 오름차순과 같은 행만 골라',
    '정렬방식이 내림차순이 아닌 주문만 골라',
    '정렬방식이 오름차순인 데이터만 남기고',
    '정렬방식이 내림차순과 같은 데이터만 골라',
    '정렬방식이 내림차순이 아닌 데이터만 골라',
])
def test_filter_direction_value_does_not_change_sorting(filter_text):
    columns = ['주문일', '금액', '정렬방식', '정렬']
    assert sort_request(table_request(f'{filter_text} JSON으로 저장해줘', columns=columns))['kind'] == 'none'
    state = table_request(f'{filter_text}, "주문일" 오름차순으로 정렬해줘', columns=columns)
    assert sort_request(state)['sorts'] == [ORDER_DATE]


@pytest.mark.parametrize('text', ['주문일이 오름차순이 되게 정렬해줘', '주문일이 오름차순인 상태로 정렬해줘'])
def test_subject_marked_column_with_a_sort_verb_is_still_a_sort(text):
    assert sort_request(table_request(text))['sorts'] == [ORDER_DATE]


@pytest.mark.parametrize('filter_value', ['오름차순', '내림차순'])
@pytest.mark.parametrize('selector', ['데이터만', '데이터 만', '자료만', '자료 만', '행만', '레코드만'])
def test_filter_value_before_an_amount_sort_is_not_a_sort_key(filter_value, selector):
    text = f'정렬방식이 {filter_value}인 {selector} 남기고 금액 내림차순 정렬'
    assert sort_request(table_request(text))['sorts'] == [
        {'field': '금액', 'direction': 'descending', 'value_type': None},
    ]


@pytest.mark.parametrize(('text', 'expected'), [
    ('정렬방식이 오름차순인 데이터만 남기고 금액 내림차순으로 정렬해줘',
     {'field': '금액', 'direction': 'descending', 'value_type': None}),
    ('정렬방식이 내림차순인 자료만 골라서 주문일 오름차순으로 정렬해줘', ORDER_DATE),
])
def test_review_filter_and_sort_requests_verbatim(text, expected):
    assert sort_request(table_request(text))['sorts'] == [expected]


@pytest.mark.parametrize('text', [
    '자료.csv에서 주문일 오름차순으로 정렬해줘',
    '내림차순인자료.csv에서 주문일 오름차순으로 정렬해줘',
    '첨부 자료를 주문일 오름차순으로 정렬해줘',
    '자료 열이 "내림차순인 자료만"인 행만 골라 주문일 오름차순으로 정렬해줘',
    '주문일이 오름차순인 상태로 자료를 정렬해줘',
])
def test_resource_mentions_do_not_hide_an_actual_sort(text):
    assert sort_request(table_request(text, columns=['주문일', '자료']))['sorts'] == [ORDER_DATE]


def test_same_column_can_sort_differently_before_and_after_deduplication():
    state = table_request(SORT_FIRST + ' 결과는 주문일 오름차순으로 정렬해줘.')
    request = sort_request(state)
    assert request['pre_sorts'] == [PRE_SORT]
    assert request['sorts'] == [ORDER_DATE]


def test_repeating_a_key_inside_one_sort_keeps_its_first_criterion():
    state = table_request('주문일 오름차순, 금액 숫자 내림차순, 주문일 내림차순으로 정렬해줘.')
    assert sort_request(state)['sorts'] == [ORDER_DATE, AMOUNT_DESCENDING]


@pytest.mark.parametrize('initial', [SORT_FIRST, SPLIT_SORT])
def test_result_only_edits_do_not_replace_the_presort(initial):
    result = sort_request(table_request(initial, '결과는 금액 숫자 내림차순으로 바꿔줘.'))
    assert result['pre_sorts'] == [PRE_SORT]
    assert result['sorts'] == [AMOUNT_DESCENDING]


@pytest.mark.parametrize(('hint', 'expected'), [
    ('결과 정렬은 오름차순으로 바꿔줘.', ORDER_DATE),
    ('결과 정렬 기준은 금액이야.', {'field': '금액', 'direction': 'descending', 'value_type': None}),
])
def test_result_only_hint_keeps_an_existing_presort(hint, expected):
    result = sort_request(table_request(SORT_FIRST, hint))
    assert result['pre_sorts'] == [PRE_SORT]
    assert result['sorts'] == [expected]


@pytest.mark.parametrize('initial', [SORT_FIRST, SPLIT_SORT])
def test_result_only_cancellation_keeps_the_presort_and_allows_a_later_result_sort(initial):
    turns = (initial, '결과 정렬만 빼줘.')
    result = sort_request(table_request(*turns))
    assert result['result_sort_cancelled']
    assert result['pre_sorts'] == result['sorts'] == [PRE_SORT]
    result = sort_request(table_request(*turns, '결과는 금액 숫자 내림차순으로 정렬해줘.'))
    assert not result.get('result_sort_cancelled')
    assert result['pre_sorts'] == [PRE_SORT]
    assert result['sorts'] == [AMOUNT_DESCENDING]


@pytest.mark.parametrize('follow_up', ['정렬은 모두 빼줘.', '중복 제거 먼저 해줘.', '새로운 작업: 금액순으로 정렬해줘.'])
def test_withdrawing_or_reordering_all_steps_does_not_carry_the_presort(follow_up):
    assert 'pre_sorts' not in sort_request(table_request(SPLIT_SORT, follow_up))


@pytest.mark.parametrize('separator', ['. 아니, ', ', 아니, ', '; '])
def test_later_directive_in_the_same_turn_withdraws_a_sort(separator):
    text = '주문일 오름차순으로 정렬해줘' + separator + '정렬은 빼고 원본 순서 그대로 저장해줘.'
    assert sort_request(table_request(text))['kind'] == 'cancelled'


@pytest.mark.parametrize('question', [
    '금액 내림차순으로 정렬해?',
    '금액 내림차순으로 정렬할까요？',
    '금액 내림차순으로 정렬하는 방법을 알려줘.',
])
def test_sort_inquiry_does_not_change_a_confirmed_stage(question):
    result = sort_request(table_request(SORT_FIRST, question))
    assert result['sorts'] == [PRE_SORT]
    assert 'pre_sorts' not in result


def test_a_correction_starts_a_new_sort_command_inside_the_turn():
    text = '주문일 오름차순으로 정렬해줘, 아니 주문일 내림차순으로 정렬해줘.'
    assert sort_request(table_request(text))['sorts'] == [PRE_SORT]


def test_withdrawing_a_key_in_the_same_turn_keeps_other_keys():
    text = '주문일 오름차순, 금액 숫자 내림차순으로 정렬해줘. 주문일 정렬만 빼줘.'
    assert sort_request(table_request(text))['sorts'] == [AMOUNT_DESCENDING]


def test_presort_only_revision_keeps_an_independent_result_sort():
    result = sort_request(table_request(SPLIT_SORT, '중복 제거 전에는 금액 숫자 내림차순으로 정렬해줘.'))
    assert result['pre_sorts'] == [AMOUNT_DESCENDING]
    assert result['sorts'] == [{'field': '금액', 'direction': 'ascending', 'value_type': None}]


def test_result_scope_applies_to_every_key_in_one_command():
    result = sort_request(table_request(SORT_FIRST, '결과는 주문일 오름차순, 금액 숫자 내림차순으로 정렬해줘.'))
    assert result['pre_sorts'] == [PRE_SORT]
    assert result['sorts'] == [ORDER_DATE, AMOUNT_DESCENDING]


def test_partially_known_presort_keeps_its_own_hint():
    text = ('먼저 최신순으로 정렬한 뒤 주문번호가 같은 행은 첫 행만 남기고, '
            '결과는 금액 숫자 내림차순으로 정렬해줘.')
    result = sort_request(table_request(text))
    assert result['pre_sort_hint'] == {'fields': [], 'direction': 'descending'}
    assert result['sorts'] == [AMOUNT_DESCENDING]


def test_partially_known_result_sort_keeps_its_own_hint():
    text = ('먼저 주문일 내림차순으로 정렬한 뒤 주문번호가 같은 행은 첫 행만 남기고, '
            '결과는 금액 기준으로 정렬해줘.')
    result = sort_request(table_request(text))
    assert result['pre_sorts'] == [PRE_SORT]
    assert result['kind'] == 'unparsed'
    assert result['fields'] == ['금액']


def test_separate_hints_for_one_stage_become_one_exact_key():
    result = sort_request(table_request('최신순으로 정렬해줘.', '정렬 기준은 주문일이야.'))
    assert result['sorts'] == [PRE_SORT]


@pytest.mark.parametrize('data_clause', [
    '결과에는 정렬 열만 표시해줘',
    '정렬이 5 이상인 행만 골라줘',
    '정렬방식이 정렬과 같은 행만 남겨줘',
    '예시 문구는 주문일 내림차순으로 정렬해줘',
])
def test_non_directive_sort_words_do_not_become_sort_operations(data_clause):
    columns = ['주문일', '금액', '정렬', '정렬방식']
    assert sort_request(table_request(data_clause, columns=columns))['kind'] == 'none'
    assert sort_request(table_request(data_clause, '주문일 오름차순으로 정렬해줘', columns=columns))['sorts'] == [
        ORDER_DATE,
    ]
