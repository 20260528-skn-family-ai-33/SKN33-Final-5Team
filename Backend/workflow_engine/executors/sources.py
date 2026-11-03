"""Shared source resolution for the registered document/table entrypoints."""
from documents.resources.document_resources import document_chunks
from documents.resources.document_resources import supports


def read_source(node,state,storage,config,external_port):
    tool=node['tool'];settings=node['settings']
    # Preserve binding lookup order/errors for direct executor callers.
    _inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    if external_port in state.get('external_inputs',{}):
        return {external_port:state['external_inputs'][external_port]}
    expected='document' if tool=='read_document' else 'table'
    provided=[id for id in state['file_ids'] if supports(storage.asset(id),expected)]
    other_readers=[n for n in (config.get('workflow') or {}).get('nodes',[])
                   if n['tool'] in ('read_table','read_document') and n['tool']!=tool]
    if state.get('file_ids') and not provided and not other_readers:
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
        ids=[]
        if not selected:
            raise ValueError('여러 읽기 노드의 실행 파일을 구분할 수 없습니다. 각 노드에 파일을 지정하세요.')
        for old in selected:
            matches=([old] if old in provided else
                     [i for i in provided if storage.asset(i).get('name')==storage.asset(old).get('name')])
            if len(matches)!=1:
                raise ValueError('새 실행 파일을 읽기 노드에 유일하게 연결할 수 없습니다. 원래 파일 이름으로 첨부하거나 파일 선택을 수정하세요.')
            ids.extend(matches)
    else:
        ids=[i for i in selected or config['document_ids'] if supports(storage.asset(i),expected)]
    if not ids:
        raise ValueError('이번 실행에 사용할 '+('문서' if expected=='document' else '표 파일')+'를 연결하세요.')
    if tool=='read_document':return {'documents':document_chunks(storage,ids)}
    if len(ids)!=1:raise ValueError('표 읽기 노드는 한 파일을 선택해야 합니다. 파일을 하나만 연결하세요.')
    asset=storage.asset(ids[0])
    return {'table':{'rows':storage.rows(ids[0]),'columns':asset['columns'],'file_id':ids[0]}}
