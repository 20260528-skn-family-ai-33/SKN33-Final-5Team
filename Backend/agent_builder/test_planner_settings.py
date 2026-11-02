import copy
import pytest
from agent_builder.models import AgentDraft
from agent_builder.planner_settings import FIELDS,compact_settings_schema,expand_workflow_settings,compact_prompt_context
from agent_builder.registry import TOOLS

def test_all_registered_tools_have_compact_contract():
    assert set(FIELDS)=={tool['id'] for tool in TOOLS}
    schema=AgentDraft.model_json_schema()['$defs']['NodeSettings']
    join=compact_settings_schema(schema,'join_tables')
    assert set(join['properties'])=={'node_ref','join_keys','join_how','join_cardinality'}
    assert len(join['properties'])<len(schema['properties'])
    assert set(join['required'])==set(join['properties'])

def test_neutral_expansion_preserves_choices_and_does_not_invent_values():
    w={'nodes':[{'tool':'join_tables','settings':{'node_ref':None,'join_keys':['item'],'join_how':'left','join_cardinality':'many_to_one'}}]}
    result=expand_workflow_settings(copy.deepcopy(w))['nodes'][0]['settings']
    assert result['join_keys']==['item'] and result['join_how']=='left' and result['join_cardinality']=='many_to_one'
    assert result['file_ids']==[] and result['instructions'] is None
    with pytest.raises(ValueError):expand_workflow_settings({'nodes':[{'tool':'join_tables','settings':{'format':'csv'}}]})

def test_compact_prompt_preserves_zero_false_and_empty_path():
    data={'settings':{'json_path':[],'date_seconds':0,'include_whitespace':False,'text_replacement':'','query':None}}
    assert compact_prompt_context(data)=={'settings':{'json_path':[],'date_seconds':0,'include_whitespace':False,'text_replacement':''}}
