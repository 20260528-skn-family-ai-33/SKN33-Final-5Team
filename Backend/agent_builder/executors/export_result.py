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
    from ..review_state import collect_reviews
    from ..reusable_workflows import REVIEW_EXPORT_ERROR,contains_review
    # Old pinned versions also run through this guard; they cannot bypass approval.
    if collect_reviews(state.get('outputs',{})) or contains_review(config):
        raise ValueError(REVIEW_EXPORT_ERROR)
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    if 'records' in inputs:
        rows = [{**r, 'evidence': json.dumps(r.get('evidence'), ensure_ascii=False)} for r in inputs['records']]
        ctx = {'rows': rows, 'columns': ['file_name', 'field', 'value', 'status', 'evidence'], 'trace': []}
        write_result_node(ctx, settings['format'])
        if inputs.get('answer') and settings['format'] == 'json':
            ctx['artifact']['content'] = json.dumps({'records': inputs['records'], 'user_confirmation': inputs['answer']}, ensure_ascii=False, indent=2)
        return {'artifact': ctx['artifact']}
    text=inputs.get('text') or inputs.get('answer')
    if not isinstance(text,str) or not text.strip():raise ValueError('저장할 텍스트 또는 추출 결과를 연결하세요.')
    if settings['format']=='json':
        try:parsed=json.loads(text)
        except (ValueError,TypeError):raise ValueError('JSON 결과가 올바른 JSON 문서가 아닙니다.') from None
        return {'artifact':{'name':'에이전트_결과.json','content':json.dumps(parsed,ensure_ascii=False,indent=2),'mime':'application/json; charset=utf-8'}}
    return {'artifact': {'name': '에이전트_결과.md', 'content': text, 'mime': 'text/markdown; charset=utf-8'}}
