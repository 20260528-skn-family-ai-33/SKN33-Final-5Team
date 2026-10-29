import pytest
from agent_builder.nodes.conversation import make_conversation
from agent_builder.test_deep_agent_conversation import state,draft
from agent_builder.test_workflow import node,bind,edge
from agent_builder.workflow import workflow_errors

@pytest.mark.parametrize('resources',[
    [],
    [{'id':'table-a','kind':'table','name':'a.csv','columns':['cost'],'profile':{}}],
    [{'id':'doc-a','kind':'document','name':'a.pdf','text':'계약 내용','columns':[]}],
    [{'id':'table-a','kind':'table','name':'a.csv','columns':['cost'],'profile':{}},
     {'id':'doc-a','kind':'document','name':'a.pdf','text':'계약 내용','columns':[]}],
])
def test_schema_file_ids_match_actual_resources(monkeypatch,resources):
    captured={}
    def factory(**kwargs):
        captured['schema']=kwargs['response_format'].schema
        class Agent:
            def invoke(self,payload,config): return {'structured_response':draft(questions=['확인해 주세요'])}
        return Agent()
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE','direct')
    monkeypatch.setattr('agent_builder.nodes.conversation.create_deep_agent',factory)
    s=state([{'role':'user','content':'자료를 처리해줘'}]);s['resources']=resources
    make_conversation(None,'test',model_factory=lambda:object())(s)
    variants=captured['schema']['$defs']['WorkflowNode']['anyOf']
    for variant in variants:
        field=variant['properties']['settings']['properties']['file_ids']
        if resources: assert field['items']['enum']==[r['id'] for r in resources]
        else: assert field['maxItems']==0

def test_read_nodes_reject_wrong_resource_kind_and_unknown_file():
    resources=[{'id':'table-a','kind':'table','columns':[]},{'id':'doc-a','kind':'document','columns':[]}]
    table=node('read','read_table',file_ids=['doc-a'])
    assert any('파일 종류' in x for x in workflow_errors({'entry':'read','nodes':[table],'edges':[]},['read_table'],resources))
    document=node('read','read_document',file_ids=['unknown'])
    assert any('파일 ID' in x for x in workflow_errors({'entry':'read','nodes':[document],'edges':[]},['read_document'],resources))

def test_invalid_edge_rejected_then_correct_edge_accepted():
    resources=[{'id':'table-a','kind':'table','columns':[]}]
    read=node('read','read_table',file_ids=['table-a'])
    write=node('write','write_result',[bind('table','read','table')],format='json')
    invalid={'entry':'read','nodes':[read,write],'edges':[edge('missing','write')]}
    valid={'entry':'read','nodes':[read,write],'edges':[edge('read','write')]}
    assert any('존재하지 않는 노드' in x for x in workflow_errors(invalid,['read_table','write_result'],resources))
    assert workflow_errors(valid,['read_table','write_result'],resources)==[]
