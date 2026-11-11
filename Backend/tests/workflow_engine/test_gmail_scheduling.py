"""Offline contracts for relative Gmail periods and general scheduled inputs."""
import base64
from datetime import datetime,timezone
import json

import pytest

from contracts.models import AgentDraft
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from workflow_engine.scheduling.gmail_schedule import PREVIOUS_DAY_QUERY,resolve_gmail_query
from workflow_engine.scheduling.schedules import Schedules,source_required,required_connections,preflight_schedule
from workflow_engine.unified_tools.expansion.google_adapters import GoogleJsonTransport,read_gmail_messages

SPEC={'frequency':'daily','timezone':'Asia/Seoul','hour':9,'minute':0,'weekday':None}


def initial():
    return {'session_id':uid(),'model':'gpt-4.1-mini','credential_id':uid(),'node_library':[],
            'base_agent_id':None,'base_version':0,'messages':[{'role':'user','content':'자료를 요약하세요.'}]}


def draft():
    return {'name':'예약 테스트','purpose':'저장된 설정을 예약 실행합니다.','instructions':'자료를 요약하세요.',
            'tool_ids':[],'document_ids':[],'requirements':[{'description':'요약','source_id':'user','quote':'자료를 요약하세요.'}],
            'questions':[],'unsupported':[],'explanation':'저장된 설정으로 실행합니다.',
            'execution_plan':{'filters':[],'sort':[],'match':'all','missing_policy':'keep','duplicate_policy':'keep','duplicate_field':None,'output_format':'json'}}


def node(id,tool,bindings=None,**settings):
    from contracts.models import NodeSettings
    return {'id':id,'tool':tool,'label':id,'bindings':bindings or [],'settings':NodeSettings.model_validate({
        'file_ids':[],'query':None,'fields':[],'instructions':None,'format':None,'question':None,'condition_field':None,'condition_operator':None,**settings}).model_dump()}


def bind(input,id,output):return {'input':input,'node_id':id,'output':output}


def edge(source,target):return {'source':source,'target':target,'branch':'next'}


@pytest.fixture(autouse=True)
def local_storage(monkeypatch):
    monkeypatch.setenv('A4B_STORAGE','sqlite')
    monkeypatch.setenv('EASYFLOW_PUBLIC_URL','http://localhost')
    monkeypatch.delenv('A4B_BUSINESS_RECORDS',raising=False)


def gmail_config():
    nodes=[node('mail','email.list',operation='read_messages',arguments_json=json.dumps({'query':PREVIOUS_DAY_QUERY,'max_results':100})),
           node('summary','compose_answer',[bind('documents','mail','documents')],instructions='조회한 모든 메일의 본문을 요약하세요.')]
    return {**draft(),'name':'전날 메일 요약','instructions':'전날 수신 메일을 요약하세요.',
            'tool_ids':['email.list','compose_answer'],'document_ids':[],
            'schedule':SPEC,'report_destination':'reports',
            'workflow':{'entry':'mail','nodes':nodes,'edges':[edge('mail','summary')]}}


def save(store,config):
    return store.save_agent({**initial(),'confirmed':True,'draft':AgentDraft.model_validate(config).model_dump()})


def test_previous_day_is_a_calendar_day_in_the_saved_timezone_including_year_and_dst():
    def period(reference,zone):
        query=resolve_gmail_query(PREVIOUS_DAY_QUERY,{'schedule':{'timezone':zone},'_execution_time':reference})
        assert query.endswith('-in:sent -in:drafts')
        return [datetime.fromtimestamp(int(part.split(':')[1]),timezone.utc) for part in query.split()[:2]]
    start,end=period('2026-01-01T00:00:00Z','Asia/Seoul')
    assert start.isoformat()=='2025-12-30T15:00:00+00:00'
    assert end.isoformat()=='2025-12-31T15:00:00+00:00'
    start,end=period('2026-03-09T13:00:00Z','America/New_York')
    assert (end-start).total_seconds()==23*3600
    start,end=period('2026-11-02T14:00:00Z','America/New_York')
    assert (end-start).total_seconds()==25*3600
    assert resolve_gmail_query('is:unread',{})=='is:unread'
    with pytest.raises(ValueError,match='기준 시각'):
        resolve_gmail_query(PREVIOUS_DAY_QUERY,{'schedule':SPEC})


def test_gmail_reader_paginates_and_keeps_every_selected_message_with_an_explicit_limit():
    calls=[]
    def call(operation,args):
        calls.append((operation,args))
        if operation=='email.list':
            offset=100 if args.get('page_token')=='second' else 0
            count=args['max_results']
            return {'messages':[{'id':str(i)} for i in range(offset,offset+count)],'nextPageToken':'second' if not offset else 'third'}
        return {'id':args['message_id'],'payload':{'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(('본문 '+args['message_id']).encode()).decode()}}}
    result=read_gmail_messages(call,query='after:1 before:2',max_results=125)
    assert [args for operation,args in calls if operation=='email.list']==[
        {'query':'after:1 before:2','max_results':100},
        {'query':'after:1 before:2','max_results':25,'page_token':'second'}]
    assert len(result['documents'])==result['returned_count']==125
    assert result['documents'][-1]['source_id']=='gmail:124'
    assert result['limit_reached'] and not result['search_complete']
    assert result['nextPageToken']=='third' and result['pages_read']==2


def test_gmail_pagination_rejects_repeated_tokens_and_duplicate_ids_without_partial_success():
    def repeated(operation,args):
        if operation=='email.list':return {'messages':[],'nextPageToken':'same'}
        pytest.fail('Bad pagination must fail before reading bodies')
    with pytest.raises(ValueError,match='페이지'):read_gmail_messages(repeated)
    def duplicate(operation,args):
        if operation=='email.list':return {'messages':[{'id':'same'}],'nextPageToken':'second'}
        pytest.fail('Duplicate IDs must fail before reading bodies')
    with pytest.raises(ValueError,match='ID'):read_gmail_messages(duplicate)


def test_nonfile_workflow_activates_and_executes_without_a_gmail_or_table_exception(tmp_path):
    from workflow_engine.executors.canonical_operation import execute
    store=Storage(tmp_path)
    step=node('calculate','scalar_calculate',operation='evaluate',arguments_json='{"formula":"2+3","variables":{}}')
    config={**draft(),'tool_ids':['scalar_calculate'],'document_ids':[],'schedule':SPEC,'report_destination':'reports',
            'workflow':{'entry':'calculate','nodes':[step],'edges':[]}}
    agent_id=save(store,config)
    schedules=Schedules(store)
    assert schedules.source(agent_id) is None
    assert schedules.activate(agent_id,True)['active']
    assert not source_required(store.agent(agent_id)['config'],store)
    assert required_connections(config)==[]
    schedules.activate(agent_id,False)
    schedules.enqueue_test(agent_id,0)
    [job]=schedules.claim_due()
    result=execute(step,{'outputs':{},'file_ids':[]},store,config,None)
    assert result['result']==5
    assert job['agent_snapshot']['config']['document_ids']==[]
    store.close()


def test_switching_to_nonfile_schedule_detaches_mapping_and_idempotent_save_preserves_activation(tmp_path):
    store=Storage(tmp_path)
    try:
        asset={'id':uid(),'name':'table.csv','kind':'table','format':'csv','columns':['amount'],'text':'','profile':{}}
        store.add_asset(asset,[{'amount':5}])
        config={**draft(),'document_ids':[asset['id']],'tool_ids':['read_table','write_result'],'schedule':SPEC,'report_destination':'reports',
                'workflow':{'entry':'read','nodes':[node('read','read_table'),node('write','write_result',[bind('table','read','table')])],'edges':[edge('read','write')]}}
        agent_id=save(store,config)
        schedules=Schedules(store)
        assert schedules.source(agent_id)['asset_id']==asset['id']
        step=node('calculate','scalar_calculate',operation='evaluate',arguments_json='{"formula":"2+3","variables":{}}')
        config={**draft(),'tool_ids':['scalar_calculate'],'schedule':SPEC,'report_destination':'reports',
                'workflow':{'entry':'calculate','nodes':[step],'edges':[]}}
        state={**initial(),'base_agent_id':agent_id,'base_version':1,'confirmed':True,'draft':AgentDraft.model_validate(config).model_dump()}
        store.save_agent(state)
        assert schedules.source(agent_id) is None
        assert store.asset(asset['id'])['name']=='table.csv'
        with pytest.raises(ValueError,match='버전'):schedules.activate(agent_id,True)
        schedules.configure(agent_id,spec=SPEC)
        schedules.activate(agent_id,True)
        before=schedules.get(agent_id)
        assert store.save_agent(state)==agent_id
        assert schedules.get(agent_id)==before and before['active']
    finally:store.close()


def test_saved_multiple_documents_can_schedule_without_a_single_managed_source(tmp_path):
    store=Storage(tmp_path)
    try:
        ids=[]
        for index in range(2):
            asset={'id':uid(),'name':f'notes{index}.txt','kind':'document','format':'txt','text':'확인된 문서 본문','pages':['확인된 문서 본문'],'columns':[]}
            store.add_asset(asset,[]);ids.append(asset['id'])
        config=gmail_config()
        config.update(tool_ids=['read_document','compose_answer'],document_ids=ids,
                      workflow={'entry':'read','nodes':[node('read','read_document'),node('summary','compose_answer',[bind('documents','read','documents')])],'edges':[edge('read','summary')]})
        agent_id=save(store,config)
        schedules=Schedules(store)
        assert schedules.source(agent_id) is None
        assert not source_required(store.agent(agent_id)['config'],store)
        assert schedules.activate(agent_id,True)['active']
        assert schedules.enqueue_test(agent_id,0)
    finally:store.close()


def test_missing_files_and_nested_human_input_still_block_unattended_schedules(tmp_path):
    store=Storage(tmp_path)
    try:
        config=gmail_config()
        config.update(tool_ids=['read_document','compose_answer'],workflow={'entry':'read','nodes':[node('read','read_document'),node('summary','compose_answer',[bind('documents','read','documents')])],'edges':[edge('read','summary')]})
        agent_id=save(store,config)
        assert source_required(store.agent(agent_id)['config'],store)
        with pytest.raises(ValueError,match='문서'):Schedules(store).activate(agent_id,True)
        with pytest.raises(ValueError,match='문서'):Schedules(store).enqueue_test(agent_id,0)
        agent={'config':gmail_config(),'workflow_dependencies':{'nested':{'config':{'workflow':{'nodes':[node('ask','ask_user',question='승인할까요?')]}},'workflow_dependencies':{}}}}
        with pytest.raises(ValueError,match='사용자 응답'):preflight_schedule(store,agent)
    finally:store.close()


def test_gmail_scheduled_run_and_retry_keep_the_original_due_date_and_no_files(tmp_path,monkeypatch):
    from service.dispatch import handle
    from workflow_engine import runner
    store=Storage(tmp_path);agent_id=save(store,gmail_config());schedules=Schedules(store)
    assert not source_required(store.agent(agent_id)['config'],store)
    assert schedules.activate(agent_id,True)['active']
    schedules.activate(agent_id,False)
    job=schedules.enqueue_test(agent_id,0)
    due='2026-11-10T00:00:00+00:00'
    job['due_at']=due
    store.db.execute('UPDATE builder_schedule_jobs SET json=? WHERE {ws} AND id=?',(json.dumps(job),job['id']))
    store.db.commit()
    [claimed]=schedules.claim_due(datetime(2026,11,10,0,0,1,tzinfo=timezone.utc))
    store.close();queries=[];fail=True
    def request(transport,method,path,query=None,**kwargs):
        nonlocal fail
        assert transport.token_provider()=='fixture-scoped-access' and method=='GET'
        queries.append(query['q'])
        if fail:
            fail=False
            raise ValueError('fixture temporary read failure')
        return {'messages':[]}
    monkeypatch.setattr(GoogleJsonTransport,'request',request)
    monkeypatch.setattr(runner,'create_chat_model',lambda **kwargs:object())
    result=handle({'directory':str(tmp_path),'action':'scheduled_run','id':claimed['id'],'lease_token':claimed['lease_token'],'gmail_access_token':'fixture-scoped-access'})
    assert result['status']=='failed'
    monkeypatch.setattr(runner,'now',lambda:'2026-11-12T00:00:00+00:00')
    retried=handle({'directory':str(tmp_path),'action':'retry_run','id':result['id'],'gmail_access_token':'fixture-scoped-access'})
    assert retried['status']=='succeeded',retried.get('error')
    assert retried['scheduled_for']==due and retried['file_ids']==[]
    assert queries[0]==queries[1]==resolve_gmail_query(PREVIOUS_DAY_QUERY,{'schedule':SPEC,'_execution_time':due})
    assert '2026-11-09' in retried['answer'] and '요약할 내용이 없습니다' in retried['answer']
    assert 'fixture-scoped-access' not in json.dumps(retried)


def test_empty_page_with_a_fresh_token_advances_and_default_limit_is_100():
    calls=[]
    def call(operation,args):
        calls.append((operation,args))
        if operation=='email.list':
            if not args.get('page_token'):return {'messages':[],'nextPageToken':'second'}
            return {'messages':[{'id':'one'}]}
        return {'id':'one','payload':{'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(b'body').decode()}}}
    result=read_gmail_messages(call)
    assert result['requested_count']==100 and result['returned_count']==1
    assert result['search_complete'] and not result['limit_reached']
    assert calls[1]==('email.list',{'query':None,'max_results':100,'page_token':'second'})


def test_pinned_child_gmail_workflow_receives_the_same_execution_clock():
    from workflow_engine.executors.control.saved_workflow import child_configuration
    parent={'_execution_time':'2026-11-10T00:00:00+00:00'}
    child=child_configuration({'config':gmail_config(),'workflow_dependencies':{}},parent,[])
    assert child['_execution_time']==parent['_execution_time']
    assert resolve_gmail_query(PREVIOUS_DAY_QUERY,child)==resolve_gmail_query(PREVIOUS_DAY_QUERY,{**parent,'schedule':SPEC})


def test_changing_single_table_to_saved_multiple_documents_ignores_the_old_mapping(tmp_path):
    store=Storage(tmp_path)
    try:
        asset={'id':uid(),'name':'table.csv','kind':'table','format':'csv','columns':['amount'],'text':'','profile':{}}
        store.add_asset(asset,[{'amount':5}])
        config={**draft(),'document_ids':[asset['id']],'tool_ids':['read_table','write_result'],'schedule':SPEC,'report_destination':'reports',
                'workflow':{'entry':'read','nodes':[node('read','read_table'),node('write','write_result',[bind('table','read','table')])],'edges':[edge('read','write')]}}
        agent_id=save(store,config);schedules=Schedules(store)
        assert schedules.source(agent_id)['asset_id']==asset['id']
        documents=[]
        for index in range(2):
            document={'id':uid(),'name':f'document{index}.txt','kind':'document','format':'txt','text':'확인된 내용','pages':['확인된 내용'],'columns':[]}
            store.add_asset(document,[]);documents.append(document['id'])
        config=gmail_config()
        config.update(tool_ids=['read_document','compose_answer'],document_ids=documents,
                      workflow={'entry':'read','nodes':[node('read','read_document'),node('summary','compose_answer',[bind('documents','read','documents')])],'edges':[edge('read','summary')]})
        store.save_agent({**initial(),'base_agent_id':agent_id,'base_version':1,'confirmed':True,'draft':AgentDraft.model_validate(config).model_dump()})
        assert schedules.source(agent_id) is None and schedules.execution_source(store.agent(agent_id)) is None
        schedules.configure(agent_id,spec=SPEC)
        assert schedules.activate(agent_id,True)['active']
        assert store.asset(asset['id'])['kind']=='table'
    finally:store.close()
