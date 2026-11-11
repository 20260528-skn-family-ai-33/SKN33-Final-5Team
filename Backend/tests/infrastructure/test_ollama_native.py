"""Exercise the real SDK/LangChain against native Ollama HTTP responses."""
import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel
from langchain_core.tools import tool
from infrastructure.llm.ollama_chat_model import OllamaChatOpenAI


class Answer(BaseModel):
    value: int


def native(content='', **extra):
    return dict(model='final664', message=dict(role='assistant', content=content),
                done=True, done_reason='stop', prompt_eval_count=12, eval_count=4, **extra)


def response(body, streaming):
    if streaming:
        return httpx.Response(200, content=json.dumps(body)+'\n', headers={'content-type':'application/x-ndjson'})
    return httpx.Response(200, json=body)


@pytest.mark.parametrize('streaming', [False, True])
def test_real_sdk_structured_output_and_strict_native_wire(monkeypatch, streaming):
    monkeypatch.setenv('A4B_OLLAMA_NUM_CTX', '32768')
    calls=[]
    def handle(request):
        payload=json.loads(request.content);calls.append(payload)
        assert request.url.path=='/gateway/api/chat'
        assert request.headers['authorization']=='Bearer test'
        assert request.extensions['timeout']['read']==300
        return response(native('{"value":7}'), streaming)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model=OllamaChatOpenAI(model='final664',api_key='test',base_url='https://example.test/gateway/v1',
            http_client=client, streaming=streaming, stream_usage=True, timeout=300, max_retries=0,
            max_tokens=6000, temperature=0, top_p=.8, seed=42)
        result=model.with_structured_output(Answer, method='json_schema').invoke('Return seven.')
    assert result.value==7
    assert len(calls)==1
    body=calls[0]
    assert body['shift'] is body['truncate'] is body['think'] is False
    assert body['keep_alive']==-1
    assert body['options']==dict(num_ctx=32768,num_predict=6000,temperature=0,top_p=.8,seed=42)
    assert body['format']['properties']['value']['type']=='integer'


@pytest.mark.parametrize('streaming', [False, True])
def test_async_real_sdk(monkeypatch, streaming):
    monkeypatch.setenv('A4B_OLLAMA_NUM_CTX','32768')
    async def run():
        seen=[]
        async def handle(request):
            seen.append(json.loads(request.content))
            assert request.url.path=='/api/chat'
            return response(native('{"value":11}'),streaming)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            model=OllamaChatOpenAI(model='final664',api_key='test',base_url='https://example.test/v1',
                http_async_client=client,streaming=streaming,max_retries=0)
            result=await model.with_structured_output(Answer,method='json_schema').ainvoke('Return eleven.')
            assert result.value==11 and seen[0]['shift'] is False
    asyncio.run(run())


@pytest.mark.parametrize('streaming', [False, True])
def test_length_finish_is_not_accepted_as_success(streaming):
    from openai import LengthFinishReasonError
    def handle(request):
        body=native('{"value":');body['done_reason']='length'
        return response(body,streaming)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model=OllamaChatOpenAI(model='final664',api_key='test',base_url='http://example.test/v1',
            http_client=client,streaming=streaming,max_retries=0)
        with pytest.raises(LengthFinishReasonError):
            model.with_structured_output(Answer,method='json_schema').invoke('Return an integer.')


@pytest.mark.parametrize('body', [{'error':'inference failed'},
    {'model':'final664','message':{'content':'incomplete'},'done':False}])
def test_broken_stream_fails_instead_of_returning_partial_text(body):
    from openai import APIConnectionError
    with httpx.Client(transport=httpx.MockTransport(lambda r:response(body,True))) as client:
        model=OllamaChatOpenAI(model='final664',api_key='test',base_url='http://example.test/v1',
            http_client=client,streaming=True,max_retries=0)
        with pytest.raises(APIConnectionError):model.invoke('Continue.')


def test_native_http_error_keeps_sdk_error_status():
    from openai import NotFoundError
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(404,json={'error':'model missing'}))) as client:
        model=OllamaChatOpenAI(model='missing',api_key='test',base_url='http://example.test/v1',http_client=client,max_retries=0)
        with pytest.raises(NotFoundError):model.invoke('Hello')


@pytest.mark.parametrize('streaming',[False,True])
def test_real_deepagent_tool_roundtrip_then_structured_result(streaming):
    from deepagents import create_deep_agent
    from langchain.agents.structured_output import ProviderStrategy
    calls=[];executed=[]
    @tool
    def double(value: int) -> int:
        """Double a number."""
        executed.append(value)
        return value*2
    def handle(request):
        body=json.loads(request.content);calls.append(body)
        assert request.url.path=='/api/chat' and body['shift'] is False
        answer=native('{"value":14}')
        if len(calls)==1:
            answer=native();answer['message']['tool_calls']=[
                {'id':'native_call_7','function':{'name':'double','arguments':{'value':7}}}]
        else:
            tool_result=next(m for m in body['messages'] if m['role']=='tool')
            assert tool_result['tool_name']=='double'
            assert tool_result['tool_call_id']=='native_call_7'
            assert tool_result['content']=='14'
            assistant=next(m for m in body['messages'] if m.get('tool_calls'))
            assert assistant['tool_calls'][0]['function']['arguments']=={'value':7}
        return response(answer,streaming)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model=OllamaChatOpenAI(model='final664',api_key='test',base_url='http://example.test/v1',
            http_client=client,streaming=streaming,max_retries=0)
        agent=create_deep_agent(model=model,tools=[double],response_format=ProviderStrategy(Answer))
        result=agent.invoke({'messages':[{'role':'user','content':'Double seven with the tool.'}]})
    assert result['structured_response'].value==14
    assert executed==[7] and len(calls)==2
