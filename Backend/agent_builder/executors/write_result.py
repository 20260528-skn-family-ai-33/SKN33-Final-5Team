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
    write_result_node(ctx, settings.get('format') or config['execution_plan']['output_format'])
    if inputs['table'].get('review_report') is True:
        ctx['artifact']['name']='검수_보고서.'+(settings.get('format') or config['execution_plan']['output_format'])
        ctx['artifact']['review_report']=True
    return {'artifact': ctx['artifact']}
