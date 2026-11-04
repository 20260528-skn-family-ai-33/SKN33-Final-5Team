import pytest

from workflow_engine.catalog.registry import node_ports
from workflow_engine.executors.document.extract_information import execute
from workflow_engine.executors.document.receipt_items import ITEM_FIELDS, extract_items, preview_items
from workflow_engine.library.reusable_workflows import requires_model


def doc(text, file_id='f', source_id='b1'):
    return dict(text=text, file_id=file_id, source_id=source_id, file_name='receipt.jpg')


HEADER = '상품코드 단 가 수량 금 액\n'
SCAN = (HEADER + '001A3리필속지(20매)\n*8809074396277 1,3002 2,600\n'
        '002 2 힙지0링3공바인다(70mn)\n*8809074397502 2,500 10 25,000\n'
        '부가세 과세 물품가액 25,091\n상품가격에 이미 포함된 부가세(2,509)\n합 계 27,600')


def test_evidence_and_uncertain_ocr_in_realistic_split_layout():
    parts = [doc(SCAN)]
    result = extract_items(parts, ITEM_FIELDS)
    first, second = result['records']
    assert [first[k] for k in ITEM_FIELDS] == ['8809074396277', 'A3리필속지(20매)', None, None, '2,600']
    assert first['status'] == 'partial'
    assert [second[k] for k in ITEM_FIELDS] == ['8809074397502', '2 힙지0링3공바인다(70mn)', '2,500', '10', '25,000']
    for record in result['records']:
        for field, proof in record['evidence'].items():
            assert record[field] in proof['quote'] in SCAN
            assert proof['source_id'] == 'b1' and proof['file_id'] == 'f'
    assert preview_items(parts, limit=1)[0]['금액'] == '2,600'
    assert result['table']['rows'][0]['금액'] == 2600


def test_receipt_subset_uses_printed_column_order_and_keeps_codes_as_text():
    parts = [doc('상품코드 상품명 수량 단가 금액\n00123456 우유 2 1,300 2,600\n소계 2,600'),
             doc('SKU Description Qty Unit Price Amount\n00003450 tea 3 500 1,500\n'
                 'Discount -100\nAB-404 coffee 2 500 1,000\nTotal 2,400', 'g', 'b2')]
    result = extract_items(parts, ['수량', '상품코드', '단가'])
    assert result['table']['columns'] == ['파일명', '수량', '상품코드', '단가']
    assert [[row[k] for k in result['table']['columns']] for row in result['table']['rows']] == [
        ['receipt.jpg',2,'00123456',1300], ['receipt.jpg',3,'00003450',500], ['receipt.jpg',2,'AB-404',500]]
    assert [(r['file_id'],r['row']) for r in result['records']] == [('f',1),('g',1),('g',2)]
    assert all(set(row['evidence']) == {'수량','상품코드','단가'} for row in result['records'])


def test_no_item_rows_for_totals_or_unsupported_layout():
    for text in ('슈퍼마켓\n부가세 2,509\n합 계 27,600', HEADER+'현금영수증\n카드 12345 2 27,600'):
        assert preview_items([doc(text)]) == []
        with pytest.raises(ValueError, match=r'receipt.jpg.*상품 내역 행'):
            extract_items([doc(SCAN), doc(text, 'g', 'g1')], ['금액'])


def test_overlapping_chunks_do_not_duplicate_or_merge_distinct_purchases():
    line = '00001234 사과 1,000 2 2,000\n'
    text = HEADER + line * 3
    chunks = [{**doc(text[:len(HEADER)+len(line)*2+7]), 'block_id':'b', 'offset':0},
              {**doc(text[len(HEADER)+4:], source_id='b2'), 'block_id':'b', 'offset':len(HEADER)+4}]
    assert [r['상품코드'] for r in preview_items(chunks)] == ['00001234']*3


def test_mode_is_native_without_model_and_old_modes_keep_their_ports():
    node = dict(tool='extract_information', bindings=[dict(input='documents', node_id='read',output='documents')],
                settings=dict(extraction_mode='receipt_items', fields=['상품코드', '금액']))
    assert node_ports(node)[1] == {'records':'records','table':'table'}
    assert not requires_model({'workflow':{'nodes':[node]}})
    assert execute(node, {'outputs': {'read':{'documents':[doc(SCAN)]}}}, None, {}, None)['table']['rows'] == [
        {'파일명':'receipt.jpg','상품코드':'8809074396277','금액':2600},
        {'파일명':'receipt.jpg','상품코드':'8809074397502','금액':25000}]
    node['settings']['extraction_mode'] = 'rows'
    assert node_ports(node)[1] == {'records':'records'} and requires_model({'workflow':{'nodes':[node]}})


@pytest.mark.parametrize('fields', [[], ['금액','금액'], ['합계']])
def test_invalid_selected_fields_are_rejected(fields):
    with pytest.raises(ValueError, match='상품 내역'):
        extract_items([doc(SCAN)], fields)
