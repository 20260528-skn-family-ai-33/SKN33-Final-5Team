import pytest

from agent_builder.conditions.condition_readiness import sort_request


ORDER_DATE = {'field': '주문일', 'direction': 'ascending', 'value_type': None}


def table_request(*turns):
    return {
        'messages': [{'role': 'user', 'content': text} for text in turns],
        'resources': [{'kind': 'table', 'columns': ['주문일', '금액', '정렬방식']}],
    }


@pytest.mark.parametrize('column', ['"주문일"', '‘주문일’', '`주문일`'])
def test_quoted_column_is_a_sort_key(column):
    request = sort_request(table_request(f'{column} 오름차순으로 정렬해서 CSV로 저장해줘'))
    assert request['sorts'] == [ORDER_DATE]


@pytest.mark.parametrize('withdrawal', ['정렬 없이', '정렬하지 않고', '정렬을 지우고'])
def test_withdrawal_applies_to_a_named_sort(withdrawal):
    state = table_request('주문일 오름차순으로 정렬해줘',
                          f'주문일 오름차순 {withdrawal} 저장해줘')
    assert sort_request(state)['kind'] == 'cancelled'


@pytest.mark.parametrize('filter_text', [
    '정렬방식이 오름차순과 같은 행만 골라',
    '정렬방식이 내림차순인 레코드만 골라',
    '정렬방식이 "주문일 오름차순"인 행만 골라',
])
def test_filter_direction_value_does_not_change_sorting(filter_text):
    assert sort_request(table_request(f'{filter_text} JSON으로 저장해줘'))['kind'] == 'none'
    state = table_request(f'{filter_text}, "주문일" 오름차순으로 정렬해줘')
    assert sort_request(state)['sorts'] == [ORDER_DATE]
