"""Shared source resolution for the registered document/table entrypoints."""
from documents.resources.document_resources import document_chunks
from documents.resources.document_resources import supports


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
                       [i for i in provided if storage.asset(i).get('name') == storage.asset(original).get('name')])
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
    other_readers=[n for n in (config.get('workflow') or {}).get('nodes',[])
                   if n['tool'] in ('read_table','read_document') and n['tool']!=tool]
    if file_ids and not provided and not other_readers:
        raise ValueError('새 실행 자료의 종류가 읽기 노드와 맞지 않습니다. 사용할 파일을 다시 선택하세요.')
    selected=settings.get('file_ids') or []
    readers=[n for n in (config.get('workflow') or {}).get('nodes',[])
             if n['tool']==tool and not n['settings'].get('operation')]
    if provided and len(readers)<=1:
        # A new run input replaces the saved input for a single reader.
        ids=provided
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
        ids=[i for i in selected or config['document_ids'] if supports(storage.asset(i),expected)]
    if not ids:
        raise ValueError('이번 실행에 사용할 '+('문서' if expected=='document' else '표 파일')+'를 연결하세요.')
    if tool=='read_table' and len(ids)!=1:raise ValueError('표 읽기 노드는 한 파일을 선택해야 합니다. 파일을 하나만 연결하세요.')
    return ids


def read_source(node,state,storage,config,external_port):
    # Preserve binding lookup order/errors for direct executor callers.
    _inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    if external_port in state.get('external_inputs',{}):
        return {external_port:state['external_inputs'][external_port]}
    ids=source_file_ids(node,state['file_ids'],storage,config,state.get('outputs'))
    if node['tool']=='read_document':return {'documents':document_chunks(storage,ids)}
    asset=storage.asset(ids[0])
    return {'table':{'rows':storage.rows(ids[0]),'columns':asset['columns'],'file_id':ids[0]}}
