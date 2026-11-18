"""Shared source resolution for the registered document/table entrypoints."""
from documents.resources.document_resources import document_chunks
from documents.resources.document_resources import saved_input, supports

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


def source_file_ids(node,file_ids,storage,config,outputs=None):
    """Shared file selection for preflight and execution; never reads file rows."""
    tool=node['tool'];settings=node['settings']
    expected='document' if tool=='read_document' else 'table'
    provided=[id for id in file_ids if supports(storage.asset(id),expected)]
    selected=settings.get('file_ids') or []
    if tool == 'read_document' and settings.get('document_table_id') is not None:
        if len(selected) != 1:
            raise ValueError(DOCUMENT_TABLE_ERROR)
        if file_ids and selected[0] not in provided:
            if len(provided) == 1 and len([n for n in (config.get('workflow') or {}).get('nodes', [])
                                          if n['tool'] == 'read_document']) <= 1:
                replacement_table_id(storage, selected[0], settings['document_table_id'], document_chunks(storage, provided))
                return provided
            raise ValueError(DOCUMENT_TABLE_ERROR)
        return selected
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
        raise ValueError('이번 실행에 사용할 '+('문서' if expected=='document' else '표 파일')+'를 연결하세요.')
    if tool=='read_table' and len(ids)!=1:raise ValueError('표 읽기 노드는 한 파일을 선택해야 합니다. 파일을 하나만 연결하세요.')
    return ids


def read_source(node,state,storage,config,external_port):
    # Preserve binding lookup order/errors for direct executor callers.
    _inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    table_id = node['settings'].get('document_table_id') if node['tool'] == 'read_document' else None
    if external_port in state.get('external_inputs',{}):
        if table_id is not None:
            raise ValueError(DOCUMENT_TABLE_ERROR)
        return {external_port:state['external_inputs'][external_port]}
    ids=source_file_ids(node,state['file_ids'],storage,config,state.get('outputs'))
    if node['tool'] == 'read_document':
        parts = document_chunks(storage, ids)
        if table_id is not None:
            original_ids = node['settings'].get('file_ids') or []
            if ids != original_ids:
                table_id = replacement_table_id(storage, original_ids[0], table_id, parts)
            parts = [part for part in parts if part.get('block_id') == table_id]
            if not any(part.get('table_cells') for part in parts):
                raise ValueError(DOCUMENT_TABLE_ERROR)
        return {'documents': parts}
    asset=storage.asset(ids[0])
    return {'table':{'rows':storage.rows(ids[0]),'columns':asset['columns'],'file_id':ids[0]}}


def replacement_table_id(storage, original_id, table_id, parts):
    """Rebind one replacement document by a unique full header, never by block ID."""
    from workflow_engine.executors.document.table_rows import _grid, label, _unit_cell
    original = [part for part in document_chunks(storage, [original_id])
                if part.get('block_id') == table_id and part.get('table_cells')]
    def signature(part):
        grid = _grid(part.get('table_cells') or [])
        if not grid or part.get('review_required'):
            return None
        columns = {column for _, row in grid for column in row}
        header_end = next((number for number, row in grid
                           if set(row) == columns and all(_unit_cell(cell) and label(cell.get('text')) for cell in row.values())
                           and len({label(cell.get('text')) for cell in row.values()}) == len(row)), None)
        if header_end is None:
            return None
        return tuple((number, column, label(cell.get('text')), cell.get('row_span') or 1, cell.get('col_span') or 1)
                     for number, row in grid if number <= header_end for column, cell in sorted(row.items()))
    if len(original) != 1 or not (expected := signature(original[0])):
        raise ValueError(DOCUMENT_TABLE_ERROR)
    candidates = {part.get('block_id') for part in parts if part.get('table_cells') and signature(part) == expected}
    if len(candidates) != 1 or None in candidates:
        raise ValueError('새 문서에서 같은 열 구조의 표를 하나로 확정할 수 없습니다. 미리보기에서 사용할 표를 다시 선택해 주세요.')
    return candidates.pop()
