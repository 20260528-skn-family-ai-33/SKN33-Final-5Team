"""Receipt arithmetic check on rows extraction; values are reported, never corrected."""
import csv
import io

import pytest

from workflow_engine.executors.document import extract_information, receipt_check
from workflow_engine.executors.output import export_result
from workflow_engine.executors.schemas import Rows

# OCR text of the GS25 test receipt after line grouping (region path: 3,300 and 7,500 misread with 8).
REGION_TEXT = '\n'.join(['GS25포곡골든점 0313321182', '마늘빅프랑크 1 2,100', '예거라를러레몬 3,380', '마운틴들러스트 1 2,100',
                         '한계수량/금액 3 7,580', '판족/팝 할인 -420', '과세 매출 6,436', '부가세 644', '-합신 계드 7,080',
                         '용 카 7,088', '카드번호 I 9490-94*-****-8089', '사용금액 7,080원', '증인번호 30037044 21/10/3122:23:19'])
CORRECT_TEXT = REGION_TEXT.replace('3,380', '3,300').replace('7,580', '7,500')


def rows(*amounts):
    return [{'상품명': f'상품{i}', '금액': a} for i, a in enumerate(amounts, 1)]


@pytest.mark.parametrize('text,value', [('7,080원', 7080), ('-420', -420), ('1.350', 1350), ('2,100', 2100), ('1,182점', 1182),
                                        ('9490-94*-****-8089', None), ('21/10/31', None), (None, None)])
def test_amount_reads_receipt_numbers(text, value):
    assert receipt_check.amount(text) == value


def test_totals_use_labelled_lines_and_skip_card_numbers():
    # "-합신 계드 7,080" and "용 카 7,088" are garbled labels and are not taken as paid amounts.
    assert receipt_check.totals(REGION_TEXT) == (7580, -420, [7080])
    assert receipt_check.totals('합 계 7,080\n신용카드 7,088') == (None, 0, [7080, 7088])


def test_consistent_misread_is_caught_by_the_paid_amount():
    # Items and subtotal both carry the 0 -> 8 misread, so only subtotal + discount vs paid disagrees.
    message = receipt_check.check(rows('2,100', '3,380', '2,100'), ['상품명', '금액'], REGION_TEXT)
    assert message == '불일치: 영수증 합계 7,580 + 할인 -420 = 7,160 ≠ 결제 금액 7,080'
    # One agreeing paid line is enough when another paid line is misread.
    assert receipt_check.check(rows('2,100', '3,300', '2,100'), ['상품명', '금액'],
                               '합계수량/금액 3 7,500\n할인 -420\n합 계 7,080\n신용카드 7,088') == '일치'


def test_item_misread_against_correct_subtotal():
    text = REGION_TEXT.replace('7,580', '7,500')
    message = receipt_check.check(rows('2,100', '3,380', '2,100'), ['상품명', '금액'], text)
    assert '상품 금액 합 7,580 ≠ 영수증 합계 7,500' in message


def test_misread_total_label_still_confirms_an_exact_sum_but_never_claims_a_mismatch():
    # Dongdaemun mart receipt: "합 계 11,400" and "현 금 50,000" came back as "게 11,400" and "회 50,000".
    text = '\n'.join(['광천)해저파래도 1 2,950 2,950', '롯데)파스타스150 2 1,200 2,400', '농심)우육탕큰사 1 950 950',
                      '빙)바나나우유혼 — 4,300 4,300', '롯데)누드빼빼로1 1 800 800', '과세액 10,364', '부가세 1,036',
                      '면 0', '게 11,400', '회 50,000', '38,600'])
    assert receipt_check.totals(text) == (None, 0, [])
    assert receipt_check.check(rows('2,950', '2,400', '950', '4,300', '800'), ['상품명', '금액'], text) == '일치 (합계 라벨 미인식, 11,400 줄 확인)'
    assert receipt_check.check(rows('2,950', '2,400', '950', '4,300', '900'), ['상품명', '금액'], text) is None
    # An item row's own amount is not mistaken for a total line.
    assert receipt_check.check(rows('2,950'), ['상품명', '금액'], '광천)해저파래도 1 2,950 2,950') is None


def test_correct_receipt_agrees_and_unknown_cases_are_not_guessed():
    assert receipt_check.check(rows('2,100', '3,300', '2,100'), ['상품명', '금액'], CORRECT_TEXT) == '일치'
    assert receipt_check.check(rows('2,100', None, '2,100'), ['상품명', '금액'], CORRECT_TEXT) == '확인 불가 (금액 없는 행 1개)'
    assert receipt_check.check(rows('2,100'), ['상품명', '수량'], CORRECT_TEXT) is None  # no amount field
    assert receipt_check.check(rows('2,100'), ['상품명', '금액'], '마늘빅프랑크 1 2,100') is None  # no total line


class RowsLLM:
    def __init__(self, rows): self.rows = rows
    def with_structured_output(self, schema, **kwargs): return self
    def invoke(self, messages): return Rows.model_validate({'rows': self.rows})


def cell(field, value, quote): return {'field': field, 'value': value, 'source_id': 's1', 'quote': quote}


def test_rows_extraction_adds_the_check_column_before_status_and_keeps_values():
    part = {'source_id': 's1', 'file_id': 'receipt', 'file_name': 'receipt.png', 'text': REGION_TEXT}
    llm = RowsLLM([{'cells': [cell('상품명', n, q), cell('금액', a, q)]}
                   for n, a, q in [('마늘빅프랑크', '2,100', '마늘빅프랑크 1 2,100'), ('예거라를러레몬', '3,380', '예거라를러레몬 3,380'),
                                   ('마운틴들러스트', '2,100', '마운틴들러스트 1 2,100')]])
    node = {'tool': 'extract_information', 'bindings': [{'input': 'documents', 'node_id': 'read', 'output': 'documents'}],
            'settings': {'fields': ['상품명', '금액'], 'instructions': None, 'extraction_mode': 'rows'}}
    records = extract_information.execute(node, {'outputs': {'read': {'documents': [part]}}, 'request': '영수증 정리'}, None, {}, llm)['records']
    assert [r['금액'] for r in records] == ['2,100', '3,380', '2,100']
    assert all(r['합계_검산'].startswith('불일치') and r['status'] == 'verified' for r in records)
    saved = export_result.execute({'tool': 'export_result', 'bindings': [{'input': 'records', 'node_id': 'extract', 'output': 'records'}],
                                   'settings': {'format': 'csv'}}, {'outputs': {'extract': {'records': records}}}, None, {}, None)['artifact']
    header = next(csv.reader(io.StringIO(saved['content'].lstrip('﻿'))))
    assert header == ['file_name', 'row', '상품명', '금액', '합계_검산', 'status', 'evidence']


def test_check_column_name_is_reserved():
    assert '합계_검산' in extract_information.reserved_field_issue(['상품명', '합계_검산'])
