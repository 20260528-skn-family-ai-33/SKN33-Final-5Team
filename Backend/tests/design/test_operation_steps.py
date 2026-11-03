from copy import deepcopy
import json
import pytest
from agent_builder.design.operation_steps import compile_workflow
from workflow_engine.workflow import workflow_errors
from tests.workflow_engine.test_workflow import node


def step(tool,settings=None,bindings=None):
    return {'tool':tool,'label':tool,'settings':settings or {},'bindings':bindings or []}


def test_ordered_two_reader_join_preserves_explicit_inputs_and_constants():
    args={'keys':['id'],'how':'left','cardinality':'many_to_one','null_keys':'error','max_rows':100}
    raw={'steps':[step('read_table',{'file_ids':['a']}),step('read_table',{'file_ids':['b']}),
         step('join_tables',{'operation':'join','arguments_json':json.dumps(args)},[
             {'input':'left','step':1,'output':'table'},{'input':'right','step':2,'output':'table'}]),
         step('write_result',{'format':'json'},[{'input':'table','step':3,'output':'table'}])]}
    original=deepcopy(raw);workflow=compile_workflow(raw)
    assert raw==original
    assert workflow['entry']=='step_1'
    assert [(e['source'],e['target']) for e in workflow['edges']]==[('step_1','step_2'),('step_2','step_3'),('step_3','step_4')]
    assert workflow['nodes'][2]['bindings']==[{'input':'left','node_id':'step_1','output':'table'},{'input':'right','node_id':'step_2','output':'table'}]
    assert json.loads(workflow['nodes'][2]['settings']['arguments_json'])==args
    # Expand compatibility defaults just as the conversation adapter does.
    for n in workflow['nodes']:n['settings']=node(n['id'],n['tool'],**n['settings'])['settings']
    resources=[{'id':'a','kind':'table','columns':['id']},{'id':'b','kind':'table','columns':['id']}]
    assert workflow_errors(workflow,['read_table','join_tables','write_result'],resources)==[]


@pytest.mark.parametrize('reference',[0,2,True,'1',None])
def test_ordered_bindings_reject_missing_self_future_or_untyped_references(reference):
    with pytest.raises(ValueError):compile_workflow({'steps':[step('scalar_calculate'),step('finish',bindings=[{'input':'text','step':reference,'output':'text'}])]})


def test_ordered_compiler_never_inserts_tools_or_allows_early_finish():
    assert len(compile_workflow({'steps':[step('scalar_calculate')]})['nodes'])==1
    with pytest.raises(ValueError):compile_workflow({'steps':[step('finish'),step('scalar_calculate')]})
    with pytest.raises(ValueError):compile_workflow({'steps':[step('condition')]})


def test_first_step_has_no_binding_surface_and_following_steps_keep_explicit_references():
    raw={'first_step':{'tool':'scalar_calculate','label':'계산','settings':{'operation':'evaluate','arguments_json':{'formula':'x*2','variables':{'x':3}}}},
         'next_steps':[step('finish',bindings=[{'input':'text','step':1,'output':'text'}])]}
    result=compile_workflow(raw)
    assert result['nodes'][0]['bindings']==[]
    assert result['nodes'][1]['bindings']==[{'input':'text','node_id':'step_1','output':'text'}]
    raw['first_step']['bindings']=[{'input':'variables','step':1,'output':'result'}]
    with pytest.raises(ValueError):compile_workflow(raw)
