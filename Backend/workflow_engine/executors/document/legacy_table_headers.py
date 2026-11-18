"""Keep persisted legacy table headers from becoming exported data rows."""
from documents.resources.document_resources import (
    TABLE_HEADERS_CHECKED, analysis_of, match_table_headers, table_cell_layout, table_cells,
)

from .table_rows import COVERAGE_KEY, label


HEADER_SCOPE_ERROR = '이전 실행의 표 머리글을 확인할 수 없습니다. 원본을 연결해 새로 실행해 주세요.'
HEADER_ROW_ERROR = '이전 실행 결과에 표 머리글이 데이터 행으로 포함되어 있습니다. 새로 실행해 주세요.'
LEGACY_PARSERS = {'docling', 'mineru'}


def _stored_layout(storage, proof, layouts):
    file_id, block_id = proof.get('file_id'), proof.get('block_id')
    if storage is None or not file_id or not block_id:
        raise ValueError(HEADER_SCOPE_ERROR)
    engine = proof.get('parser_engine')
    key = file_id, block_id, engine
    if key not in layouts:
        try:
            analysis = analysis_of(storage.asset(file_id)) or {}
            blocks = [block for block in analysis.get('blocks', []) if block.get('id') == block_id
                      and block.get('engine') == engine and 'table' in block.get('content_types', [])]
            layout = table_cell_layout(table_cells(blocks[0])) if len(blocks) == 1 else []
        except (TypeError, ValueError):
            raise ValueError(HEADER_SCOPE_ERROR) from None
        if not layout:
            raise ValueError(HEADER_SCOPE_ERROR)
        layouts[key] = layout
    return layouts[key]


def restore_legacy_table_headers(parts, storage):
    if storage is None:
        return parts
    layouts = {}
    restored = []
    for part in parts:
        if (part.get('parser_engine') in LEGACY_PARSERS and part.get('table_cells')
                and not part.get(TABLE_HEADERS_CHECKED)):
            cells = part['table_cells']
            if part['parser_engine'] == 'mineru':
                cells = match_table_headers(cells, _stored_layout(storage, part, layouts))
                if cells is None:
                    raise ValueError(HEADER_SCOPE_ERROR)
            part = {**part, 'table_cells': cells, TABLE_HEADERS_CHECKED: True}
        restored.append(part)
    return restored


def validate_legacy_record_headers(records, storage):
    if storage is None:
        return
    layouts = {}
    for record in records:
        evidence = record.get('evidence')
        if record.get(COVERAGE_KEY) != 'complete' or not isinstance(evidence, dict):
            continue
        for proof in evidence.values():
            if (not isinstance(proof, dict) or proof.get('parser_engine') not in LEGACY_PARSERS
                    or proof.get(TABLE_HEADERS_CHECKED)
                    or 'table_row' not in proof):
                continue
            row, column = proof['table_row'], proof.get('table_column')
            layout = _stored_layout(storage, proof, layouts)
            if any(position[0] == row and marked for position, marked in layout):
                raise ValueError(HEADER_ROW_ERROR)
            if not any(position[:2] == (row, column) and position[-1] == label(proof.get('quote'))
                       for position, _ in layout):
                raise ValueError(HEADER_SCOPE_ERROR)
