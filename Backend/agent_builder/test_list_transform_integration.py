"""Synthetic JSON inputs exercising the actual registered local operations."""
import json
import pytest
from agent_builder.test_workflow import node,bind,edge
from agent_builder.workflow import workflow_errors
from agent_builder.workflow_runtime import execute_node,compile_workflow

def run(text,op,index=None):
    n=node('list','list_transform',[bind('text','source','text')],list_operation=op,list_index=index)
    return execute_node(n,{'outputs':{'source':{'text':text}}},None,{},None)['text']

def test_select_and_count_existing_local_operations():
    assert json.loads(run('[{"x":1},{"x":2}]','select',1))=={'x':2}
    assert json.loads(run('[1,2,3]','count'))==3
    assert json.loads(run('[]','count'))==0

@pytest.mark.parametrize('text,op,index',[("{}",'count',None),('[1]','select',1),('[1]','select',-1),('[1]','select',True),('[NaN]','count',None),('[1e999]','count',None),('[{"x":1,"x":2}]','count',None)])
def test_rejects_non_array_invalid_index_and_ambiguous_json(text,op,index):
    with pytest.raises(ValueError):run(text,op,index)

def test_composed_json_lookup_list_count_and_saved_artifact():
    nodes=[node('source','agent_node'),
           node('json','json_transform',[bind('text','source','text')],json_operation='lookup',json_path=['items']),
           node('list','list_transform',[bind('text','json','text')],list_operation='count'),
           node('save','export_result',[bind('text','list','text')],format='json')]
    workflow={'entry':'source','nodes':nodes,'edges':[edge(a['id'],b['id']) for a,b in zip(nodes,nodes[1:])]}
    config={'workflow':workflow,'tool_ids':['agent_node','json_transform','list_transform','export_result'],'document_ids':[],'execution_plan':{'output_format':'json'}}
    assert not workflow_errors(workflow,config['tool_ids'],[])
    def executor(n,state,storage,config,llm):
        return {'text':'{"items":["a","b","c"]}'} if n['tool']=='agent_node' else execute_node(n,state,storage,config,llm)
    class S:path=':memory:'
    graph,connection=compile_workflow(S(),config,None,executor=executor)
    try:out=graph.invoke({'request':'synthetic','file_ids':[],'outputs':{},'steps':[]},{'configurable':{'thread_id':'synthetic-list'}})
    finally:connection.close()
    assert out['outputs']['list']['text']=='3'
    assert json.loads(out['outputs']['save']['artifact']['content'])==3
