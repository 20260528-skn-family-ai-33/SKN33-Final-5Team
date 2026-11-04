"""Project a source-backed preview from an already parsed upload."""
import re

from documents.resources.document_resources import analysis_of


_LABEL_VALUE = re.compile(r'^([^\W\d_][\w가-힣 ()/·.%-]{0,47}?)\s*[:：]\s*(\S.*)$')
_CELL = re.compile(r'^([A-Z]{1,3})([1-9]\d{0,6})$')
_SAMPLE_ROWS = 3
_MAX_FIELDS = 100


def labeled_value(line):
    match = _LABEL_VALUE.fullmatch(line)
    if match and not match[2].startswith('//'):
        return match[1].strip(), match[2].strip()
    return None


def _display(value):
    if isinstance(value, str):
        return value[:160]
    return value if isinstance(value, (int, float, bool)) else None


def _preview_table(name, grid, *, cell_order=None):
    if not grid:
        return None
    first, *rest = sorted(grid)
    keys = sorted(grid[first], key=cell_order)
    columns = [grid[first][key] for key in keys]
    if not all(isinstance(value, str) and value.strip() for value in columns):
        return None
    columns = [value.strip() for value in columns]
    if len(set(columns)) != len(columns):
        return None
    rows = [{column: _display(grid[row].get(key)) for key, column in zip(keys, columns)}
            for row in rest[:_SAMPLE_ROWS]]
    return {'name': name, 'columns': columns, 'preview': rows}


def _native_tables(blocks):
    sheets, tables = {}, []
    for block in blocks:
        location = block.get('native_location') or {}
        data = block.get('data') or {}
        sheet = location.get('sheet')
        coord = _CELL.fullmatch(location.get('cell') or '')
        if sheet and coord:
            rows = sheets.setdefault(sheet, {})
            row = int(coord[2])
            if row in rows or len(rows) <= _SAMPLE_ROWS:
                rows.setdefault(row, {})[coord[1]] = data.get('value')
        if 'table' not in block.get('content_types', []):
            continue
        grid = {}
        for cell in data.get('cells') or []:
            row = cell.get('row', cell.get('start_row_offset_idx'))
            col = cell.get('column', cell.get('start_col_offset_idx'))
            if isinstance(row, int) and isinstance(col, int) and 0 <= row <= _SAMPLE_ROWS:
                grid.setdefault(row, {})[col] = cell.get('text')
        table = _preview_table(f'표 {len(tables) + 1}', grid)
        if table:
            tables.append(table)
    for sheet, rows in sheets.items():
        table = _preview_table(sheet, rows, cell_order=lambda col: (len(col), col))
        if table:
            tables.append(table)
    return tables


def inspect_file(asset):
    analysis = analysis_of(asset)
    blocks = [block for block in (analysis or {}).get('blocks', []) if block.get('selected', True)]
    # A reviewed upload can contain discarded OCR alternatives, including all text.
    if analysis is not None:
        texts = (block.get('text', '') for block in blocks if 'cell' not in block.get('content_types', []))
    else:
        texts = asset.get('pages') or [asset.get('text', '')]

    snippets, fields = [], {}
    columns = asset.get('columns') or []
    for text in texts:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if len(snippets) < 4 and line[:160] not in snippets:
                snippets.append(line[:160])
            pair = labeled_value(line)
            if not pair:
                continue
            name, value = pair
            if name in columns:
                continue
            if name in fields:
                if fields[name] != value:
                    fields[name] = None
            elif len(fields) < _MAX_FIELDS:
                fields[name] = value

    tables = _native_tables(blocks)
    preview = [{column: _display(row.get(column)) for column in columns}
               for row in (asset.get('preview') or [])[:_SAMPLE_ROWS]]
    review = (asset.get('parser_metadata') or {}).get('document_review') or {}
    partial = asset.get('parser_status') in ('partial', 'failed') or bool((analysis or {}).get('review_required'))
    return {
        'id': asset['id'], 'name': asset.get('name', ''), 'kind': asset.get('kind', 'document'),
        'has_readable_text': bool(snippets or columns or tables or any(b.get('text', '').strip() for b in blocks)),
        'partial': partial and review.get('status') != 'completed',
        'columns': columns, 'preview': preview, 'tables': tables, 'snippets': snippets,
        'fields': [{'label': name, **({'value': _display(value)} if value is not None else {})}
                   for name, value in fields.items()],
    }
