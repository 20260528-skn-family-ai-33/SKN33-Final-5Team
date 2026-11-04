import pytest
from agent_builder.design.nodes.conversation import make_conversation
from tests.design.test_deep_agent_conversation import state, draft
from tests.workflow_engine.test_workflow import node, bind, edge
from workflow_engine.workflow import workflow_errors

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
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    s=state([{'role':'user','content':'자료를 처리해줘'}]);s['resources']=resources
    make_conversation(None,'test',model_factory=lambda:object())(s)
    variants=captured['schema']['$defs']['WorkflowNode']['anyOf']
    for variant in variants:
        field_schema=variant['properties']['settings']
        if '$ref' in field_schema:field_schema=captured['schema']['$defs'][field_schema['$ref'].split('/')[-1]]
        settings=field_schema['properties']
        if 'file_ids' not in settings:
            if 'operation' in settings:
                assert set(settings)=={'operation','arguments_json','output_port'}
            else:
                from agent_builder.design.nodes.conversation import NATIVE_SETTINGS
                identifier=variant['properties']['tool']['enum'][0]
                assert set(settings)=={*NATIVE_SETTINGS[identifier],'node_ref'}
            continue
        field=settings['file_ids']
        if resources: assert field['items']['enum']==[r['id'] for r in resources]
        else: assert field['maxItems']==0

def test_read_nodes_reject_wrong_resource_kind_and_unknown_file():
    resources=[{'id':'table-a','kind':'table','columns':[]},{'id':'doc-a','kind':'document','columns':[]}]
    table=node('read','read_table',file_ids=['doc-a'])
    assert any('파일 종류' in x for x in workflow_errors({'entry':'read','nodes':[table],'edges':[]},['read_table'],resources))
    document=node('read','read_document',file_ids=['unknown'])
    assert any('파일 ID' in x for x in workflow_errors({'entry':'read','nodes':[document],'edges':[]},['read_document'],resources))


def test_native_operation_requires_explicit_contract_inspection(monkeypatch):
    from workflow_engine.catalog.canonical import operation_variants as original
    offered = []
    def tracked(*args, **kwargs):
        offered.append(set(args[2]) if len(args)>2 and args[2] is not None else None)
        return original(*args, **kwargs)
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setattr('workflow_engine.catalog.canonical.operation_variants', tracked)
    def factory(**kwargs):
        class Agent:
            def invoke(self, payload, config):
                assert 'read_table' not in offered[0]
                tools = {t.name:t for t in kwargs['tools']}
                details = tools['inspect_tool'].invoke({'tool_id':'read_table'})
                assert details['operation_contracts']
                assert 'read_table' in offered[-1]
                return {'structured_response':draft(questions=['실행할 업무를 알려주세요'])}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', factory)
    s = state([{'role':'user','content':'자료를 읽어줘'}])
    s['resources'] = [{'id':'table-a','kind':'table','name':'a.csv','columns':['cost'],'profile':{}}]
    make_conversation(None,'test',model_factory=lambda:object())(s)

def test_invalid_edge_rejected_then_correct_edge_accepted():
    resources=[{'id':'table-a','kind':'table','columns':[]}]
    read=node('read','read_table',file_ids=['table-a'])
    write=node('write','write_result',[bind('table','read','table')],format='json')
    invalid={'entry':'read','nodes':[read,write],'edges':[edge('missing','write')]}
    valid={'entry':'read','nodes':[read,write],'edges':[edge('read','write')]}
    assert any('존재하지 않는 노드' in x for x in workflow_errors(invalid,['read_table','write_result'],resources))
    assert workflow_errors(valid,['read_table','write_result'],resources)==[]
