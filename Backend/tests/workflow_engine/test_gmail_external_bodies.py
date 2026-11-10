"""Offline coverage for Gmail text bodies served by messages.attachments.get."""
import base64
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from workflow_engine.unified_tools.expansion.google_adapters import (
    GoogleApiError, GoogleJsonTransport, gmail_body_reader, gmail_documents, read_gmail_messages,
)


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def part(mime='text/plain',attachment='body-id',**extra):
    return {'mimeType':mime,'body':{'attachmentId':attachment},**extra}


def message(id,payload):
    return {'id':id,'threadId':'thread-'+id,'payload':payload}


def test_external_plain_body_preserves_order_charset_and_source_without_fetching_files_or_html():
    content='분리된 한국어 본문'.encode('euc-kr')
    external=part(headers=[{'name':'Content-Type','value':'text/plain; charset=euc-kr'}])
    external['body']['size']=len(content)
    payload={'mimeType':'multipart/mixed','headers':[{'name':'Subject','value':'본문 제목'}],
             'parts':[part(attachment='file',filename='첨부.txt'),
                      part(attachment='file-without-name',headers=[{'name':'Content-Disposition','value':'attachment'}]),
                      external,part(mime='text/html',attachment='unneeded-html')]}
    original=message('newest',payload);snapshot=deepcopy(original);calls=[]
    def call(operation,args):
        calls.append((operation,args))
        if operation=='email.list':return {'messages':[{'id':'newest'},{'id':'older'}]}
        if operation=='email.get':
            return original if args['message_id']=='newest' else message('older',{'mimeType':'text/plain','body':{'data':encoded(b'older body')}})
        assert operation=='email.attachment.get' and args=={'message_id':'newest','attachment_id':'body-id'}
        return {'data':encoded(content),'size':len(content)}
    result=read_gmail_messages(call)
    assert [item['source_id'] for item in result['documents']]==['gmail:newest','gmail:older']
    assert '분리된 한국어 본문' in result['documents'][0]['text']
    assert result['documents'][0]['file_name']=='본문 제목'
    assert result['body_unavailable_message_ids']==[] and result['complete']
    assert [operation for operation,_ in calls]==['email.list','email.get','email.attachment.get','email.get']
    assert original==snapshot


def test_external_html_is_converted_to_text_and_hidden_content_is_excluded():
    content='<head>숨김</head><p>실제 본문</p><script>실행 금지</script>'.encode()
    calls=[]
    def call(operation,args):
        calls.append((operation,args));return {'data':encoded(content),'size':len(content)}
    documents=gmail_documents(message('html-id',part('text/html')),gmail_body_reader(call))
    assert documents[0]['source_id']=='gmail:html-id'
    assert '실제 본문' in documents[0]['text']
    assert '숨김' not in documents[0]['text'] and '실행 금지' not in documents[0]['text']
    assert calls==[('email.attachment.get',{'message_id':'html-id','attachment_id':'body-id'})]


def test_empty_external_plain_can_fall_back_to_external_html():
    calls=[]
    def call(operation,args):
        calls.append(args['attachment_id'])
        data=b'' if args['attachment_id']=='empty' else b'<p>fallback</p>'
        return {'data':encoded(data),'size':len(data)}
    payload={'mimeType':'multipart/alternative','parts':[part(attachment='empty'),part('text/html','html')]}
    assert 'fallback' in gmail_documents(message('m',payload),gmail_body_reader(call))[0]['text']
    assert calls==['empty','html']


def test_inline_body_and_file_attachment_do_not_trigger_extra_requests():
    payload={'mimeType':'multipart/mixed','parts':[
        {'mimeType':'text/plain','body':{'data':encoded(b'inline text'),'attachmentId':'redundant'}},
        part(filename='file.txt'),part(headers=[{'name':'Content-Disposition','value':'attachment; filename="file.txt"'}]),
    ]}
    def unused(*args):pytest.fail('An inline body or a real file attachment triggered an extra request')
    assert 'inline text' in gmail_documents(message('m',payload),gmail_body_reader(unused))[0]['text']


def test_external_reader_caches_only_within_the_same_message_id():
    calls=[]
    def call(operation,args):
        calls.append(args);return {'data':encoded(args['message_id'].encode())}
    reader=gmail_body_reader(call)
    payload={'mimeType':'multipart/mixed','parts':[part(),part()]}
    assert gmail_documents(message('one',payload),reader)[0]['text'].endswith('one\none')
    assert gmail_documents(message('two',part()),reader)[0]['text'].endswith('two')
    assert calls==[{'message_id':'one','attachment_id':'body-id'},{'message_id':'two','attachment_id':'body-id'}]


@pytest.mark.parametrize('response',[None,[],{}, {'data':None},{'data':'***'},
                                   {'data':encoded(b'x'),'size':2},
                                   {'data':encoded(b'x'),'size':True},
                                   {'data':encoded(b'x'),'size':-1}])
def test_malformed_external_body_response_aborts_the_batch_instead_of_marking_body_unavailable(response):
    calls=[]
    def call(operation,args):
        calls.append((operation,args))
        if operation=='email.list':return {'messages':[{'id':'first'},{'id':'bad'},{'id':'never'}]}
        if operation=='email.get':
            return message(args['message_id'],part() if args['message_id']=='bad' else {'mimeType':'text/plain','body':{'data':encoded(b'first')}})
        return response
    with pytest.raises(ValueError,match='전체 요약을 중단'):
        read_gmail_messages(call)
    assert [args['message_id'] for operation,args in calls if operation=='email.get']==['first','bad']


@pytest.mark.parametrize('failure',[GoogleApiError(401),GoogleApiError(403),GoogleApiError(404),GoogleApiError(500),ConnectionError('offline')])
def test_external_read_failure_preserves_auth_errors_and_never_returns_a_partial_batch(failure):
    calls=[]
    def call(operation,args):
        calls.append((operation,args))
        if operation=='email.list':return {'messages':[{'id':'first'},{'id':'bad'},{'id':'never'}]}
        if operation=='email.get':return message(args['message_id'],part() if args['message_id']=='bad' else {'mimeType':'text/plain','body':{'data':encoded(b'first')}})
        raise failure
    expected=GoogleApiError if isinstance(failure,GoogleApiError) and failure.status in {401,403} else ValueError
    with pytest.raises(expected):read_gmail_messages(call)
    assert [args['message_id'] for operation,args in calls if operation=='email.get']==['first','bad']


@pytest.mark.parametrize('body',[{'attachmentId':None},{'attachmentId':''},{'attachmentId':7},
                              {'attachmentId':'body','size':True},{'attachmentId':'body','size':-1},
                              {'attachmentId':'body','size':10_000_001}])
def test_invalid_or_oversized_external_part_is_rejected_before_fetch(body):
    def unused(*args):pytest.fail('Invalid external-body metadata made a provider call')
    with pytest.raises(ValueError):
        gmail_documents(message('m',{'mimeType':'text/plain','body':body}),gmail_body_reader(unused))


def test_external_body_size_mismatch_and_missing_reader_fail_explicitly():
    current=message('m',{'mimeType':'text/plain','body':{'attachmentId':'body','size':2}})
    with pytest.raises(ValueError,match='크기가 일치'):
        gmail_documents(current,gmail_body_reader(lambda *args:{'data':encoded(b'x'),'size':1}))
    with pytest.raises(ValueError,match='연결'):
        gmail_documents(current)


def test_external_request_budget_applies_to_each_message_without_dropping_later_mail():
    calls=[]
    def call(operation,args):
        calls.append((operation,args))
        if operation=='email.list':return {'messages':[{'id':'a'},{'id':'b'}]}
        if operation=='email.get':return message(args['message_id'],{'mimeType':'multipart/mixed','parts':[part(attachment=str(i)) for i in range(11)]})
        return {'data':encoded(b'x'),'size':1}
    result=read_gmail_messages(call)
    assert result['returned_count']==2
    assert sum(operation=='email.attachment.get' for operation,_ in calls)==22


def test_external_byte_budget_rejects_the_next_known_oversized_part_before_fetch():
    content=b'x'*6_000_000;calls=[]
    def call(operation,args):
        calls.append(args);return {'data':encoded(content),'size':len(content)}
    reader=gmail_body_reader(call)
    assert len(reader('a',{'attachmentId':'large','size':len(content)}))==len(content)
    with pytest.raises(ValueError,match='한도'):
        reader('b',{'attachmentId':'next','size':5_000_000})
    assert len(calls)==1


def test_external_body_keeps_single_message_and_batch_character_limits():
    content=('긴 본문 '*10000).encode()
    def call(operation,args):
        if operation=='email.list':return {'messages':[{'id':str(i)} for i in range(10)]}
        if operation=='email.get':return message(args['message_id'],part())
        return {'data':encoded(content),'size':len(content)}
    result=read_gmail_messages(call,max_results=10)
    assert all(len(item['text'])<=8000 for item in result['documents'])
    assert result['truncated_message_ids']==[str(i) for i in range(10)]
    assert all(item['text'].endswith('[이후 본문 생략]') for item in result['documents'])
    single=gmail_documents(message('single',part()),gmail_body_reader(call))[0]
    assert single['text'].endswith('[이후 본문 생략]') and len(single['text'])<=30000+len('\n[이후 본문 생략]')


@pytest.mark.parametrize('mode',['batch','individual'])
def test_service_summary_reads_external_body_with_only_read_requests_and_real_evidence(tmp_path,monkeypatch,mode):
    from service.dispatch import handle
    from tests.workflow_engine.test_workflow import bind, edge, node, saved
    from workflow_engine import runner
    from workflow_engine.executors import agent_factory
    content='외부 파트의 실제 메일 본문';calls=[];expected={'text':'실제 본문을 확인한 요약입니다.','citations':[]}
    def request(transport,method,path,query=None,**kwargs):
        assert transport.token_provider()=='offline-scoped-token' and method=='GET'
        calls.append((path,query))
        if path.endswith('/messages'):return {'messages':[{'id':'external'}]}
        if path.endswith('/messages/external'):
            return message('external',{'mimeType':'multipart/mixed','parts':[part(),part(filename='do-not-read.txt',attachment='file')]})
        assert path.endswith('/messages/external/attachments/body-id') and query is None
        return {'data':encoded(content.encode()),'size':len(content.encode())}
    class Model:
        def with_structured_output(self,*args,**kwargs):return self
        def invoke(self,*args,**kwargs):return expected
    def grounded_agent(**options):
        evidence=options['tools'][0].invoke({})
        selected=next(item for item in evidence if content in item['quote'])
        expected['citations']=[{'evidence_id':selected['evidence_id']}]
        return SimpleNamespace(invoke=lambda *args,**kwargs:{'structured_response':expected})
    monkeypatch.setattr(GoogleJsonTransport,'request',request)
    monkeypatch.setattr(runner,'create_chat_model',lambda **kwargs:Model())
    monkeypatch.setattr(agent_factory,'create_deep_agent',grounded_agent)
    mail=node('mail','email.list',operation='read_messages',arguments_json='{"max_results":1}') if mode=='batch' else node('mail','email.get',operation='execute',arguments_json='{"message_id":"external"}')
    workflow={'entry':'mail','nodes':[mail,node('summary','compose_answer',[bind('documents','mail','documents')])],
              'edges':[edge('mail','summary')]}
    store,identifier,_=saved(tmp_path,workflow);store.close()
    result=handle({'action':'test','directory':str(tmp_path),'id':identifier,'content':'메일 본문을 요약해주세요.',
                   'gmail_access_token':'offline-scoped-token'})
    assert result['status']=='succeeded',result.get('error')
    assert result['outputs']['mail']['documents'][0]['source_id']=='gmail:external'
    assert content in result['outputs']['mail']['text']
    assert result['outputs']['summary']['text']==expected['text'] and expected['text'] in result['answer']
    assert len(calls)==(3 if mode=='batch' else 2)
    assert 'offline-scoped-token' not in json.dumps(result)
