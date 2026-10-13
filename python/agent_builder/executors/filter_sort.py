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
    ctx = {**inputs['table'], 'trace': []}
    plan = settings.get('execution_plan') or config['execution_plan']
    filter_sort_node(ctx, plan['filters'], plan['sort'], plan['match'], plan['missing_policy'], plan['duplicate_policy'], plan['duplicate_field'])
    result = {k: ctx[k] for k in ['rows', 'columns', 'file_id']}
    if inputs['table'].get('review_report') is True:
        result['review_report'] = True
    return {'table': result}
