"""Compatibility views of routed document assets for existing workflow tools."""
import json


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


def chunks_for_asset(asset):
    if not supports(asset, 'document'):
        raise ValueError('문서 읽기에 사용할 본문 또는 분석 결과가 없습니다.')
    analysis = analysis_of(asset)
    if analysis:
        review=(asset.get('parser_metadata') or {}).get('document_review') or {}
        blocks = [block for block in analysis['blocks'] if block.get('selected', True)]
        structured_parents = {block['parent_object_id'] for block in blocks
                              if block.get('parent_object_id') and 'table' in block.get('content_types', [])
                              and (block.get('data') or {}).get('cells')}
        coverage_only = []
        has_text = False
        for block in blocks:
            block_text = block.get('text', '')
            is_table = 'table' in block.get('content_types', [])
            cells = ((block.get('data') or {}).get('cells') or []) if is_table else None
            if not block_text.strip() and cells:
                block_text = '\n'.join(str(cell.get('text') or '') for cell in cells)
            empty = not block_text.strip()
            # A layout region handled by a selected child is the same table.
            if empty and (not is_table or block['engine'] == 'layout' and block['id'] in structured_parents):
                continue
            location = block['native_location']
            source = f"{asset['id']}:{block['id']}:"
            provenance = dict(file_id=asset['id'], file_name=asset['name'], page=location.get('page'),
                              block_id=block['id'], native_location=location, bbox=block.get('bbox'),
                              parser_engine=block['engine'], review_required=analysis['review_required'] and review.get('status')!='completed',
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
                           page=page if asset.get('format') == 'pdf' else None, offset=start, text=chunk)


def locator(chunk):
    if chunk.get('native_location'):
        return json.dumps(chunk['native_location'], ensure_ascii=False, sort_keys=True) + ';offset=' + str(chunk['offset'])
    return f"page={chunk['page']};offset={chunk['offset']}"

def document_chunks(storage,ids):
    return [chunk for id in ids for chunk in chunks_for_asset(storage.asset(id))]
