"""Compatibility views of routed document assets for existing workflow tools."""
import json


TABLE_SCOPE_INCOMPLETE = 'table_scope_incomplete'
EMPTY_DOCUMENTS = '_empty_documents'
TABLE_HEADERS_CHECKED = 'table_headers_checked'


def document_scope(output):
    """Metadata that must travel with documents even when the chunk list is empty."""
    return {key: output[key] for key in (TABLE_SCOPE_INCOMPLETE, EMPTY_DOCUMENTS) if key in output}


def input_document_scope(node, outputs):
    source = next((binding['node_id'] for binding in node['bindings']
                   if binding['input'] == binding['output'] == 'documents'), None)
    return document_scope(outputs.get(source, {}))


class RemovedUpload(ValueError):
    """A saved reader may still need the input slot's name, type and columns."""
    def __init__(self, record):
        self.schema = {k: record[k] for k in ('id', 'name', 'kind', 'columns', 'input_kinds') if k in record}
        self.schema['deleted'] = True
        pending = 'pending_delete' in record
        super().__init__('삭제 중인 자료입니다.' if pending else
                         f"‘{record.get('name', '참고 파일')}’ 원본이 삭제됐습니다. 새 파일을 연결해 실행하세요.")


def saved_input(storage, id):
    try:
        return storage.asset(id)
    except RemovedUpload as error:
        return error.schema


def available_inputs(storage, ids):
    return [asset for asset in (saved_input(storage, id) for id in ids) if not asset.get('deleted')]


def analysis_of(asset):
    metadata = asset.get('parser_metadata') or {}
    analysis = metadata.get('document_analysis') or {}
    return analysis if analysis.get('contract_version') == 'a4b.document_analysis.v1' else None


def supports(asset, kind):
    if asset.get('deleted'):
        return kind in asset.get('input_kinds', [asset.get('kind')])
    if asset.get('kind') == kind:
        return True
    return kind == 'document' and bool(analysis_of(asset)) and bool(asset.get('text', '').strip())


def table_cell_layout(cells):
    from workflow_engine.executors.document.table_rows import _grid, label

    layout = []
    for row, row_cells in _grid(cells):
        for column, cell in sorted(row_cells.items()):
            height, width = (1 if cell.get(key) is None else cell[key] for key in ('row_span', 'col_span'))
            if any(type(span) is not int or span < 1 for span in (height, width)):
                raise ValueError('표 셀의 행·열 병합 범위를 확인할 수 없습니다.')
            position = (row, column, height, width, label(cell.get('text')))
            layout.append((position, cell.get('column_header') is True))
    return layout


def table_cells(block):
    """Recover legacy MinerU column headers only from matching saved HTML."""
    data = block.get('data') or {}
    cells = data.get('cells') or []
    if not cells or block.get('engine') != 'mineru' or not data.get('html'):
        return cells
    from documents.analysis.tables import from_html

    try:
        if not all(isinstance(cell, dict) and 'column_header' not in cell for cell in cells):
            return cells
        restored = match_table_headers(cells, table_cell_layout(from_html(data['html'])['cells']))
    except (TypeError, ValueError):
        return cells
    return cells if restored is None else restored


def match_table_headers(cells, reference_layout):
    """Transfer header markers without changing the matched cells' evidence."""
    if [position for position, _ in reference_layout] != [position for position, _ in table_cell_layout(cells)]:
        return None
    headers = {(row, column) for (row, column, _, _, _), marked in reference_layout if marked}
    return [dict(cell, column_header=True)
            if (cell.get('row', cell.get('start_row_offset_idx')),
                cell.get('column', cell.get('start_col_offset_idx'))) in headers else cell
            for cell in cells]


def document_review_scope(analysis):
    """Compute global/affected scope once per document, including unknown scopes."""
    if not analysis.get('review_required'):
        return False, set()
    reasons = set(analysis.get('review_reasons') or []) | set(analysis.get('warnings') or [])
    scopes = analysis.get('review_scopes')
    if not reasons or not isinstance(scopes, dict):
        return True, set()
    ids = {b['id'] for b in analysis.get('blocks', [])}
    affected_ids = set()
    for reason in reasons:
        affected = scopes.get(reason)
        if not isinstance(affected, list) or not affected or any(not isinstance(i, str) or i not in ids for i in affected):
            return True, set()
        affected_ids.update(affected)
    return False, affected_ids


def selected_table_needs_review(analysis, block, *, scope=None):
    """Selection never clears global, local, parent, or unknown review errors."""
    if block.get('status') == 'review_required' or (block.get('data', {}).get('table_quality') or {}).get('issues'):
        return True
    global_review, affected = document_review_scope(analysis) if scope is None else scope
    return global_review or block['id'] in affected or block.get('parent_object_id') in affected


def select_table_parts(parts, table_id):
    """Apply narrow review scope only for an explicit table selection."""
    return [{**part, 'review_required': part.get('selected_table_review_required', part.get('review_required', False))}
            for part in parts if part.get('block_id') == table_id]


def chunks_for_asset(asset):
    if not supports(asset, 'document'):
        raise ValueError('문서 읽기에 사용할 본문 또는 분석 결과가 없습니다.')
    chunks = list(_asset_chunks(asset))
    for chunk in chunks:
        chunk['document_chunk_count'] = len(chunks)
        yield chunk


def _asset_chunks(asset):
    analysis = analysis_of(asset)
    if analysis:
        review=(asset.get('parser_metadata') or {}).get('document_review') or {}
        review_scope = document_review_scope(analysis)
        blocks = [block for block in analysis['blocks'] if block.get('selected', True)]
        structured_parents = {block['parent_object_id'] for block in blocks
                              if block.get('parent_object_id') and 'table' in block.get('content_types', [])
                              and (block.get('data') or {}).get('cells')}
        coverage_only = []
        has_text = False
        for block in blocks:
            block_text = block.get('text', '')
            is_table = 'table' in block.get('content_types', [])
            cells = table_cells(block) if is_table else None
            if not block_text.strip() and cells:
                block_text = '\n'.join(str(cell.get('text') or '') for cell in cells)
            empty = not block_text.strip()
            # A layout region handled by a selected child is the same table.
            if empty and (not is_table or block['engine'] == 'layout' and block['id'] in structured_parents):
                continue
            location = block['native_location']
            source = f"{asset['id']}:{block['id']}:"
            provenance = dict(file_id=asset['id'], file_name=asset['name'], page=location.get('page'),
                              source_text_length=len(block_text),
                              block_id=block['id'], native_location=location, bbox=block.get('bbox'),
                              parser_engine=block['engine'], review_required=analysis['review_required'] and review.get('status')!='completed',
                              selected_table_review_required=selected_table_needs_review(analysis, block, scope=review_scope) and review.get('status')!='completed',
                              document_review_status=review.get('status','pending'), document_review_revision=review.get('revision',0))
            if empty:
                coverage_only.append(dict(source_id=source + '0', offset=0, text='', table_cells=cells, **provenance))
                continue
            for start in range(0, len(block_text), 1400):
                text = block_text[start:start + 1800]
                if not text.strip():
                    continue
                part = dict(source_id=source + str(start), offset=start, text=text, **provenance)
                if cells is not None:
                    part['table_cells'] = cells
                    if cells:
                        part[TABLE_HEADERS_CHECKED] = True
                    cells = None
                has_text = True
                yield part
        # Keep unresolved empty tables visible to extraction without making an empty document readable.
        if has_text:
            yield from coverage_only
        return
    for page, text in enumerate(asset.get('pages') or [asset.get('text', '')], 1):
        for start in range(0, len(text), 1400):
            chunk = text[start:start + 1800]
            if chunk.strip():
                yield dict(source_id=f"{asset['id']}:{page}:{start}", file_id=asset['id'], file_name=asset['name'],
                           page=page if asset.get('format') == 'pdf' else None, offset=start, text=chunk,
                           source_text_length=len(text))


def locator(chunk):
    if chunk.get('native_location'):
        return json.dumps(chunk['native_location'], ensure_ascii=False, sort_keys=True) + ';offset=' + str(chunk['offset'])
    return f"page={chunk['page']};offset={chunk['offset']}"

def document_chunks(storage,ids):
    return [chunk for id in ids for chunk in chunks_for_asset(storage.asset(id))]
