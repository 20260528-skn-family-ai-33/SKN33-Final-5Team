from copy import deepcopy

from agent_builder.experiments.binding_repairs import repair_bindings
from agent_builder.models import ExecutionPlan
from agent_builder.test_workflow import node,bind,edge
from agent_builder.workflow import workflow_errors


def sample():
    return {'entry':'read','nodes':[node('read','read_table',[bind('file','read','table')],file_ids=['asset']),
            node('save','write_result',[bind('table','read','artifact')],format='json'),
            node('end','finish',[bind('artifact','save','text')])],
            'edges':[edge('read','save'),edge('save','end')]}


def test_unique_contract_repairs_are_logged_and_do_not_mutate_source():
    original=sample();unchanged=deepcopy(original)
    repaired,changes=repair_bindings(original,[{'id':'asset','kind':'table','columns':['a']}])
    assert original==unchanged
    assert len(changes)==3
    assert repaired['nodes'][0]['bindings']==[]
    assert repaired['nodes'][1]['bindings']==[bind('table','read','table')]
    assert repaired['nodes'][2]['bindings']==[bind('artifact','save','artifact')]
    assert repaired['edges']==original['edges']
    plan=ExecutionPlan(filters=[],sort=[],match='all',missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='json').model_dump()
    assert workflow_errors(repaired,['read_table','write_result','finish'],[{'id':'asset','kind':'table','columns':['a']}],plan)==[]


def test_known_wrong_type_output_is_never_rewritten():
    workflow={'entry':'a','nodes':[node('a','condition',condition_operator='empty'),
              node('b','write_result',[bind('table','a','text')],format='json')],
              'edges':[edge('a','b','yes')]}
    repaired,changes=repair_bindings(workflow,[])
    assert repaired==workflow and changes==[]


def test_ambiguous_output_alias_is_left_for_validation():
    workflow={'entry':'a','nodes':[node('a','condition',condition_operator='empty'),
              node('b','export_result',[bind('text','a','typo')],format='markdown')],
              'edges':[edge('a','b','yes')]}
    # condition has both text and answer (type=text), so no unique output exists.
    repaired,changes=repair_bindings(workflow,[])
    assert repaired==workflow and changes==[]


def test_unknown_nodes_and_non_adjacent_bindings_are_not_invented():
    workflow=sample();workflow['nodes'][0]['bindings']=[]
    workflow['nodes'][1]['bindings'][0]['node_id']='missing'
    workflow['nodes'][2]['bindings'][0]['node_id']='read'
    repaired,changes=repair_bindings(workflow,[{'id':'asset','kind':'table'}])
    assert repaired==workflow and changes==[]


def test_invalid_file_does_not_allow_dropping_reader_binding():
    workflow=sample();workflow['nodes']=workflow['nodes'][:1];workflow['edges']=[]
    repaired,changes=repair_bindings(workflow,[])
    assert repaired==workflow and changes==[]


def test_duplicate_node_ids_disable_all_repairs():
    workflow=sample();workflow['nodes'][1]['id']='read'
    repaired,changes=repair_bindings(workflow,[{'id':'asset','kind':'table'}])
    assert repaired==workflow and changes==[]
