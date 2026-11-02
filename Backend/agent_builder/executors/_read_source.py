"""Shared source resolution for the registered document/table entrypoints."""
from ..workflow_runtime import document_chunks
from ..document_resources import supports


def read_source(node,state,storage,config,external_port):
    tool=node['tool'];settings=node['settings']
    # Preserve binding lookup order/errors for direct executor callers.
    _inputs={b['input']:state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    if external_port in state.get('external_inputs',{}):
        return {external_port:state['external_inputs'][external_port]}
    expected='document' if tool=='read_document' else 'table'
    provided=[id for id in state['file_ids'] if supports(storage.asset(id),expected)]
    selected=[id for id in settings['file_ids'] if id in provided]
    if expected=='table' and provided:
        referenced={id for n in (config.get('workflow') or {}).get('nodes',[]) if n.get('tool')=='read_table'
                    for id in n.get('settings',{}).get('file_ids',[])}
        if len(referenced)>1 and not referenced.issubset(provided):
            raise ValueError('여러 표 워크플로의 각 입력 파일을 모두 연결하세요. 새 파일은 노드별로 다시 지정해야 합니다.')
    # When multiple execution files are supplied, never guess which input a
    # particular reader means. Its explicit selected resource must be present.
    if expected=='table' and len(provided)>1:
        if len(selected)!=1:raise ValueError('여러 표 중 각 읽기 노드에 사용할 파일을 명시하세요.')
        ids=selected
    else:
        ids=provided or [id for id in settings['file_ids'] or config['document_ids'] if supports(storage.asset(id),expected)]
    if not ids:
        raise ValueError('이번 실행에 사용할 '+('문서' if expected=='document' else '표 파일')+'를 연결하세요.')
    if tool=='read_document':return {'documents':document_chunks(storage,ids)}
    if len(ids)!=1:raise ValueError('표 읽기 노드는 한 파일을 선택해야 합니다. 파일을 하나만 연결하세요.')
    asset=storage.asset(ids[0])
    return {'table':{'rows':storage.rows(ids[0]),'columns':asset['columns'],'file_id':ids[0]}}
