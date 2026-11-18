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
    # A conflicting paid line must remain visible even when another one agrees.
    assert receipt_check.check(rows('2,100', '3,300', '2,100'), ['상품명', '금액'],
                               '합계수량/금액 3 7,500\n할인 -420\n합 계 7,080\n신용카드 7,088').startswith('불일치:')


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
    assert receipt_check.check(rows('2,950', '2,400', '950', '4,300', '800'), ['상품명', '금액'], text) == '확인 필요 (합계 라벨 미인식, 11,400 줄 확인)'
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
    # Check results and provenance remain internal. A totals discrepancy or
    # value citation does not certify that every source row was extracted.
    assert list(records[0]).index('합계_검산') < list(records[0]).index('status')
    assert all(record['row_coverage'] == 'unverified' for record in records)
    with pytest.raises(ValueError, match='전체 행'):
        export_result.execute({'tool': 'export_result', 'bindings': [{'input': 'records', 'node_id': 'extract', 'output': 'records'}],
                              'settings': {'format': 'csv'}}, {'outputs': {'extract': {'records': records}}}, None, {}, None)


def test_check_column_name_is_reserved():
    assert '합계_검산' in extract_information.reserved_field_issue(['상품명', '합계_검산'])


def test_conflicting_paid_amount_cannot_be_confirmed_by_matching_total():
    text = '소계 7,580\n할인 -420\n합계 7,160\n사용금액 7,080'
    assert receipt_check.check(rows('2,100', '3,380', '2,100'), ['금액'], text).startswith('불일치:')


@pytest.mark.parametrize('field', ['가격', 'price', '단가'])
def test_unit_price_is_not_summed_as_line_amount(field):
    assert receipt_check.check([{'수량': '3', field: '1,000'}], ['수량', field],
                               '사과 3 1,000 3,000\n합계 3,000') is None


def test_cash_tendered_and_change_are_not_payment_totals():
    text = '소계 3,000\n합계 3,000\n현금 받은금액 10,000\n거스름돈 7,000'
    assert receipt_check.check(rows('3,000'), ['금액'], text) == '일치'


def test_unlabelled_item_amount_does_not_prove_total():
    assert receipt_check.check(rows('1,000'), ['금액'], '사과 1,000\n배 2,000').startswith('확인 필요')


def native_receipt(file_id, amounts, summary, amount_field='금액'):
    grid = [['코드', '상품명', amount_field],
            *[[f'A{i}', f'상품{i}', value] for i, value in enumerate(amounts, 1)]]
    table = {'file_id': file_id, 'file_name': file_id + '.pdf', 'source_id': file_id + ':table',
            'text': '\n'.join(' '.join(row) for row in grid),
            'table_cells': [dict(row=r, column=c, text=value)
                            for r, row in enumerate(grid) for c, value in enumerate(row)]}
    return [table, {'file_id': file_id, 'file_name': file_id + '.pdf', 'source_id': file_id + ':summary', 'text': summary}]


def extract_rows(parts, fields, llm=None):
    node = {'tool': 'extract_information',
            'bindings': [{'input': 'documents', 'node_id': 'read', 'output': 'documents'}],
            'settings': {'fields': fields, 'instructions': None, 'extraction_mode': 'rows'}}
    return extract_information.execute(node, {'outputs': {'read': {'documents': parts}},
                                             'request': '영수증 CSV 정리'}, None, {}, llm)['records']


@pytest.mark.parametrize(('amounts', 'summary', 'expected'), [
    (['1000', '2000'], '합계 4000', '불일치'),
    (['1000', '2000'], '합계 3000', '일치'),
    (['1000', '2000'], '소계 3000\n할인 -500\n사용금액 2500', '일치'),
    (['1000', '2000'], '소계 3000\n할인 -500\n사용금액 3000', '불일치'),
    (['1000', ''], '합계 3000', '확인 불가'),
])
def test_native_rows_apply_the_same_arithmetic_check_without_changing_values(amounts, summary, expected):
    records = extract_rows(native_receipt('native', amounts, summary), ['상품명', '금액'])
    assert [record['금액'] for record in records] == [value or None for value in amounts]
    assert all(record['합계_검산'].startswith(expected) for record in records)
    assert all(record['status'] == 'verified' and record['row_coverage'] == 'complete' for record in records)
    assert all('table_row' in record['evidence']['금액'] for record in records)


@pytest.mark.parametrize('fields', [['상품명'], ['상품명', '단가']])
def test_native_rows_without_line_amount_do_not_add_arithmetic_findings(fields):
    records = extract_rows(native_receipt('native', ['1000', '2000'], '합계 4000', amount_field='단가'), fields)
    assert all('합계_검산' not in record for record in records)


def test_native_and_model_rows_keep_arithmetic_and_coverage_scoped_to_each_file():
    first = native_receipt('first', ['1000', '2000'], '합계 4000')
    second = native_receipt('second', ['500', '600'], '합계 1100')
    legacy = {'file_id': 'legacy', 'file_name': 'legacy.pdf', 'source_id': 's1', 'text': '상품 700\n합계 700'}
    model = RowsLLM([{'cells': [cell('상품명', '상품', '상품 700'), cell('금액', '700', '상품 700')]}])
    # Interleaved source chunks must still be grouped by document, not checked as a combined receipt.
    records = extract_rows([*first, legacy, *second], ['상품명', '금액'], model)
    assert [record['합계_검산'].split(':')[0] for record in records] == ['불일치', '불일치', '일치', '일치', '일치']
    assert [record['row_coverage'] for record in records] == ['complete', 'complete', 'unverified', 'complete', 'complete']


@pytest.mark.parametrize(('paid', 'expected'), [('2500', '일치'), ('2600', '불일치')])
def test_native_summary_cells_are_checked_once_despite_flattened_overlapping_text(paid, expected):
    grid = [['코드', '상품명', '금액'], ['A1', '사과', '1000'], ['A2', '배', '2000'],
            ['', '소계', '3000'], ['', '할인', '-500'], ['', '합계', paid]]
    # The pipeline emits one cell payload and overlapping text chunks for this block.
    part = {'file_id': 'native', 'file_name': 'native.pdf', 'source_id': 'native:table:0', 'block_id': 'table',
            'text': '\n'.join(value for row in grid for value in row),
            'table_cells': [dict(row=r, column=c, text=value) for r, row in enumerate(grid) for c, value in enumerate(row)]}
    from workflow_engine.executors.document.table_rows import row_check_text
    overlap = {key: value for key, value in part.items() if key != 'table_cells'}
    overlap.update(source_id='native:table:1400', text='할인\n-500\n합계\n' + paid)
    text = row_check_text([part, overlap])
    assert receipt_check.totals(text) == (3000, -500, [int(paid)])
    assert receipt_check.check(rows('1000', '2000'), ['상품명', '금액'], text).startswith(expected)


@pytest.mark.parametrize('marker', ['합계', '합\n계'])
def test_native_total_inside_flattened_table_reaches_extraction_findings(marker):
    part = native_receipt('native', ['1000', '2000'], '')[0]
    part['table_cells'] += [dict(row=3, column=c, text=value) for c, value in enumerate(['', marker, '4000'])]
    part['text'] = '\n'.join(cell['text'] for cell in part['table_cells'])
    records = extract_rows([part], ['상품명', '금액'])
    assert [record['금액'] for record in records] == ['1000', '2000']
    assert all(record['합계_검산'].startswith('불일치') for record in records)


@pytest.mark.parametrize('block_id', [None, 'summary'])
def test_overlapping_prose_chunks_count_each_printed_discount_once(block_id):
    source = 'x' * 1450 + '\n소계 3000\n할인 -500\n결제금액 2500'
    table, summary = native_receipt('native', ['1000', '2000'], source)
    summary.update(source_id='native:1:0', offset=0)
    if block_id is not None:
        summary['block_id'] = block_id
    overlap = {**summary, 'source_id': 'native:1:1400', 'offset': 1400, 'text': source[1400:]}
    records = extract_rows([table, summary, overlap], ['상품명', '금액'])
    assert [record['합계_검산'] for record in records] == ['일치', '일치']


def test_offset_deduplication_keeps_equal_discounts_at_different_source_positions():
    source = 'x' * 1450 + '\n소계 3000\n할인 -500\n할인 -500\n결제금액 2000'
    table, summary = native_receipt('native', ['1000', '2000'], source)
    summary.update(block_id='summary', offset=0)
    overlap = {**summary, 'source_id': 'native:summary:1400', 'offset': 1400, 'text': source[1400:]}
    records = extract_rows([table, summary, overlap], ['상품명', '금액'])
    assert [record['합계_검산'] for record in records] == ['일치', '일치']
