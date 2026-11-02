"""Real local JSON implementation; synthetic input only, no model/network."""
import json
import pytest
from agent_builder.models import NodeSettings
from agent_builder.test_workflow import node,bind,edge
from agent_builder.workflow import workflow_errors
from agent_builder.workflow_runtime import compile_workflow,execute_node

def run(value,operation,path=None):
    n=node('json','json_transform',[bind('text','source','text')],json_operation=operation,json_path=path)
    return execute_node(n,{'outputs':{'source':{'text':value}}},None,{},None)['text']

def test_existing_parser_and_nested_lookup_return_json_text():
    source='{"목록":[{"금액":10},{"금액":20}],"활성":true}'
    assert json.loads(run(source,'parse'))=={'목록':[{'금액':10},{'금액':20}],'활성':True}
    assert json.loads(run(source,'lookup',['목록',1,'금액']))==20
    assert json.loads(run(source,'lookup',[]))==json.loads(source)
    assert json.loads(run('{"x":null}','lookup',['x'])) is None
    assert NodeSettings.model_validate({'file_ids':[],'query':None,'fields':[],'instructions':None,
        'format':None,'question':None,'condition_field':None,'condition_operator':None,
        'json_operation':'lookup','json_path':['목록',1]}).json_path==['목록',1]

@pytest.mark.parametrize('text,operation,path',[
    ('{"x":1,"x":2}','parse',None),('{"x":NaN}','parse',None),('{"x":1e999}','parse',None),
    ('not json','parse',None),('{"x":1}','lookup',['missing']),('[1]','lookup',[1]),
    ('[1]','lookup',[-1]),('[1]','lookup',[True]),('[1]','lookup',None),
])
def test_invalid_json_or_path_fails_without_guessing(text,operation,path):
    with pytest.raises(ValueError):run(text,operation,path)

def test_workflow_validator_requires_text_and_valid_path():
    w={'entry':'source','nodes':[node('source','agent_node'),node('json','json_transform',[],json_operation='lookup',json_path=[-1])],
       'edges':[edge('source','json')]}
    issues=workflow_errors(w,['agent_node','json_transform'],[])
    assert any('필수 입력' in issue for issue in issues)
    assert any('JSON 조회 경로' in issue for issue in issues)

def test_compiled_workflow_uses_real_json_executor():
    nodes=[node('source','agent_node'),
           node('json','json_transform',[bind('text','source','text')],json_operation='lookup',json_path=['values',1]),
           node('finish','finish',[bind('text','json','text')])]
    workflow={'entry':'source','nodes':nodes,'edges':[edge('source','json'),edge('json','finish')]}
    config={'workflow':workflow,'tool_ids':['agent_node','json_transform','finish'],'document_ids':[],'execution_plan':{'output_format':'json'}}
    assert not workflow_errors(workflow,config['tool_ids'],[])
    def executor(n,state,storage,config,llm):
        return {'text':'{"values":[10,20]}'} if n['tool']=='agent_node' else execute_node(n,state,storage,config,llm)
    class Storage:path=':memory:'
    graph,connection=compile_workflow(Storage(),config,None,executor=executor)
    try:out=graph.invoke({'request':'synthetic','file_ids':[],'outputs':{},'steps':[]},{'configurable':{'thread_id':'synthetic'}})
    finally:connection.close()
    assert out['outputs']['json']['text']=='20'
    assert out['outputs']['finish']['text']=='20'
