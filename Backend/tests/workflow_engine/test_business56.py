"""56 supplied business labels: save/reload/bind/compile/execute, with real local files.
Model responses and external accounts are injected fixtures; no live delivery or quality claim.
"""
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.models import NodeSettings, WorkflowNode
from infrastructure.persistence.storage import Storage
from workflow_engine.catalog.canonical import BUSINESS_IDS, OPERATIONS, arguments_spec, operation_spec
from workflow_engine.catalog.registry import TOOLS, EXECUTORS, PORTS, node_ports
from workflow_engine.executors.canonical_operation import validate_settings
from workflow_engine.library import node_library
from workflow_engine.runner import run_workflow
from workflow_engine.unified_tools.local_operations import document
from workflow_engine.workflow import workflow_errors
from tests.workflow_engine.test_workflow import saved, node, bind, edge


@pytest.fixture
def business_files(tmp_path):
    from pypdf import PdfWriter
    from PIL import Image
    from docx import Document
    from openpyxl import Workbook
    root=tmp_path/'operation-files'; root.mkdir()
    (root/'a.txt').write_text('Revenue 100',encoding='utf-8')
    (root/'a.csv').write_text('id,amount\n1,10\n2,20\n',encoding='utf-8')
    (root/'a.html').write_text('<h1>Hello</h1><table><tr><th>id</th></tr><tr><td>1</td></tr></table>')
    (root/'template.txt').write_text('Hello {{name}}',encoding='utf-8')
    writer=PdfWriter();writer.add_blank_page(width=200,height=100);writer.write(root/'a.pdf')
    Image.new('RGB',(2,2),'white').save(root/'page.png')
    d=Document();d.add_paragraph('hello');d.save(root/'a.docx')
    w=Workbook();w.active.title='Data';w.active.append(['id']);w.active.append([1]);w.save(root/'a.xlsx');w.close()
    return root


def examples(root):
    d=document('Revenue 100');source={**d,'source_id':'s'}
    table={'columns':['id','amount'],'rows':[{'id':1,'amount':10},{'id':2,'amount':20}]}
    def asset(name):return {'path':name,'version':hashlib.sha256((root/name).read_bytes()).hexdigest()}
    return {
      'read_document':('extract_content',dict(asset='a.txt',format='txt')),
      'read_table':('parse',dict(source='a.csv',format='csv')),
      'read_web':('extract_html',dict(asset='a.html',extract='both')),
      'search_document':('retrieve_passages',dict(documents=[source],query='Revenue',top_k=1)),
      'extract_information':('extract_fields',dict(documents=[source],fields=[{'name':'revenue'}],missing_policy='null')),
      'compose_answer':('answer',dict(question='Revenue?',evidence=[source])),
      'filter_sort':('deduplicate',dict(table=table,columns=['id'],keep='first')),
      'aggregate_table':('sum',dict(table=table,group_by=[],value_column='amount',null_policy='error')),
      'calculate_columns':('compute',dict(table=table,output_column='double',formula='amount*2')),
      'join_tables':('join',dict(left=table,right={'rows':[{'id':1,'name':'A'}]},keys=['id'],how='left',cardinality='many_to_one',null_keys='error',max_rows=10)),
      'compare_tables':('diff',dict(baseline=table,candidate=table,keys=['id'],compare_columns=['amount'],absolute_tolerance=0)),
      'create_spreadsheet':('create',dict(sheets=[{'name':'Data','table':table}])),
      'edit_spreadsheet':('append_rows',dict(workbook=asset('a.xlsx'),expected_version=asset('a.xlsx')['version'],sheet_name='Data',table={'rows':[{'id':2}]})),
      'create_document':('compose',dict(sections=[{'heading':'Title','text':'Hello'}],format='docx')),
      'edit_document':('replace_text',dict(document=d,expected_version=d['version'],find='Revenue',replacement='매출',occurrence='all')),
      'format_document':('apply_formatting',dict(document=asset('a.docx'),expected_version=asset('a.docx')['version'],rules=[{'paragraph':1,'bold':True}])),
      'fill_template':('fill_fields',dict(template='template.txt',field_values={'name':'Kim'},missing_policy='keep')),
      'convert_format':('convert',dict(asset='a.docx',source_format='docx',target_format='txt',loss_policy='allow')),
      'export_result':('serialize',dict(value=[{'amount':30}],format='json',filename='result.json')),
      'proofread_document':('report_only',dict(document=d,language='en',checks=['spelling'])),
      'translate_document':('translate',dict(document=d,source_language='en',target_language='ko')),
      'compare_documents':('diff',dict(baseline=d,candidate=d,mode='lines')),
      'verify_content_requirements':('check_required',dict(document=d,requirements=[{'id':'r','type':'contains','text':'100'}])),
      'review_document':('review',dict(document=d,rubric=[],evidence=[source])),
      'reply_review':('review_reply',dict(customer_request='help',policy=[source],draft={'text':'Hello'},rubric=[])),
      'review_summary':('tabulate_reviews',dict(reviews=[{'issues':[{'id':'a','message':'m'}]}],columns=['id','message'],dedup_key='id')),
      'validate_document_format':('report_only',dict(asset=asset('a.docx'),reference=asset('a.docx'),checks=[{'paragraph':1,'property':'bold'}])),
      'check_document_links':('check_local_targets',dict(document={'text':'[a](a.txt)'},local_assets=['a.txt'])),
      'text_statistics':('measure',dict(document=d,units=['characters'],whitespace='exclude',word_policy='whitespace')),
      'detect_language':('detect',dict(document=d,allowed_languages=['en','ko'])),
      'document_visual_qa':('answer_visual',dict(pages=[{'path':'page.png','page':1}],question='What?')),
      'classify_document':('classify',dict(document=d,classes=[{'id':'finance'}],mode='single')),
      'verify_table_calculations':('check_totals',dict(table=table,checks=[{'id':'sum','operation':'sum','column':'amount','expected':30}],absolute_tolerance=0)),
      'split_pdf':('extract_pages',dict(asset='a.pdf',page_numbers=[1])),
      'merge_documents':('merge',dict(assets=['a.pdf','a.pdf'],format='pdf')),
      'compress_pdf':('optimize',dict(asset='a.pdf',target_bytes=1,quality_policy='lossless')),
      'render_document_pages':('render',dict(asset='a.pdf',page_numbers=[1],dpi=72)),
      'annotate_document':('add_annotations',dict(asset='a.pdf',annotations=[{'page':1,'type':'highlight','rect':[10,10,60,30],'text':'note'}],flatten=False)),
      'scalar_calculate':('evaluate',dict(formula='x*2',variables={'x':3})),
      'text_transform':('replace',dict(text='hello',find='hello',replacement='안녕')),
      'json_transform':('lookup',dict(value={'a':[1,2]},path=['a',1])),
      'list_transform':('count',dict(items=[1,2])),
      'date_transform':('adjust',dict(timestamp='2026-11-03T10:00:00+09:00',seconds=60)),
      'url_transform':('build',dict(scheme='https',host='example.com',path='/a')),
      'base64_codec':('convert',dict(text='hello',mode='encode')),
      'text_hash':('sha256',dict(text='hello')),
      'email.prepare':('execute',dict(sender='sender@example.com',to=['to@example.com'],subject='Test',body='Hello')),
      'email.send':('execute',dict(to=['to@example.com'],subject='Test',body='Hello')),
      'email.list':('execute',dict(max_results=1)),
      'email.get':('execute',dict(message_id='m1')),
      'email.attachment.get':('execute',dict(message_id='m1',attachment_id='a1')),
      'calendar.create_event':('execute',dict(summary='Test',start='2026-11-03T10:00:00+09:00',end='2026-11-03T11:00:00+09:00')),
      'calendar.list_events':('execute',dict(max_results=1)),
      'calendar.get_event':('execute',dict(event_id='e1')),
      'calendar.update_event':('execute',dict(event_id='e1',changes={'summary':'Changed'})),
      'calendar.delete_event':('execute',dict(event_id='e1')),
    }


MODEL_RESULTS={
 'extract_information':{'values':[{'field':'revenue','value':100,'source_id':'s','quote':'Revenue 100'}]},
 'compose_answer':{'answer':{'text':'100','citations':[{'source_id':'s','quote':'Revenue 100'}]}},
 'proofread_document':{'review':{'issues':[]}}, 'review_document':{'review':{'issues':[]}},
 'reply_review':{'review':{'issues':[]}},
 'translate_document':{'document':{'text':'매출 100'},'alignments':[{'source':'Revenue 100','target':'매출 100'}]},
 'detect_language':{'result':{'language':'en','confidence':.9}},
 'classify_document':{'result':{'labels':['finance'],'scores':{'finance':.9}}},
 'document_visual_qa':{'answer':{'text':'test','citations':[{'page':1}]}},
}


@pytest.mark.parametrize('tool',BUSINESS_IDS)
def test_each_business_label_saves_reloads_and_executes_in_service(tmp_path,business_files,tool):
    cases=examples(business_files)
    assert set(cases)==set(BUSINESS_IDS)
    operation,args=cases[tool]
    settings=NodeSettings.model_validate({'operation':operation,'arguments_json':json.dumps(args)}).model_dump()
    n={'id':'business','tool':tool,'label':tool,'bindings':[],'settings':settings}
    w={'entry':'business','nodes':[n],'edges':[]}
    assert workflow_errors(w,[tool],[])==[]
    store,identifier,_=saved(tmp_path,w)
    try:
        snapshot=node_library.save(store,{'kind':'processing','name':tool,'purpose':'56개 업무 도구 연결 검증',
                                         'tool':tool,'settings':settings})
        loaded=node_library.get(store,snapshot['ref'])
        assert loaded['spec']['settings']['operation']==operation
        applied=node_library.apply_refs({'entry':'business','nodes':[{**n,'settings':{**settings,'node_ref':snapshot['ref']}}],'edges':[]},[loaded])
        assert applied['nodes'][0]['settings']['arguments_json']==settings['arguments_json']
        calls=[]
        def connector(op,arguments):calls.append((op,arguments));return {'id':'offline-fixture','operation':op}
        store.workflow_connectors={'gmail':connector,'google_calendar':connector}
        class Model:
            def invoke(self,messages):
                calls.append(messages)
                return SimpleNamespace(content=json.dumps(MODEL_RESULTS[tool]))
        run=run_workflow(store,identifier,'업무 실행','unused',llm=Model())
        assert run['status']=='succeeded',run.get('error')
        assert 'result' in run['outputs']['business']
        assert set(node_ports(n)[1])<=set(run['outputs']['business'])
        if 'asset' in run['outputs']['business']:
            artifact=run['artifact'];data=base64.b64decode(artifact['content'])
            assert (tmp_path/run['report_path']).read_bytes()==data
            assert artifact['encoding']=='base64'
        if tool in MODEL_RESULTS or tool.startswith(('email.','calendar.')) and tool!='email.prepare':assert calls
    finally:store.close()


def test_recommendation_labels_schema_registry_and_executors_agree():
    from typing import get_args
    assert len(BUSINESS_IDS)==56 and len(set(BUSINESS_IDS))==56
    assert set(BUSINESS_IDS)<={t['id'] for t in TOOLS}
    assert set(get_args(WorkflowNode.model_fields['tool'].annotation))==set(EXECUTORS)==set(PORTS)
    for tool,specs in OPERATIONS.items():
        for spec in specs:assert arguments_spec(tool,spec) is not None


def test_missing_or_unknown_arguments_stop_before_execution():
    for arguments in ({'formula':'x*2'},{'formula':'x*2','variables':{'x':2},'made_up':True}):
        n=node('calc','scalar_calculate',operation='evaluate',arguments_json=json.dumps(arguments))
        assert workflow_errors({'entry':'calc','nodes':[n],'edges':[]},['scalar_calculate'],[])


def test_invalid_entry_also_reports_operation_and_binding_errors():
    read=node('read','read_table')
    calc=node('calc','scalar_calculate',[bind('variables','read','table')],
              operation='evaluate',arguments_json=json.dumps({'formula':'x*2','variables':{'x':2}}))
    errors=workflow_errors({'entry':'missing','nodes':[read,calc],'edges':[edge('read','calc')]},
                           ['read_table','scalar_calculate'],[])
    assert any('시작 노드' in e for e in errors)
    assert any('상수와 연결 입력이 중복' in e for e in errors)


def test_missing_google_account_fails_without_any_delivery(tmp_path,monkeypatch):
    monkeypatch.delenv('A4B_GOOGLE_ACCESS_TOKEN',raising=False)
    n=node('mail','email.send',operation='execute',arguments_json=json.dumps({'to':['to@example.com'],'subject':'Test','body':'Hello'}))
    store,identifier,_=saved(tmp_path,{'entry':'mail','nodes':[n],'edges':[]})
    try:
        result=run_workflow(store,identifier,'실행','unused')
        assert result['status']=='failed' and '계정 연결' in result['error']
    finally:store.close()


def test_typed_native_table_can_feed_new_join_and_existing_writer(tmp_path):
    from tests.design.test_builder import initial,draft
    from infrastructure.persistence.ids import uid
    table={'columns':['id','value'],'rows':[{'id':1,'value':10}]}
    read=node('read','read_table')
    join=node('join','join_tables',[bind('left','read','table')],operation='join',arguments_json=json.dumps({
        'right':{'rows':[{'id':1,'name':'A'}]},'keys':['id'],'how':'left','cardinality':'many_to_one','null_keys':'error','max_rows':10}))
    out=node('out','write_result',[bind('table','join','table')],format='json')
    w={'entry':'read','nodes':[read,join,out],'edges':[edge('read','join'),edge('join','out')]}
    store,identifier,_=saved(tmp_path,w)
    try:
        asset={'id':uid(),'name':'a.csv','kind':'table','format':'csv','columns':table['columns']}
        store.add_asset(asset,table['rows'])
        result=run_workflow(store,identifier,'실행','unused',file_ids=[asset['id']])
        assert result['status']=='succeeded',result.get('error')
        assert result['outputs']['join']['table']['rows']==[{'id':1,'value':10,'name':'A'}]
    finally:store.close()


@pytest.mark.parametrize('tool',BUSINESS_IDS)
@pytest.mark.parametrize('mode',['deepagents','direct'])
def test_planner_accepts_operation_settings_and_preserves_exact_arguments(monkeypatch,business_files,tool,mode):
    from copy import deepcopy
    from agent_builder.design.nodes.conversation import make_conversation
    from agent_builder.design.nodes.validation import validate_node
    from tests.design.test_deep_agent_conversation import draft,state
    operation,args=examples(business_files)[tool]
    # This is exactly the compact response grammar, not a prefilled native settings object.
    n={'id':'business','tool':tool,'label':'요청 업무','bindings':[],
       'settings':{'operation':operation,'arguments_json':json.dumps(args)}}
    raw=draft(workflow={'entry':'business','nodes':[n],'edges':[]})
    original={**state([{'role':'user','content':'요청한 자료를 처리해줘'}]),'session_id':'business-test','trace':[]}
    captured={}
    if mode=='deepagents':
        raw['workflow']={'first_step':{'tool':tool,'label':'요청 업무',
            'settings':{'operation':operation,'arguments_json':deepcopy(args),'output_port':None}},'next_steps':[]}
        monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
        original['capability_assessment']={'decision':'existing_tools','tool_ids':[tool]}
        monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
        monkeypatch.setenv('A4B_TOOL_CATALOG_MODE','direct')
        def factory(**kwargs):
            captured['schema']=kwargs['response_format'].schema
            from jsonschema import Draft202012Validator
            tools={t.name:t for t in kwargs['tools']}
            assert tools['inspect_tool'].invoke({'tool_id':tool})['id']==tool
            # Native tools disclose alternative operations after explicit inspection.
            Draft202012Validator(captured['schema']).validate(raw)
            class Agent:
                def invoke(self,payload,*a,**kw):
                    context=json.loads(payload['messages'][0]['content'].split('\n',1)[1])
                    assert [t['id'] for t in context['selected_tool_contracts']]==[tool]
                    return {'structured_response':deepcopy(raw)}
            return Agent()
        monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
        model=lambda:object()
    else:
        monkeypatch.setenv('A4B_PLANNER_MODE','direct')
        monkeypatch.setenv('A4B_PLANNER_PROMPT','v7')
        raw['requirements']=[{'description':'요청','source_id':'user','quote':original['messages'][0]['content']}]
        class Model:
            def invoke(self,messages):return {'content':json.dumps(raw)}
        model=Model
    result=make_conversation('','fake',model_factory=model)(original)
    checked=validate_node({**original,**result})
    assert checked['status']=='ready',checked['issues']
    produced=checked['draft']['workflow']['nodes'][0]
    assert produced['tool']==tool
    assert produced['settings']['operation']==operation
    assert json.loads(produced['settings']['arguments_json'])==args


def test_table_result_can_fill_nested_spreadsheet_argument(tmp_path):
    import io
    from openpyxl import load_workbook
    calc=node('table','join_tables',operation='join',arguments_json=json.dumps({
        'left':{'rows':[{'id':1,'value':10}]},'right':{'rows':[{'id':1,'name':'A'}]},'keys':['id'],
        'how':'left','cardinality':'many_to_one','null_keys':'error','max_rows':10}))
    create=node('xlsx','create_spreadsheet',[bind('sheets','table','table')],operation='create',arguments_json=json.dumps({
        'sheets':[{'name':'Result','table':{'$binding':'sheets'}}]}))
    w={'entry':'table','nodes':[calc,create],'edges':[edge('table','xlsx')]}
    assert workflow_errors(w,['join_tables','create_spreadsheet'],[])==[]
    store,identifier,_=saved(tmp_path,w)
    try:
        result=run_workflow(store,identifier,'실행','unused')
        assert result['status']=='succeeded',result.get('error')
        wb=load_workbook(io.BytesIO(base64.b64decode(result['artifact']['content'])))
        assert list(wb['Result'].values)==[('id','value','name'),(1,10,'A')]
        wb.close()
    finally:store.close()


def test_json_table_projection_validates_shape_at_runtime(tmp_path):
    select=node('select','json_transform',operation='lookup',output_port='table',arguments_json=json.dumps({
        'value':{'tables':[{'columns':['x'],'rows':[{'x':1}]}]},'path':['tables',0]}))
    write=node('write','write_result',[bind('table','select','table')],format='json')
    w={'entry':'select','nodes':[select,write],'edges':[edge('select','write')]}
    assert workflow_errors(w,['json_transform','write_result'],[])==[]
    store,identifier,_=saved(tmp_path,w)
    try:
        result=run_workflow(store,identifier,'실행','unused')
        assert result['status']=='succeeded',result.get('error')
        assert result['outputs']['select']['table']['rows']==[{'x':1}]
    finally:store.close()


def test_operation_bridge_works_with_preserved_verifier():
    from agent_builder.design.nodes.verification import project_operations,tools_for_drafts
    from verification.workflow.adapters import draft_from_backend
    from verification.workflow.contracts import ToolSpec
    n=node('calc','scalar_calculate',operation='evaluate',arguments_json='{"formula":"1+2","variables":{}}')
    raw={'document_ids':[],'workflow':{'entry':'calc','nodes':[n],'edges':[]}}
    projected,specs=project_operations(raw)
    adapted=draft_from_backend(projected,draft_id='test',version='1')
    assert adapted.workflow.nodes[0].tool==specs[0]['id']
    assert specs[0]['id'] in {ToolSpec.model_validate(t).id for t in tools_for_drafts(raw)}


def test_canonical_document_can_feed_native_extraction(tmp_path,business_files):
    from workflow_engine.executors.schemas import Facts,Fact
    read=node('html','read_web',operation='extract_html',arguments_json='{"asset":"a.html","extract":"text"}')
    extract=node('extract','extract_information',[bind('documents','html','documents')],fields=['제목'])
    w={'entry':'html','nodes':[read,extract],'edges':[edge('html','extract')]}
    store,identifier,_=saved(tmp_path,w)
    class Model:
        def with_structured_output(self,*a,**kw):return self
        def invoke(self,messages):
            source=json.loads(messages[-1]['content'])['documents'][0]
            return Facts(facts=[Fact(field='제목',value='Hello',source_id=source['source_id'],quote='Hello')])
    try:
        result=run_workflow(store,identifier,'실행','unused',llm=Model())
        assert result['status']=='succeeded',result.get('error')
        assert result['outputs']['extract']['records'][0]['value']=='Hello'
    finally:store.close()


def test_empty_html_text_still_returns_valid_document_ports(tmp_path,business_files):
    n=node('html','read_web',operation='extract_html',arguments_json='{"asset":"a.html","extract":"tables"}')
    store,identifier,_=saved(tmp_path,{'entry':'html','nodes':[n],'edges':[]})
    try:assert run_workflow(store,identifier,'실행','unused')['status']=='succeeded'
    finally:store.close()


def test_uploaded_asset_id_resolves_only_connected_original(tmp_path):
    from infrastructure.persistence.ids import uid
    text=b'Hello uploaded document'
    digest=hashlib.sha256(text).hexdigest()
    originals=tmp_path/'builder-originals';originals.mkdir()
    relative='builder-originals/'+digest+'.txt';(tmp_path/relative).write_bytes(text)
    identifier=uid()
    n=node('read','read_document',operation='extract_content',arguments_json=json.dumps({'asset':{'asset_id':identifier},'format':'txt'}))
    w={'entry':'read','nodes':[n],'edges':[]}
    store,agent,_=saved(tmp_path,w)
    asset={'id':identifier,'kind':'document','name':'upload.txt','format':'txt','original_path':relative,'hash':digest}
    store.add_asset(asset,[])
    try:
        blocked=run_workflow(store,agent,'실행','unused')
        assert blocked['status']=='failed'
        result=run_workflow(store,agent,'실행','unused',file_ids=[identifier])
        assert result['status']=='succeeded',result.get('error')
        assert result['outputs']['read']['document']['text']==text.decode()
        assert (tmp_path/relative).read_bytes()==text
    finally:store.close()


def test_ollama_object_arguments_are_normalized_without_losing_nested_values(monkeypatch):
    from workflow_engine.catalog.canonical import parse_arguments,operation_variants
    from contracts.models import AgentDraft
    args={'keys':['customer_id'],'how':'left','max_rows':100}
    settings=NodeSettings.model_validate({'operation':'join','arguments_json':args})
    assert isinstance(settings.arguments_json,str) and parse_arguments(settings.model_dump())==args
    assert args=={'keys':['customer_id'],'how':'left','max_rows':100}
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    schema=AgentDraft.model_json_schema()
    for variant in operation_variants(schema['$defs']['WorkflowNode'],schema['$defs']['Binding']):
        assert variant['properties']['settings']['properties']['arguments_json']['type']=='object'


@pytest.mark.parametrize('patch',[{'how':'left equal-named keys'},{'keys':{'customer_id':1}},
                                  {'cardinality':'many_to_many'},{'max_rows':True},{'max_rows':-1}])
def test_join_policy_errors_are_blocked_before_execution(patch):
    args={'keys':['customer_id'],'how':'left','cardinality':'many_to_one','null_keys':'error','max_rows':100,**patch}
    n=node('join','join_tables',[bind('left','a','table'),bind('right','b','table')],operation='join',arguments_json=json.dumps(args))
    with pytest.raises(ValueError):validate_settings(n)


def test_ollama_decoder_keeps_join_arguments_and_ports_separate_from_other_tools(monkeypatch):
    from workflow_engine.catalog.canonical import operation_variants
    from contracts.models import AgentDraft
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    schema=AgentDraft.model_json_schema()
    variants=operation_variants(schema['$defs']['WorkflowNode'],schema['$defs']['Binding'])
    join=next(v for v in variants if v['properties']['tool']['enum']==['join_tables'])
    properties=join['properties']['settings']['properties']['arguments_json']['properties']
    assert 'file_id' not in properties and properties['how']['enum']==['inner','left']
    ports=join['properties']['bindings']['items']['properties']['input']['enum']
    assert 'left' in ports and 'right' in ports and 'table' not in ports
    assert join['properties']['settings']['properties']['arguments_json']['additionalProperties'] is False


def test_ollama_lazy_contracts_keep_full_discovery_and_expand_after_inspection(monkeypatch):
    from agent_builder.design.nodes.conversation import make_conversation
    from tests.design.test_deep_agent_conversation import draft,state
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE','direct')
    original={**state([{'role':'user','content':'계산해줘'}]),'trace':[],
              'capability_assessment':{'decision':'existing_tools','tool_ids':['scalar_calculate']}}
    def factory(**kwargs):
        schema=kwargs['response_format'].schema
        def decoded_tools():
            return {identifier for v in schema['$defs']['WorkflowNode']['anyOf']
                    for identifier in v['properties']['tool']['enum']}
        assert 'scalar_calculate' in decoded_tools() and 'join_tables' not in decoded_tools()
        tools={t.name:t for t in kwargs['tools']}
        assert tools['inspect_tool'].invoke({'tool_id':'join_tables'})['id']=='join_tables'
        assert 'join_tables' in decoded_tools()
        class Agent:
            def invoke(self,payload,*args,**kw):
                context=json.loads(payload['messages'][0]['content'].split('\n',1)[1])
                assert set(BUSINESS_IDS)<={t['id'] for t in context['supported_tools']}
                return {'structured_response':draft(questions=['계산식을 알려주세요.'])}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    make_conversation('','fake',model_factory=lambda:object())(original)


@pytest.mark.parametrize('tool',BUSINESS_IDS)
def test_each_business_example_fits_actual_ollama_decoder(monkeypatch,business_files,tool):
    from contracts.models import AgentDraft
    from jsonschema import Draft202012Validator
    from workflow_engine.catalog.canonical import operation_variants
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    schema=AgentDraft.model_json_schema()
    variants=operation_variants(schema['$defs']['WorkflowNode'],schema['$defs']['Binding'],{tool})
    operation,args=examples(business_files)[tool]
    wire_node={'id':'business','tool':tool,'label':'업무','bindings':[],
               'settings':{'operation':operation,'arguments_json':args,'output_port':None}}
    decoder={'anyOf':variants,'$defs':schema['$defs']}
    Draft202012Validator.check_schema(decoder)
    Draft202012Validator(decoder).validate(wire_node)
    changed={**wire_node,'settings':{**wire_node['settings'],'arguments_json':{**args,'made_up_argument':True}}}
    assert not Draft202012Validator(decoder).is_valid(changed)


def test_scalar_missing_formula_variable_is_rejected_before_save():
    n=node('calc','scalar_calculate',operation='evaluate',arguments_json=json.dumps({'formula':'x*3+2','variables':{}}))
    errors=workflow_errors({'entry':'calc','nodes':[n],'edges':[]},['scalar_calculate'],[])
    assert any('변수 값이 없습니다' in e and 'x' in e for e in errors)
    n['settings']['arguments_json']=json.dumps({'formula':'x*3+2','variables':{'x':7}})
    assert workflow_errors({'entry':'calc','nodes':[n],'edges':[]},['scalar_calculate'],[])==[]


@pytest.mark.parametrize('formula,variables',[('x*2',{'x':True}),('1/x',{'x':0}),('x*2',{'x':'wrong'})])
def test_invalid_scalar_constant_is_rejected_before_save(formula,variables):
    n=node('calc','scalar_calculate',operation='evaluate',arguments_json=json.dumps({'formula':formula,'variables':variables}))
    assert any('상수 값을 확인' in e for e in workflow_errors({'entry':'calc','nodes':[n],'edges':[]},['scalar_calculate'],[]))


@pytest.mark.parametrize('sheets',[[],[{'name':'Data','rows':[]}],[{'name':'Data','table':{'rows':[[1,2]]}}],
    [{'name':'Data','table':{'columns':['id'],'rows':[{'amount':1}]}}],
    [{'name':'Data','table':{'rows':[{'id':{'unexpected':'nested'}}]}}]])
def test_invalid_spreadsheet_shape_is_rejected_before_save(sheets):
    n=node('xlsx','create_spreadsheet',operation='create',arguments_json=json.dumps({'sheets':sheets}))
    assert workflow_errors({'entry':'xlsx','nodes':[n],'edges':[]},['create_spreadsheet'],[])


def test_two_independent_readers_are_serialized_without_changing_join_inputs():
    from copy import deepcopy
    from workflow_engine.workflow import normalize_connections
    a=node('orders','read_table',file_ids=['orders-file'])
    b=node('customers','read_table',file_ids=['customers-file'])
    join=node('join','join_tables',[bind('left','orders','table'),bind('right','customers','table')],operation='join',arguments_json=json.dumps({
        'keys':['customer_id'],'how':'left','cardinality':'many_to_one','null_keys':'error','max_rows':100}))
    out=node('out','write_result',[bind('table','join','table')],format='json')
    w={'entry':'orders','nodes':[a,b,join,out],'edges':[edge('orders','join'),edge('customers','join'),edge('join','out')]}
    original=deepcopy(w)
    normalized=normalize_connections(w)
    assert normalized['edges']==[edge('orders','customers'),edge('customers','join'),edge('join','out')]
    assert normalized['nodes']==original['nodes']
    resources=[{'id':'orders-file','kind':'table','columns':['customer_id']},{'id':'customers-file','kind':'table','columns':['customer_id']}]
    assert workflow_errors(normalized,['read_table','join_tables','write_result'],resources)==[]
    # An unrelated side-effect root never acquires an invented execution order.
    invalid=deepcopy(original);invalid['nodes'].insert(0,node('send','email.send',operation='execute',arguments_json='{}'))
    assert normalize_connections(invalid)['edges']==original['edges']


def test_invalid_historical_arguments_do_not_block_verifying_corrected_candidate():
    from copy import deepcopy
    from agent_builder.design.nodes.verification import tools_for_drafts,project_operations
    valid={'document_ids':[],'workflow':{'entry':'calc','nodes':[node('calc','scalar_calculate',operation='evaluate',
         arguments_json=json.dumps({'formula':'x*2','variables':{'x':3}}))],'edges':[]}}
    previous=deepcopy(valid)
    previous['workflow']['nodes'][0]['settings']['arguments_json']=json.dumps({'formula':'x*2','variables':{}})
    projected,specs=project_operations(previous,strict=False)
    assert projected['workflow']['nodes'][0]['settings']['arguments_json']==previous['workflow']['nodes'][0]['settings']['arguments_json']
    assert specs[0]['id'] in {t['id'] for t in tools_for_drafts(previous,valid)}
    with pytest.raises(ValueError):project_operations(previous)
    with pytest.raises(ValueError):tools_for_drafts(valid,previous)


def test_verifier_retains_all_ids_and_exact_ports_only_for_used_nodes():
    from agent_builder.design.nodes.verification import tools_for_drafts,project_operations
    raw={'document_ids':[],'workflow':{'entry':'calc','nodes':[node('calc','scalar_calculate',operation='evaluate',
         arguments_json=json.dumps({'formula':'1+2','variables':{}}))],'edges':[]}}
    _,specs=project_operations(raw)
    tools={t['id']:t for t in tools_for_drafts(raw)}
    assert set(BUSINESS_IDS)<=tools.keys()
    assert tools[specs[0]['id']]['output_ports']['result']=='json'
    assert tools['create_spreadsheet']['input_ports']=={} and tools['create_spreadsheet']['description']


def test_unique_typed_output_alias_selects_actual_text_without_casting_json():
    from workflow_engine.workflow import normalize_connections
    calc=node('calc','scalar_calculate',operation='evaluate',arguments_json=json.dumps({'formula':'1+2','variables':{}}))
    done=node('done','finish',[bind('text','calc','result')])
    w=normalize_connections({'entry':'calc','nodes':[calc,done],'edges':[edge('calc','done')]})
    assert w['nodes'][1]['bindings']==[bind('text','calc','text')]
    assert workflow_errors(w,['scalar_calculate','finish'],[])==[]
    # A JSON sink must retain the raw JSON value even when text is also available.
    lookup=node('lookup','json_transform',[bind('value','calc','result')],operation='lookup',arguments_json=json.dumps({'path':[]}))
    w=normalize_connections({'entry':'calc','nodes':[calc,lookup],'edges':[edge('calc','lookup')]})
    assert w['nodes'][1]['bindings']==[bind('value','calc','result')]


@pytest.mark.parametrize('tool,operation,content,expected',[
    ('read_document','read_text',b'Uploaded text','Uploaded text'),
    ('read_table','read_csv',b'id,amount\nA,10\n',[{'id':'A','amount':'10'}])])
def test_runtime_file_read_operations_accept_scoped_uploaded_asset(tmp_path,tool,operation,content,expected):
    from infrastructure.persistence.ids import uid
    identifier=uid();digest=hashlib.sha256(content).hexdigest()
    originals=tmp_path/'builder-originals';originals.mkdir();relative='builder-originals/'+digest+'.txt'
    (tmp_path/relative).write_bytes(content)
    n=node('read','read_table' if tool=='read_table' else 'read_document',operation=operation,
           arguments_json=json.dumps({'path':{'asset_id':identifier}}))
    store,agent,_=saved(tmp_path,{'entry':'read','nodes':[n],'edges':[]})
    store.add_asset({'id':identifier,'kind':'document','name':'upload.txt','format':'txt','original_path':relative,'hash':digest},[])
    try:
        from agent_builder.conditions.input_requirements import preflight_execution
        # Canonical file readers consume their own asset arguments, not native
        # parsed-table selections. The service must not reject them beforehand.
        assert preflight_execution(store.agent(agent)['config'],store,[identifier]) == []
        result=run_workflow(store,agent,'실행','unused',file_ids=[identifier])
        assert result['status']=='succeeded',result.get('error')
        assert result['outputs']['read']['result']==expected
        assert (tmp_path/relative).read_bytes()==content
    finally:store.close()


def test_asset_resolution_preserves_visual_page_metadata_and_pins_path(tmp_path):
    from workflow_engine.executors.canonical_operation import resolve_assets,execution_context
    from infrastructure.persistence.ids import uid
    store=Storage(tmp_path);identifier=uid();content=b'image fixture';digest=hashlib.sha256(content).hexdigest()
    originals=tmp_path/'builder-originals';originals.mkdir();relative='builder-originals/'+digest+'.png';(tmp_path/relative).write_bytes(content)
    store.add_asset({'id':identifier,'kind':'document','name':'page.png','original_path':relative,'hash':digest},[])
    try:
        context=execution_context(store)
        resolved=resolve_assets({'asset_id':identifier,'page':2,'path':'../../ignored','version':'untrusted'},store,context,{identifier})
        assert resolved['page']==2 and resolved['version']==digest
        assert context.file(resolved['path']).read_bytes()==content
    finally:store.close()


def test_runtime_record_readers_join_and_export_connect_without_table_cast(tmp_path,business_files):
    (business_files/'b.csv').write_text('id,name\n1,Kim\n',encoding='utf-8')
    a=node('a','read_table',operation='read_csv',arguments_json='{"path":"a.csv"}')
    b=node('b','read_table',operation='read_csv',arguments_json='{"path":"b.csv"}')
    join=node('join','join_tables',[bind('left','a','records'),bind('right','b','records')],operation='join_records',
              arguments_json=json.dumps({'keys':['id'],'how':'left','cardinality':'many_to_one'}))
    out=node('out','export_result',[bind('value','join','records')],operation='serialize',arguments_json='{"format":"json","filename":"joined.json"}')
    w={'entry':'a','nodes':[a,b,join,out],'edges':[edge('a','b'),edge('b','join'),edge('join','out')]}
    assert workflow_errors(w,['read_table','join_tables','export_result'],[])==[]
    store,agent,_=saved(tmp_path,w)
    try:
        result=run_workflow(store,agent,'실행','unused')
        assert result['status']=='succeeded',result.get('error')
        assert result['outputs']['join']['records']==[{'id':'1','amount':'10','name':'Kim'},{'id':'2','amount':'20','name':None}]
    finally:store.close()


def test_projection_node_library_preserves_result_type_through_save_reload_and_execution(tmp_path):
    from contracts.models import NodeSettings
    args={'value':{'table':{'columns':['id'],'rows':[{'id':1}]}},'path':['table']}
    settings=NodeSettings.model_validate({'operation':'lookup','arguments_json':json.dumps(args),'output_port':'table'}).model_dump()
    store=Storage(tmp_path)
    try:
        snapshot=node_library.save(store,{'kind':'processing','name':'표 선택','purpose':'JSON에서 실제 표를 선택합니다.','tool':'json_transform','settings':settings})
        loaded=node_library.get(store,snapshot['ref'])
        assert loaded['spec']['settings']['output_port']=='table'
        workflow={'entry':'pick','nodes':[node('pick','json_transform',node_ref=snapshot['ref'])],'edges':[]}
        applied=node_library.apply_refs(workflow,[loaded])
        assert workflow_errors(applied,['json_transform'],[])==[]
    finally:store.close()
    # Execute the applied immutable settings independently of library snapshot ownership.
    applied['nodes'][0]['settings']['node_ref']=None
    execution_store,agent,_=saved(tmp_path,applied)
    try:
        result=run_workflow(execution_store,agent,'실행','unused')
        assert result['status']=='succeeded',result.get('error')
        assert result['outputs']['pick']['table']==args['value']['table']
    finally:execution_store.close()


@pytest.mark.parametrize('settings',[{'output_node':'other'},{'agent_id':'other'},{'agent_version':1},{'output_port':'artifact'}])
def test_projection_library_still_rejects_saved_workflow_references_and_invalid_ports(settings):
    base={'operation':'lookup','arguments_json':'{"value":{},"path":[]}','output_port':'table',**settings}
    with pytest.raises(ValueError):node_library.NodeSpec.model_validate({'kind':'processing','name':'표 선택','purpose':'JSON에서 표를 선택합니다.','tool':'json_transform','settings':base})


def test_reused_workflow_receives_runtime_uploaded_file_during_compile(tmp_path):
    from infrastructure.persistence.ids import uid
    identifier=uid();content=b'Uploaded input for child';digest=hashlib.sha256(content).hexdigest()
    originals=tmp_path/'builder-originals';originals.mkdir();relative='builder-originals/'+digest+'.txt'
    (tmp_path/relative).write_bytes(content)
    read=node('read','read_document',operation='extract_content',arguments_json=json.dumps({'asset':{'asset_id':identifier},'format':'txt'}))
    child_store,child_id,_=saved(tmp_path,{'entry':'read','nodes':[read],'edges':[]})
    child_store.add_asset({'id':identifier,'kind':'document','name':'uploaded.txt','format':'txt','original_path':relative,'hash':digest},[])
    child_store.close()
    reuse=node('child','saved_workflow',agent_id=child_id,agent_version=1,output_node='read',output_port='text')
    from tests.design.test_builder import initial,draft
    from contracts.models import AgentDraft
    store=Storage(tmp_path);child=store.agent(child_id)
    parent=initial();parent['credential_id']=child['credential_id'];parent['model']=child['model']
    config={**draft(),'workflow':{'entry':'child','nodes':[reuse],'edges':[]},'tool_ids':['saved_workflow'],'document_ids':[]}
    parent.update(draft=AgentDraft.model_validate(config).model_dump(),confirmed=True)
    parent_id=store.save_agent(parent)
    try:
        success=run_workflow(store,parent_id,'실행','unused',file_ids=[identifier])
        assert success['status']=='succeeded',success.get('error')
        assert success['outputs']['child']['text']==content.decode()
        blocked=run_workflow(store,parent_id,'실행','unused')
        assert blocked['status']=='failed'
    finally:store.close()
