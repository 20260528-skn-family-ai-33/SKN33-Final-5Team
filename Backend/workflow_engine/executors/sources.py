"""Shared source resolution for the registered document/table entrypoints."""
from documents.resources.document_resources import (
    EMPTY_DOCUMENTS, analysis_of, chunks_for_asset, document_scope, saved_input, supports,
    table_cell_layout, table_cells, select_table_parts,
)

DOCUMENT_TABLE_ERROR = '선택한 표를 원본 문서에서 확인할 수 없습니다. 문서 미리보기에서 표를 다시 선택해 주세요.'


def replacement_files(readers, provided, storage):
    """Match all source slots before executing any reader, without merging inputs."""
    replacements, owners = {}, {}
    for reader in readers:
        selected = reader['settings'].get('file_ids') or []
        if not selected:
            raise ValueError('여러 읽기 노드의 실행 파일을 구분할 수 없습니다. 각 노드에 파일을 지정하세요.')
        for original in selected:
            if original in replacements:
                continue  # Explicit reuse of the same original remains supported.
            matches = ([original] if original in provided else
                       [i for i in provided if storage.asset(i).get('name') == saved_input(storage,original).get('name')])
            if len(matches) != 1:
                raise ValueError('새 실행 파일을 읽기 노드에 유일하게 연결할 수 없습니다. 원래 파일 이름으로 첨부하거나 파일 선택을 수정하세요.')
            replacement = matches[0]
            if replacement in owners and owners[replacement] != original:
                raise ValueError('서로 다른 원본에 같은 새 실행 파일이 연결됩니다. 각 읽기 노드의 파일 선택을 수정하세요.')
            replacements[original] = replacement
            owners[replacement] = original
    return replacements


def source_file_ids(node, file_ids, storage, config, outputs=None, *, allow_deferred=False):
    """Select physical inputs and validate explicit document table replacements."""
    ids, _ = _resolve_source(node, file_ids, storage, config, outputs, allow_deferred=allow_deferred)
    return ids


def _resolve_source(node, file_ids, storage, config, outputs, *, allow_deferred=False):
    tool=node['tool'];settings=node['settings']
    expected='document' if tool=='read_document' else 'table'
    provided=[id for id in file_ids if supports(storage.asset(id),expected)]
    selected=settings.get('file_ids') or []
    if tool == 'read_document' and settings.get('document_table_id') is not None:
        table_id = settings['document_table_id']
        if len(selected) != 1:
            raise ValueError(DOCUMENT_TABLE_ERROR)
        if file_ids and selected[0] not in provided:
            if len(provided) == 1 and len([n for n in (config.get('workflow') or {}).get('nodes', [])
                                          if n['tool'] == 'read_document']) <= 1:
                return provided, _replacement_table_id(storage.asset(selected[0]), table_id, storage.asset(provided[0]))
            raise ValueError(DOCUMENT_TABLE_ERROR)
        return selected, table_id
    other_readers=[n for n in (config.get('workflow') or {}).get('nodes',[])
                   if n['tool'] in ('read_table','read_document') and n['tool']!=tool]
    if file_ids and not provided and not other_readers:
        raise ValueError('새 실행 자료의 종류가 읽기 노드와 맞지 않습니다. 사용할 파일을 다시 선택하세요.')
    readers=[n for n in (config.get('workflow') or {}).get('nodes',[])
             if n['tool']==tool and not n['settings'].get('operation')]
    if provided and len(readers)<=1:
        # Preserve an explicit Table source when a run also supplies unrelated
        # uploads. A single supplied file remains an intentional replacement.
        if tool=='read_table' and selected and len(provided)>1:
            replacements=replacement_files([node],provided,storage)
            ids=[replacements[old] for old in selected]
        else:ids=provided
    elif provided:
        # Never bind several source slots by upload order or silently reuse old data.
        # Existing selected IDs are exact; replacements must match unique filenames.
        from workflow_engine.execution_path import execution_path
        workflow=config.get('workflow') or {}
        path=execution_path(workflow,outputs)
        if node['id'] not in {n['id'] for n in path}:
            # Preflight can describe a future branch but must not require its sibling.
            path=execution_path(workflow,outputs,start=node['id'])
        active={n['id'] for n in path}
        replacements = replacement_files([r for r in readers if r['id'] in active], provided, storage)
        ids = [replacements[old] for old in selected]
    else:
        ids=[i for i in selected or config['document_ids'] if supports(saved_input(storage,i),expected)]
    if not ids:
        if allow_deferred and not file_ids and not selected:
            return [], None
        raise ValueError('이번 실행에 사용할 '+('문서' if expected=='document' else '표 파일')+'를 연결하세요.')
    if tool=='read_table' and len(ids)!=1:raise ValueError('표 읽기 노드는 한 파일을 선택해야 합니다. 파일을 하나만 연결하세요.')
    return ids, None


def read_source(node,state,storage,config,external_port):
    # Preserve binding lookup order/errors for direct executor callers.
    _inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    table_id = node['settings'].get('document_table_id') if node['tool'] == 'read_document' else None
    external_inputs = state.get('external_inputs', {})
    if external_port in external_inputs:
        if table_id is not None:
            raise ValueError(DOCUMENT_TABLE_ERROR)
        scope = document_scope(external_inputs) if external_port == 'documents' else {}
        return {external_port: external_inputs[external_port], **scope}
    ids, table_id = _resolve_source(node, state['file_ids'], storage, config, state.get('outputs'))
    if node['tool'] == 'read_document':
        parts = []
        empty_documents = []
        for identifier in ids:
            asset = storage.asset(identifier)
            chunks = list(chunks_for_asset(asset))
            if not chunks:
                empty_documents.append(asset['name'])
            parts.extend(chunks)
        if table_id is not None:
            parts = select_table_parts(parts, table_id)
            if not any(part.get('table_cells') for part in parts):
                raise ValueError(DOCUMENT_TABLE_ERROR)
        result = {'documents': parts}
        if empty_documents:
            result[EMPTY_DOCUMENTS] = empty_documents
        return result
    asset=storage.asset(ids[0])
    return {'table':{'rows':storage.rows(ids[0]),'columns':asset['columns'],'file_id':ids[0]}}


def _contiguous_extent(ranges):
    ranges = sorted(ranges)
    start = end = ranges[0][0]
    for left, right in ranges:
        if left > end:
            return None
        end = max(end, right)
    return start, end


def _table_structure(block):
    try:
        layout = table_cell_layout(table_cells(block))
    except (TypeError, ValueError):
        return (), None
    shape = tuple(position for position, _ in layout)
    headers = [position for position, is_header in layout if is_header]
    return shape, _header_signature(shape, headers)


def _header_signature(shape, headers):
    if not headers:
        return None
    header_rows = _contiguous_extent((row, row + height) for row, _, height, _, _ in headers)
    labelled_columns = [(column, column + width) for _, column, _, width, name in headers if name]
    if header_rows is None or not labelled_columns:
        return None
    all_columns = _contiguous_extent((column, column + width) for _, column, _, width, _ in shape)
    if all_columns is None or _contiguous_extent(labelled_columns) != all_columns:
        return None
    end = header_rows[1]
    if not any(row + height == end and name for row, _, height, _, name in headers):
        return None
    leading = tuple(entry for entry in shape if entry[0] < end)
    if any(row + height > end for row, _, height, _, _ in leading):
        return None
    return leading


def _document_tables(asset):
    analysis = analysis_of(asset) or {}
    review = (asset.get('parser_metadata') or {}).get('document_review') or {}
    if analysis.get('review_required') and review.get('status') != 'completed':
        return []
    return [block for block in analysis.get('blocks', []) if block.get('selected', True)
            and 'table' in block.get('content_types', [])]


def _replacement_table_id(original, table_id, replacement):
    """Rebind to a unique, parser-confirmed header and merge structure."""
    original_tables = [block for block in _document_tables(original) if block.get('id') == table_id]
    expected = _table_structure(original_tables[0])[1] if len(original_tables) == 1 else None
    if expected is None:
        raise ValueError(DOCUMENT_TABLE_ERROR)
    error = '새 문서에서 같은 열 구조의 표를 하나로 확정할 수 없습니다. 미리보기에서 사용할 표를 다시 선택해 주세요.'
    header_end = max(row + height for row, _, height, _, _ in expected)
    candidates = []
    for block in _document_tables(replacement):
        shape, signature = _table_structure(block)
        if signature == expected:
            candidates.append(block.get('id'))
        elif signature is None and tuple(entry for entry in shape if entry[0] < header_end) == expected:
            # An unconfirmed table with the same leading cells can also be the requested table.
            raise ValueError(error)
    if len(candidates) != 1 or candidates[0] is None:
        raise ValueError(error)
    return candidates[0]
