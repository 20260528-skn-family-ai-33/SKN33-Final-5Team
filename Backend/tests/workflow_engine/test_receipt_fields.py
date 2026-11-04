import pytest

from workflow_engine.executors.document import extract_information
from workflow_engine.executors.document.receipt_fields import recognized_facts
from workflow_engine.executors.schemas import Facts


def receipts(text, identifier='page1'):
    return [dict(source_id=identifier, file_id='f', file_name='scan.jpg', text=text, page=1)]


def test_transaction_header_and_total_have_literal_evidence():
    source = receipts(
        '쿠폰 만료일 2010-04-24\n'
        '2010-03-24 21:17 POS 번호: 1016\n'
        '상호명: 마트 탄현점\n'
        '상품 A 2,600\n상품 B 25,000\n부가세 과세 물품가액 25,091\n'
        '상품가격에 이미 포함된 부가세(2,509)\n합  계     27,600')
    found = recognized_facts(['날짜', '상호명', '결제 금액'], source)
    assert [f['value'] for f in found.values()] == ['2010-03-24', '마트 탄현점', '27,600']
    for fact in found.values():
        assert fact['source_id'] == 'page1' and fact['value'] in fact['quote'] in source[0]['text']


@pytest.mark.parametrize(('label', 'expected'), [
    ('거래일자: 2024년 2월 29일', '2024년 2월 29일'), ('2025/02/28', '2025/02/28'),
    ('유효기간: 2028-01-01', None), ('거래일자: 2025.02.29', None),
])
def test_date_variants_and_invalid_dates(label, expected):
    fact = recognized_facts(['날짜'], receipts(label)).get('날짜')
    assert (fact['value'] if fact else None) == expected


def test_missing_or_conflicting_payment_is_never_summed_or_picked_from_tax():
    for body in ('상품 25,000\n소계 25,091\n부가세 합계 2,509',
                 '합 계 27,600\n합 계 27,800', '합계 2개 27,600'):
        assert recognized_facts(['결제 금액'], receipts(body)).get('결제 금액') is None
    # A clearly labelled payment after discounts has priority over the gross total.
    assert recognized_facts(['결제 금액'], receipts('합계 30,000\n실결제금액: 27,600원'))['결제 금액']['value'] == '27,600원'
    assert recognized_facts(['결제 금액'], receipts('합계 30,000\n실결제금액 27,600원 (체크카드)'))['결제 금액']['value'] == '27,600원'
    assert recognized_facts(['결제 금액'], receipts('Total $12.34 (Visa)\nTotal $45.67 (Visa)'))['결제 금액'] is None


def test_split_payment_preserves_original_label_and_value_in_one_source():
    found = recognized_facts(['결제금액'], receipts('부가세\n2,509\n합  계 \r\n  27,600'))
    assert found['결제금액'] == dict(source_id='page1', quote='합  계 \r\n  27,600', value='27,600')
    assert recognized_facts(['결제금액'], receipts('합계') + receipts('27,600', 'page2')).get('결제금액') is None


@pytest.mark.parametrize(('field', 'line', 'value', 'valid'), [
    ('결제 금액', 'TOTAL: USD $12.34 (Visa)', '$12.34', True),
    ('결제 금액', '결 제 금 액  27,600원 (체크카드)', '27,600원', True),
    ('날짜', 'Purchase Date: 03/24/2010 (Wed)', '03/24/2010', True),
    ('날짜', 'DATE: March 24, 2010', 'March 24, 2010', True),
    ('결제 금액', 'Subtotal $10.00', '$10.00', False),
    ('결제 금액', 'Sub total $10.00', '$10.00', False),
    ('결제 금액', 'VAT Total: $2.34', '$2.34', False),
    ('결제 금액', 'Total: $12.34 (credit)', '12', False),
    ('결제 금액', '담배 1개 2,600', '2,600', False),
    ('날짜', 'Coupon expiration date: 03/24/2010', '03/24/2010', False),
])
def test_model_fact_needs_a_contextual_label_and_complete_value(field, line, value, valid):
    from workflow_engine.executors.document.receipt_fields import model_fact
    source, = receipts(line)
    fact = model_fact(field, dict(source_id='page1', quote=value, value=value), {'page1': source})
    assert bool(fact) == valid
    if valid:
        assert fact['quote'] == line and fact['value'] == value


def test_receipt_export_rejects_even_verbatim_vat_as_final_amount():
    source = receipts('E-MART\n과세 합계 25,091\n부가세 합계 2,509')
    class WrongTotal:
        def with_structured_output(self, *args, **kwargs):return self
        def invoke(self, messages):
            return Facts.model_validate({'facts':[
                dict(field='상호명', value='E-MART', quote='E-MART', source_id='page1'),
                dict(field='결제 금액', value='2,509', quote='부가세 합계 2,509', source_id='page1')]})
    node = dict(tool='extract_information', settings=dict(extraction_mode='fields', fields=['상호명','날짜','결제 금액'], instructions=None),
                bindings=[dict(input='documents', node_id='read', output='documents')])
    output = extract_information.execute(node, {'request':'','outputs':{'read':{'documents':source}}}, None,
                                         {'purpose':'영수증에서 항목 추출'}, WrongTotal())
    assert output['table']['rows'] == [{'상호명':'E-MART','날짜':None,'결제 금액':None}]
    assert [r['status'] for r in output['records']] == ['verified','not_found_or_unverified','not_found_or_unverified']
