import json,re
from pathlib import Path
from pydantic import BaseModel,ConfigDict
from langgraph.types import interrupt
from deepagents import create_deep_agent
from langchain_core.tools import tool as registered_tool
from langchain.agents.structured_output import ProviderStrategy
from langchain.agents.middleware import wrap_model_call
from ..workflow_runtime import Fact,Facts,SearchTerms,GroundedAnswer,Citation,Decision,InputAssessment,document_chunks,original_quote
from ..tools.filter_sort import filter_sort_node
from ..tools.write_result import write_result_node

def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    if 'table' in state.get('external_inputs',{}):return {'table':state['external_inputs']['table']}
    expected = 'document' if tool == 'read_document' else 'table'
    provided = [id for id in state['file_ids'] if storage.asset(id)['kind'] == expected]
    ids = provided or [id for id in settings['file_ids'] or config['document_ids'] if storage.asset(id)['kind'] == expected]
    if not ids:
        raise ValueError('이번 실행에 사용할 ' + ('문서' if expected == 'document' else '표 파일') + '를 연결하세요.')
    if tool == 'read_document':
        return {'documents': document_chunks(storage, ids)}
    if len(ids) != 1:
        raise ValueError('표 읽기 노드는 한 파일을 선택해야 합니다. 파일을 하나만 연결하세요.')
    a = storage.asset(ids[0])
    return {'table': {'rows': storage.rows(ids[0]), 'columns': a['columns'], 'file_id': ids[0]}}
