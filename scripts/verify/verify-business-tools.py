"""Offline audit of the 56-label catalogue -> node schema -> registered executors."""
import importlib
import json
import sys
from pathlib import Path
from typing import get_args

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'Backend'))
from contracts.models import WorkflowNode
from workflow_engine.catalog.canonical import BUSINESS_IDS,CONTROL_IDS,OPERATIONS,contract
from workflow_engine.catalog.registry import TOOLS,EXECUTORS,PORTS


def audit():
    registered={t['id']:t for t in TOOLS}
    schema=set(get_args(WorkflowNode.model_fields['tool'].annotation))
    expected=set(BUSINESS_IDS)|set(CONTROL_IDS)
    assert len(BUSINESS_IDS)==56 and len(set(BUSINESS_IDS))==56
    assert set(registered)==schema==set(EXECUTORS)==set(PORTS)==expected
    rows=[]
    for identifier in BUSINESS_IDS:
        tool=registered[identifier];module,name=tool['module_path'].split(':')
        assert callable(getattr(importlib.import_module(module),name))
        operations=[contract(identifier,spec) for spec in OPERATIONS[identifier]]
        assert operations
        rows.append({'id':identifier,'node_registered':True,'executor_importable':True,
                     'operations':[{'id':o['id'],'status':o['status']} for o in operations]})
    return {'business_tools':56,'control_compatibility_tools':len(CONTROL_IDS),
            'registered_node_types':len(registered),'missing_business_tools':[],
            'live_model_quality_verified':False,'live_external_accounts_verified':False,'tools':rows}


if __name__=='__main__':print(json.dumps(audit(),ensure_ascii=False,indent=2))
