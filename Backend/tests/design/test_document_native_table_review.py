import pytest

from workflow_engine.executors.document.table_rows import has_repeated_rows, native_records


def table(rows, name='table', page=1):
    return {
        'file_id': 'sample', 'file_name': 'sample.pdf', 'source_id': name, 'block_id': name, 'page': page,
        'table_cells': [dict(row=r, column=c, text=value)
                        for r, row in enumerate(rows) for c, value in enumerate(row)],
    }


def test_totals_and_vat_are_excluded_while_items_named_like_totals_remain():
    part = table([
        ('품목코드', '품명', '실입고'),
        ('A01', '복사용지', '48'),
        ('', '소계', '48'),
        ('A02', '합계', '20'),
        ('A03', '합계표 스티커', '38'),
        ('', '합계', '106'),
        ('', '부가가치세', '10'),
    ])
    records = native_records([part], ['품명', '실입고'], ['page'], 3)
    assert [(record['품명'], record['실입고'], record['row']) for record in records] == [
        ('복사용지', '48', 1), ('합계', '20', 2), ('합계표 스티커', '38', 3),
    ]
    assert [record['evidence']['품명']['table_row'] for record in records] == [1, 3, 4]
    assert all(record['row_coverage'] == 'complete' for record in records)


def test_merged_summary_label_is_excluded_before_requesting_item_cell_alignment():
    part = table([
        ('Code', 'Label', 'Value'),
        ('A01', 'Paper', '5'),
        ('Grand total', '', '5'),
    ])
    cells = part['table_cells']
    label = next(cell for cell in cells if (cell['row'], cell['column']) == (2, 0))
    label['col_span'] = 2
    cells[:] = [cell for cell in cells if (cell['row'], cell['column']) != (2, 1)]
    records = native_records([part], ['Label', 'Value'], [], 5)
    assert [(record['Label'], record['Value']) for record in records] == [('Paper', '5')]


def test_total_label_with_ordinary_item_shape_requires_review():
    part = table([('품명', '실입고'), ('복사용지', '5'), ('합계', '5')])
    with pytest.raises(ValueError, match='합계·세금 행과 일반 항목을 구분할 수 없습니다'):
        native_records([part], ['품명', '실입고'], [], 5)
    assert not has_repeated_rows([part], ['품명'])


def test_scalar_field_check_requires_multiple_value_rows_under_a_single_header():
    first = table([('문서번호', '발행일'), ('DLV-1', '2026-11-17')], name='first')
    second = table([('문서번호', '발행일'), ('DLV-2', '2026-11-18')], name='second', page=2)
    assert not has_repeated_rows([first, second], ['문서번호'])
    repeated = table([
        ('문서번호', '발행일'),
        ('DLV-1', '2026-11-17'),
        ('문서번호', '발행일'),
        ('DLV-2', '2026-11-18'),
    ])
    malformed = table([('무관', ''), ('다른 값', '')], name='malformed')
    malformed['table_cells'][0]['row'] = None
    assert has_repeated_rows([malformed, repeated], ['문서번호'])


def test_scalar_field_check_ignores_rows_without_a_requested_value():
    part = table([('문서번호', '발행일'), ('DLV-1', '2026-11-17'), ('', '2026-11-18')])
    assert not has_repeated_rows([part], ['문서번호'])
    assert has_repeated_rows([part], ['문서번호', '발행일'])


def test_row_request_detects_same_table_with_one_value_row_on_each_page():
    first = table([('Item', 'Count'), ('Paper', '3')], name='first')
    second = table([(' item ', 'COUNT'), ('Pencil', '5')], name='second', page=None)
    second['native_location'] = {'page': 2}
    assert has_repeated_rows([second, first], ['Item'], across_pages=True)
    assert not has_repeated_rows([second, first], ['Item'])


@pytest.mark.parametrize('difference', ['page', 'missing_page', 'file', 'header', 'header_span', 'empty_value'])
def test_row_request_keeps_independent_single_row_tables_distinct(difference):
    first = table([('Item', 'Count'), ('Paper', '3')], name='first')
    second = table([('Item', 'Count'), ('Pencil', '5')], name='second', page=2)
    if difference == 'page':
        second['page'] = first['page']
    elif difference == 'missing_page':
        second['page'] = None
    elif difference == 'file':
        second['file_id'] = 'another-file'
    elif difference == 'header':
        second['table_cells'][1]['text'] = 'Weight'
    elif difference == 'header_span':
        second['table_cells'][1]['col_span'] = 2
    else:
        second['table_cells'][2]['text'] = ''
    assert not has_repeated_rows([first, second], ['Item'], across_pages=True)
