"""Extract receipt line items from their printed table, keeping per-cell source quotes."""
from decimal import Decimal
from itertools import islice
import re


ITEM_FIELDS = ('상품코드', '상품명', '단가', '수량', '금액')
_COLUMNS = {'상품코드': r'상품코드|품목코드|바코드|sku|barcode', '상품명': r'상품명|품명|품목명|description|productname|itemname',
            '단가': r'단가|unitprice', '수량': r'수량|quantity|qty', '금액': r'금액|amount|linetotal'}
_END = re.compile(r'^(?:부가(?:세|가치세)|과세(?:물품|금액|매출|합계)|면세(?:물품|금액|매출|합계)|공급가|소계|합계|총합계|'
                  r'(?:최종)?결제(?:금액|합계|수단)|받을금액|판매총액|주문합계|총수량|상품가격에|신용카드|거스름|현금영수증|subtotal|grandtotal|total|vat|tax:)', re.I)
_ADJUSTMENT = re.compile(r'^(?:할인(?:금?액|합계)?|에누리|카드할인|행사할인|적립포인트|사용포인트|discount|savings)[:\s+-]', re.I)
_NUMBER = re.compile(r'[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?(?:원)?')
_COUNT = re.compile(r'-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:개)?')
_CODE = re.compile(r'[*#]?(\d{3,24}|(?=[A-Za-z0-9-]{4,24}$)(?=[A-Za-z0-9-]*\d)[A-Za-z][A-Za-z0-9-]{3,23})')
_INDEXED_NAME = re.compile(r'^\s*(\d{3})[.)]?\s*(?=[^\W\d_]|[가-힣]|\d+\s+\D)(.+)', re.UNICODE)


def item_field_issue(fields):
    if not fields or len(set(fields)) != len(fields) or any(f not in ITEM_FIELDS for f in fields):
        return '상품 내역에는 상품코드·상품명·단가·수량·금액 중 추출할 항목을 중복 없이 선택하세요.'
    return None


def _header(line):
    compact = ''.join(line.casefold().split())
    if any(char.isdigit() for char in compact):
        return []
    positions = [(m.start(), name) for name, pattern in _COLUMNS.items() if (m := re.search(pattern, compact))]
    if len(positions) < 2 or not any(n in ('단가','수량','금액') for _, n in positions):
        return []
    if len(positions) < 3 and not any(n in ('상품명','상품코드') for _, n in positions):
        return []
    return [name for _, name in sorted(positions)]


def _lines(parts):
    # Chunks within a single OCR block can overlap. Keep complete lines once without
    # de-duplicating two separately printed purchases of the very same product.
    groups = {}
    for part in parts:
        key = part.get('block_id') or (part.get('page'), part['source_id'].rsplit(':', 1)[0])
        groups.setdefault(key, []).append(part)
    for group in groups.values():
        chunks = sorted(group, key=lambda part: part.get('offset', 0))
        seen_until = -1
        for i, part in enumerate(chunks):
            start = part.get('offset', 0)
            for raw in part['text'].splitlines(keepends=True):
                end = start + len(raw)
                if start >= seen_until:
                    unfinished = not raw.endswith(('\r','\n')) and i+1 < len(chunks) and chunks[i+1].get('offset', 0) < end
                    if not unfinished:
                        seen_until = end
                        if raw.strip():
                            yield part, raw.strip()
                start = end


def _fact(value, part, line):
    return {'source_id': part['source_id'], 'value': value, 'quote': line}


def _file_items(parts):
    columns, pending = [], None
    last_page = None
    for part, line in _lines(parts):
        page = part.get('page')
        if last_page is not None and last_page != page:
            pending = None
        last_page = page
        if _END.match(''.join(line.split())):
            columns, pending = [], None
            continue
        if _ADJUSTMENT.match(line):
            pending = None
            continue
        header = _header(line)
        if header:
            columns, pending = header, None
            continue
        if not columns:
            continue

        # Find the printed quantities from the right; a broken OCR number with a
        # comma still occupies its original cell, but is never reconstructed.
        tokens = list(re.finditer(r'\S+', line))
        code = _CODE.fullmatch(tokens[0].group()) if ('상품코드' in columns or tokens[0].group().startswith('*')) else None
        first = 1 if code else 0
        tail = len(tokens)
        while tail > first and re.fullmatch(r'[+-]?\d[\d,.]*(?:원|개)?', tokens[tail-1].group()):
            tail -= 1
        numbers = [t.group() for t in tokens[tail:]]
        if not numbers:
            indexed = _INDEXED_NAME.match(line)
            name = indexed[2].strip() if indexed else line
            pending = _fact(name, part, line) if re.search(r'[^\W\d_]', name) else None
            continue
        facts = {}
        if code:
            facts['상품코드'] = _fact(code[1], part, line)
        if tail > first:
            name = line[tokens[first].start():tokens[tail-1].end()]
            indexed = _INDEXED_NAME.match(name) if not code else None
            if indexed:
                name = indexed[2].strip()
            if re.search(r'[^\W\d_]', name):
                facts['상품명'] = _fact(name, part, line)
        elif pending:
            facts['상품명'] = pending
        pending = None
        if not ('상품명' in facts or '상품코드' in facts):
            continue
        numeric_cols = [c for c in columns if c in ('단가','수량','금액')]
        if len(numbers) == len(numeric_cols):
            cells = list(zip(numeric_cols, numbers))
        elif numeric_cols and numeric_cols[-1] == '금액' and len(numbers) >= 2:
            # A fused token (e.g. price+qty) loses the column boundary. The last
            # amount remains independently printed in its own token.
            cells = [('금액', numbers[-1])] if any(not _NUMBER.fullmatch(n) and not _COUNT.fullmatch(n) for n in numbers[:-1]) else []
        else:
            cells = []
        for field, value in cells:
            if (_COUNT if field == '수량' else _NUMBER).fullmatch(value):
                facts[field] = _fact(value, part, line)
        if '상품코드' in facts or any(field in facts for field in numeric_cols):
            yield facts


def _records(parts, fields, require_items=False):
    from workflow_engine.executors.document.extract_information import checked
    if issue := item_field_issue(fields):
        raise ValueError(issue)
    files = {}
    for part in parts:
        files.setdefault(part['file_id'], []).append(part)
    for file_id, pages in files.items():
        lookup = {p['source_id']: p for p in pages}
        name = pages[0]['file_name']
        row = 0
        for row, facts in enumerate(_file_items(pages), 1):
            values, evidence = {}, {}
            for field in fields:
                value, proof = checked(facts.get(field), lookup)
                values[field] = value
                if proof: evidence[field] = proof
            status = 'verified' if len(evidence) == len(fields) else 'partial' if evidence else 'not_found_or_unverified'
            yield dict(file_id=file_id, file_name=name, row=row, **values, evidence=evidence, status=status)
        if require_items and not row:
            raise ValueError(f'{name}: 영수증에서 상품 내역 행을 확인하지 못했습니다. 상품명과 금액이 있는 표가 선명한 사진이나 PDF를 연결하세요.')


def _cell(field, value):
    if field not in ('단가', '수량', '금액') or value is None:
        return value
    # Only the XLSX table is numeric; keep the exact printed text in records and previews.
    number = Decimal(value.rstrip('원개').replace(',', ''))
    if len(number.as_tuple().digits) > 15:  # Excel silently rounds longer numbers
        return value
    return int(number) if number == number.to_integral_value() else float(number)


def extract_items(parts, fields):
    records = list(_records(parts, fields, require_items=True))
    if not records:
        raise ValueError('상품 내역을 읽을 영수증이 없습니다. 사진이나 PDF를 연결하세요.')
    table = {'columns': ['파일명', *fields], 'rows': [
        {'파일명': r['file_name'], **{field: _cell(field, r[field]) for field in fields}} for r in records]}
    return {'records': records, 'table': table}


def preview_items(parts, limit=5):
    """Bounded preview of the same original cells the final workbook will export."""
    return [{'파일명': r['file_name'], **{field: r[field] for field in ITEM_FIELDS}}
            for r in islice(_records(parts, ITEM_FIELDS), limit)]
