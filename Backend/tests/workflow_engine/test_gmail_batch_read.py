"""Bounded Gmail reads and the two-node summary flow, with offline account fixtures."""
import base64
import json
from types import SimpleNamespace

import pytest

from service.dispatch import handle
from tests.workflow_engine.test_workflow import bind, edge, node, saved
from workflow_engine.catalog.canonical import operation_spec, ports
from workflow_engine.unified_tools import dispatcher
from workflow_engine.unified_tools.expansion.google_adapters import GoogleApiError, GoogleJsonTransport, read_gmail_messages
from workflow_engine.workflow import workflow_errors


def message(id,text=None):
    payload={'mimeType':'text/plain','headers':[{'name':'Subject','value':'제목 '+id},
                                              {'name':'From','value':'sender@example.com'}]}
    if text is not None:payload['body']={'data':base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')}
    return {'id':id,'threadId':'thread-'+id,'internalDate':'1234567890','payload':payload}


def test_read_messages_preserves_real_ids_and_order_without_adding_a_business_tool():
    calls=[]
    ids=['m5','m4','m3','m2','m1']

    def call(operation,args):
        calls.append((operation,args))
        if operation=='email.list':return {'messages':[{'id':id} for id in ids],'nextPageToken':'next'}
        assert operation=='email.get' and args['format']=='full'
        return message(args['message_id'],'본문 '+args['message_id'])

    result=read_gmail_messages(call)
    assert calls[0]==('email.list',{'query':None,'max_results':5})
    assert [args['message_id'] for _,args in calls[1:]]==ids
    assert [d['source_id'] for d in result['documents']]==['gmail:'+id for id in ids]
    assert all('본문 '+id in d['text'] for id,d in zip(ids,result['documents']))
    assert result['returned_count']==5 and result['requested_count']==5 and result['complete']
    assert result['nextPageToken']=='next'
    spec=operation_spec('email.list','read_messages')
    assert ports('email.list',spec)[1]['documents']=='documents'
    assert operation_spec('email.list',None)['id']=='execute'
    assert dispatcher.resolve('email.list')[1]['id']=='execute'


@pytest.mark.parametrize('count',[0,2])
def test_read_messages_handles_fewer_or_zero_matching_messages(count):
    calls=[]

    def call(operation,args):
        calls.append(operation)
        if operation=='email.list':return {'messages':[{'id':str(i)} for i in range(count)]}
        return message(args['message_id'],'실제 본문')

    result=read_gmail_messages(call,query='is:unread',max_results=5)
    assert result['returned_count']==len(result['documents'])==count
    assert len(calls)==count+1
    assert result['complete']


@pytest.mark.parametrize('limit',[0,11,-1,True,'5',1.5])
def test_read_messages_rejects_out_of_bound_counts_before_any_provider_call(limit):
    def call(*args):pytest.fail('Invalid batch made an external call')
    with pytest.raises(ValueError,match='1~10'):read_gmail_messages(call,max_results=limit)
    step=node('mail','email.list',operation='read_messages',arguments_json=json.dumps({'max_results':limit}))
    assert workflow_errors({'entry':'mail','nodes':[step],'edges':[]},['email.list'],[])


@pytest.mark.parametrize('status',[401,403,404,500])
def test_read_messages_never_returns_a_successful_partial_batch(status):
    calls=[]

    def call(operation,args):
        calls.append((operation,args))
        if operation=='email.list':return {'messages':[{'id':'a'},{'id':'b'},{'id':'c'}]}
        if args['message_id']=='b':raise GoogleApiError(status)
        return message(args['message_id'],'본문')

    error=GoogleApiError if status in {401,403} else ValueError
    with pytest.raises(error):read_gmail_messages(call)
    assert [args['message_id'] for operation,args in calls if operation=='email.get']==['a','b']


def test_read_messages_marks_unreadable_and_truncated_bodies_and_bounds_total_context():
    def call(operation,args):
        if operation=='email.list':return {'messages':[{'id':str(i)} for i in range(10)]}
        return message(args['message_id'],None if args['message_id']=='0' else '긴 본문 '*10000)

    result=read_gmail_messages(call,max_results=10)
    assert result['returned_count']==len(result['documents'])==10
    assert result['body_unavailable_message_ids']==['0']
    assert result['documents'][0]['body_unavailable'] is True
    assert '텍스트 본문이 없습니다' in result['documents'][0]['text']
    assert result['truncated_message_ids']==[str(i) for i in range(1,10)]
    assert sum(len(d['text']) for d in result['documents'])<=40000
    assert all(d['text'].endswith('[이후 본문 생략]') for d in result['documents'][1:])


def test_service_executes_latest_five_summary_with_only_read_requests_and_real_evidence(tmp_path,monkeypatch):
    from workflow_engine import runner
    from workflow_engine.executors import agent_factory

    ids=['m5','m4','m3','m2','m1'];calls=[];inspected=[]
    expected={'text':'다섯 메일의 실제 내용을 확인했습니다.','citations':[]}

    def request(transport,method,path,query=None,**kwargs):
        assert transport.token_provider()=='scoped-read-token'
        assert method=='GET'
        calls.append((path,query))
        if path.endswith('/messages'):
            assert query=={'q':None,'maxResults':5,'pageToken':None}
            return {'messages':[{'id':id} for id in ids]}
        id=path.rsplit('/',1)[-1]
        assert id in ids and query=={'format':'full'}
        return message(id,'실제 본문 '+id)

    class Model:
        def with_structured_output(self,*args,**kwargs):return self
        def invoke(self,*args,**kwargs):return expected

    def grounded_agent(**options):
        evidence=options['tools'][0].invoke({})
        inspected.extend(evidence)
        for id in ids:
            selected=next(e for e in evidence if '실제 본문 '+id in e['quote'])
            expected['citations'].append({'evidence_id':selected['evidence_id']})
        return SimpleNamespace(invoke=lambda *args,**kwargs:{'structured_response':expected})

    monkeypatch.setattr(GoogleJsonTransport,'request',request)
    monkeypatch.setattr(runner,'create_chat_model',lambda **kwargs:Model())
    monkeypatch.setattr(agent_factory,'create_deep_agent',grounded_agent)
    nodes=[node('mail','email.list',operation='read_messages',arguments_json='{"max_results":5}'),
           node('summary','compose_answer',[bind('documents','mail','documents')])]
    workflow={'entry':'mail','nodes':nodes,'edges':[edge('mail','summary')]}
    assert not workflow_errors(workflow,['email.list','compose_answer'],[])
    store,id,_=saved(tmp_path,workflow);store.close()
    result=handle({'action':'test','directory':str(tmp_path),'id':id,'content':'지메일에서 최근 5개 메일 내용 요약해서 정리해줘',
                   'gmail_access_token':'scoped-read-token'})
    assert result['status']=='succeeded',result.get('error')
    assert len(calls)==6 and inspected
    assert [e['source_id'] for e in result['outputs']['summary']['evidence']]==['gmail:'+id for id in ids]
    assert result['outputs']['summary']['text']==expected['text']
    assert result['answer']==expected['text']
    assert '실제 본문 m5' in result['outputs']['mail']['text']
    assert 'scoped-read-token' not in json.dumps(result)
