"""Extract every requested cell from parser-native tables without a model.

The parser's row/column coordinates, not adjacency in flattened OCR text, own
row identity. Unknown/merged layouts are refused rather than silently flattened.
"""
import re
import unicodedata


class TableStructureError(ValueError):
    """Parser evidence needs review; a clarification answer cannot repair it."""
    code = 'document_table_structure'


COVERAGE_KEY = 'row_coverage'
TABLE_INTENT = re.compile(r'엑셀|excel|xlsx|csv|표로|표 형태|표 형식|목록으로|한 줄씩|행으로|스프레드시트', re.I)
_ITEM_SCOPE = re.compile(
    r'(?:행|품목|항목)\s*단위|(?:품목|항목)\s*(?:별|마다)'
    r'|(?:모든|각|전체|반복(?:되는)?)\s*(?:품목|행|항목)'
    r'|\b(?:each|every|all)\s+(?:rows?|line[\s-]+items?)\b', re.I
)
_DOCUMENT_SCOPE = re.compile(r'(?:문서|파일)\s*(?:당|마다|별(?:로)?)')
_DOCUMENT_COUNT = re.compile(_DOCUMENT_SCOPE.pattern + r'\s*(?:\S+\s+){0,3}(?:한\s*(?:행|줄|값|개)|하나|1\s*개)')
_ROW_REQUEST = re.compile(r'한\s*(?:행|줄)\s*씩|모두\s*(?:추출|뽑)|목록으로')
_SCALAR_REQUEST = re.compile(
    r'\b한\s*값(?!\s*씩)|\b(?:한\s*개|하나|1\s*개)\s*만|\b(?:첫(?:\s*번째)?|마지막)\s*(?:행|항목|값)\s*만'
)
_ROW_OPERATION = re.compile(
    r'정렬|(?:오름|내림)\s*차순|필터(?:링)?|그룹(?:화|별)'
    r'|\b(?:sort|filter)(?:ed|ing)?\b|\bgroup(?:ed|ing)?\s+by\b', re.I
)
_SUMMARY_LABELS = frozenset({
    '합계', '소계', '총계', '총합계', '부가세', '부가가치세', '세액',
    'total', 'subtotal', 'grandtotal', 'vat', 'tax',
})
_SUMMARY_VALUE = re.compile(r'[−+-]?[₩$€£]?\d+(?:[,.]\d+)*(?:원|%)?')
ROW_DESIGN_INSTRUCTION = ('문서의 반복 항목을 CSV/표로 정리하는 경우 extract_information.settings.extraction_mode=rows로 지정하고 '
                          'fields에는 사용자가 요청한 열을 넣습니다. 문서당 한 값인 메타데이터 추출만 fields 모드를 사용합니다. '
                          'CSV의 export_result.settings.fields에도 요청한 열만 지정합니다. 근거와 검증 정보는 내부 records에 유지하며 사용자 CSV 열에 넣지 않습니다. '
                          '문서 제목 때문에 반복 행을 단일 값 추출로 바꾸지 마세요.')


def label(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFC', '' if text is None else str(text))).casefold()


def row_collection_intent(*requests):
    """Identify a collection request without treating a table's format as its granularity."""
    table_requested = False
    row_operation_requested = False
    for request in reversed(requests):
        if not request:
            continue
        # "File by file" can organize each item. A count without that item scope limits the file's result.
        if _ITEM_SCOPE.search(request):
            return True
        if _DOCUMENT_COUNT.search(request):
            return False
        if _ROW_REQUEST.search(request):
            return True
        if _DOCUMENT_SCOPE.search(request) or _SCALAR_REQUEST.search(request):
            return False
        table_requested = table_requested or bool(TABLE_INTENT.search(request))
        row_operation_requested = row_operation_requested or bool(_ROW_OPERATION.search(request))
    # A format-only request must not inherit a conflicting row assumption from generated instructions.
    return row_operation_requested if table_requested else None


def _table_parts(parts):
    seen = set()
    for part in parts:
        if 'table_cells' not in part:
            continue
        identity = (part['file_id'], part.get('block_id', part['source_id']))
        if identity in seen:
            continue
        seen.add(identity)
        yield part


def _grid(cells):
    grid = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise TableStructureError('표 셀의 행·열 위치를 확인할 수 없습니다.')
        row = cell.get('row', cell.get('start_row_offset_idx'))
        column = cell.get('column', cell.get('start_col_offset_idx'))
        if type(row) is not int or type(column) is not int or row < 0 or column < 0:
            raise TableStructureError('표 셀의 행·열 위치를 확인할 수 없습니다.')
        if column in grid.setdefault(row, {}):
            raise TableStructureError('동일한 행·열에 여러 셀이 있어 추출을 중단했습니다.')
        grid[row][column] = cell
    return sorted(grid.items())


def _unit_cell(cell):
    return (cell.get('row_span') or 1) == 1 and (cell.get('col_span') or 1) == 1


def _marked_header(row):
    return any(cell.get('column_header') is True for cell in row.values())


def _leading_headers(grid):
    marked = [(number, row) for number, row in grid if _marked_header(row)]
    if not marked:
        return []
    leading = [marked[0]]
    for number, row in marked[1:]:
        if number != leading[-1][0] + 1:
            break
        leading.append((number, row))
    shapes = [_row_shape(row) for _, row in leading]
    if any(_row_shape(row) not in shapes for _, row in marked[len(leading):]):
        raise TableStructureError('표에서 서로 다른 머리글 구간을 하나의 항목 표로 확인할 수 없습니다.')
    return leading


def native_header_labels(parts):
    labels = set()
    for part in _table_parts(parts):
        grid = _grid(part.get('table_cells') or [])
        for _, row in _leading_headers(grid) or grid:
            labels.update(label(cell.get('text')) for cell in row.values())
    return labels


def _columns(grid, targets):
    headers = _leading_headers(grid)
    partial_header = False
    for number, row in reversed(headers) if headers else grid:
        found = {}
        for column, cell in row.items():
            field = targets.get(label(cell.get('text')))
            if field is None:
                continue
            if field in found:
                raise TableStructureError('표에 같은 추출 열 이름이 여러 개 있습니다.')
            found[field] = column
        if len(found) == len(targets):
            if any(not _unit_cell(row[column]) for column in found.values()):
                raise TableStructureError('병합된 표 머리글의 열 대응을 확인할 수 없습니다.')
            return headers[-1][0] if headers else number, found
        partial_header = partial_header or bool(found)
    if partial_header:
        raise ValueError('표에서 요청한 열을 모두 찾지 못했습니다. 원본의 열 이름을 확인해 주세요.')
    return None, {}


def _text(cell):
    value = cell.get('text')
    return '' if value is None else str(value).strip()


def _check_source_key(part):
    if part.get('block_id') is not None:
        return part['file_id'], 'block', part['block_id']
    source_id = part['source_id']
    page_source, delimiter, start = source_id.rpartition(':')
    if delimiter and type(part.get('offset')) is int and start == str(part['offset']):
        return part['file_id'], 'page', page_source
    return part['file_id'], 'chunk', source_id


def _join_source_chunks(chunks):
    if any(type(chunk.get('offset')) is not int or chunk['offset'] < 0 for chunk in chunks):
        return '\n'.join(chunk['text'] for chunk in chunks)
    segments = []
    end = -1
    for chunk in sorted(chunks, key=lambda part: part['offset']):
        start = chunk['offset']
        text = chunk['text']
        if start > end:
            segments.append(text)
        else:
            segments[-1] += text[end - start:]
        end = max(end, start + len(text))
    return '\n'.join(segments)


def _native_check_lines(grid, fields):
    from workflow_engine.executors.document import receipt_check

    header, columns = _columns(grid, {label(field): field for field in fields})
    amount_column = columns.get(receipt_check.line_amount_field(fields))
    if amount_column is None:
        return
    details = {number for number, _ in _detail_rows(grid, header, strict=True)}
    for number, row in grid:
        if number <= header or number in details:
            continue
        markers = _summary_labels(row)
        cell = row.get(amount_column)
        if len(markers) != 1 or cell is None or not _unit_cell(cell):
            continue
        amount = receipt_check.amount(_text(cell))
        if amount is not None:
            marker = ' '.join(_text(row[markers[0]]).split())
            yield f'{marker} {amount}'


def row_check_text(parts, *, native_fields=None):
    """Project verified native summary rows or preserve parser text for the OCR check."""
    sources = {}
    for part in parts:
        sources.setdefault(_check_source_key(part), []).append(part)
    text = []
    for chunks in sources.values():
        cells = next((part['table_cells'] for part in chunks if part.get('table_cells')), None)
        if not cells:
            text.append(_join_source_chunks(chunks))
            continue
        grid = _grid(cells)
        if native_fields is not None:
            text.extend(_native_check_lines(grid, native_fields))
        else:
            text.extend(' '.join(_text(row[column]).replace('\n', ' ') for column in sorted(row))
                        for _, row in grid)
    return '\n'.join(text)


def _summary_labels(row):
    return [column for column, cell in row.items() if label(cell.get('text')).rstrip(':：') in _SUMMARY_LABELS]


def _row_shape(row):
    return {column: (label(cell.get('text')), cell.get('row_span') or 1, cell.get('col_span') or 1)
            for column, cell in row.items()}


def _detail_rows(grid, header, *, strict):
    # A projected column can contain the same value as its heading. Only an
    # entire row matching the original header can be a repeated header.
    header_shape = _row_shape(next(row for number, row in grid if number == header))
    rows = [(number, row) for number, row in grid if number > header
            and _row_shape(row) != header_shape
            and not _marked_header(row)
            and any(_text(cell) for cell in row.values())]
    ordinary = [row for _, row in rows if not _summary_labels(row)]
    text_columns = [{column for column, cell in row.items() if _text(cell) and not _SUMMARY_VALUE.fullmatch(label(_text(cell)))}
                    for row in ordinary]
    identifier_columns = set.intersection(*text_columns) if text_columns else set()
    for number, row in rows:
        summary_columns = _summary_labels(row)
        if not summary_columns:
            yield number, row
            continue
        marker_column = summary_columns[0]
        marker = row[marker_column]
        values = {column: _text(cell) for column, cell in row.items() if column not in summary_columns and _text(cell)}
        # A normal item identifier can accompany a name that happens to equal a summary label.
        if len(summary_columns) == 1 and _unit_cell(marker) and any(
            column in identifier_columns and not _SUMMARY_VALUE.fullmatch(label(value))
            for column, value in values.items()
        ):
            yield number, row
            continue
        missing_identifier = any(column < marker_column and column not in values for column in identifier_columns)
        marker_width = marker.get('col_span') or 1
        if (len(summary_columns) == 1 and values and (marker.get('row_span') or 1) == 1
                and type(marker_width) is int and marker_width > 0
                and all(column >= marker_column + marker_width and _unit_cell(row[column]) and _SUMMARY_VALUE.fullmatch(label(value))
                        for column, value in values.items())
                and (marker_width > 1 or missing_identifier)):
            continue
        if strict:
            raise ValueError('합계·세금 행과 일반 항목을 구분할 수 없습니다. 원본 표를 확인해 주세요.')


def has_repeated_rows(parts, fields, *, across_pages=False):
    """Find multiple value rows under the same header, across pages only for row requests."""
    targets = {label(field): field for field in fields}
    if not targets or len(targets) != len(fields):
        return False
    pages_by_header = {}
    for part in _table_parts(parts):
        if not part.get('table_cells') or part.get('review_required'):
            continue
        try:
            grid = _grid(part['table_cells'])
            header, selected = _columns(grid, targets)
        except ValueError:
            continue
        if header is None:
            continue
        count = 0
        for _, row in _detail_rows(grid, header, strict=False):
            cells = [row.get(column) for column in selected.values()]
            if all(cell is not None and _unit_cell(cell) for cell in cells) and any(_text(cell) for cell in cells):
                count += 1
                if count == 2:
                    return True
        if not across_pages or not count:
            continue
        page = part.get('page')
        if page is None:
            page = (part.get('native_location') or {}).get('page')
        if type(page) is not int or page < 1:
            continue
        header_shape = _row_shape(next(row for number, row in grid if number == header))
        if any(type(span) is not int or span < 1 for _, height, width in header_shape.values() for span in (height, width)):
            continue
        key = (part['file_id'], frozenset(header_shape.items()))
        if pages_by_header.setdefault(key, page) != page:
            return True
    return False


def native_records(parts, fields, evidence_keys, max_rows):
    records, relevant, unmatched_tables, unstructured_tables = [], False, False, False
    targets = {label(field): field for field in fields}
    if len(targets) != len(fields):
        raise ValueError('공백을 제외하면 같은 추출 항목이 있습니다. 열 이름을 구분해 주세요.')
    if not targets:
        return None
    for part in _table_parts(parts):
        cells = part.get('table_cells')
        if not cells:
            unstructured_tables = True
            continue
        if part.get('review_required'):
            raise TableStructureError('표 인식 결과에 검토가 필요합니다. 원본과 대조한 뒤 실행해 주세요.')
        grid = _grid(cells)
        header, selected = _columns(grid, targets)
        if header is None:
            unmatched_tables = True
            continue
        relevant = True
        for number, row in _detail_rows(grid, header, strict=True):
            record = dict(file_name=part['file_name'], row=len(records) + 1)
            evidence = {}
            for field, column in selected.items():
                cell = row.get(column)
                if cell is None:
                    raise TableStructureError('표의 일부 셀 위치가 없어 모든 항목의 대응을 확인할 수 없습니다.')
                if not _unit_cell(cell):
                    raise TableStructureError('추출 열의 병합 셀을 행별로 대응할 수 없습니다. 표 구조를 확인해 주세요.')
                value = _text(cell)
                record[field] = value or None
                evidence[field] = {**{key: part[key] for key in evidence_keys if key in part},
                                   'quote': value, 'table_row': number, 'table_column': column}
            record.update(status='verified', evidence=evidence, **{COVERAGE_KEY: 'complete'})
            records.append(record)
            if len(records) > max_rows:
                raise ValueError('추출 행 제한을 넘었습니다. 파일을 나누어 주세요. 일부 행만 저장하지 않았습니다.')
    if relevant and unstructured_tables:
        raise TableStructureError('셀 위치를 확인할 수 없는 표가 함께 있어 전체 행의 추출 범위를 확인할 수 없습니다. 원본 표를 검토해 주세요.')
    if relevant and unmatched_tables:
        raise ValueError('요청한 열과 대응하지 않는 표가 함께 있습니다. 전체 행의 추출 범위를 확인해 주세요.')
    return records if relevant else None
