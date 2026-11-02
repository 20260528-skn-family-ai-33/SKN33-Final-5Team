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
    ids=provided or [id for id in settings['file_ids'] or config['document_ids'] if supports(storage.asset(id),expected)]
    if not ids:
        raise ValueError('이번 실행에 사용할 '+('문서' if expected=='document' else '표 파일')+'를 연결하세요.')
    if tool=='read_document':return {'documents':document_chunks(storage,ids)}
    if len(ids)!=1:raise ValueError('표 읽기 노드는 한 파일을 선택해야 합니다. 파일을 하나만 연결하세요.')
    asset=storage.asset(ids[0])
    return {'table':{'rows':storage.rows(ids[0]),'columns':asset['columns'],'file_id':ids[0]}}
