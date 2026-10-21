"""Transport compatibility and actionable connection errors; no business policy."""
from agent_builder.ollama_chat_model import OllamaChatOpenAI
from agent_builder.runtime import public_error
from agent_builder.nodes.conversation import make_conversation
from agent_builder.test_deep_agent_conversation import state, draft
import pytest


def test_ollama_final_payload_omits_empty_tool_controls():
    model=OllamaChatOpenAI(model='qwen3:8b',api_key='test',base_url='http://127.0.0.1:1/v1')
    payload=model._get_request_payload('test',tools=[],tool_choice='auto',parallel_tool_calls=False,
        response_format={'type':'json_object'})
    assert not {'tools','tool_choice','parallel_tool_calls'} & payload.keys()
    assert payload['response_format']=={'type':'json_object'}



def test_remote_connection_errors_are_actionable_and_keys_are_hidden(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_LLM_API_KEY','dedicated-test-key')
    import httpx
    from langchain_openai.chat_models.base import OpenAIConnectionError,OpenAITimeoutError
    request=httpx.Request('POST','https://example.invalid/v1/chat/completions')
    error=OpenAIConnectionError(request=request)
    assert 'RunPod가 실행 중인지' in public_error(error)
    assert 'example.invalid' not in public_error(error)
    timeout=OpenAITimeoutError(request=request)
    assert '시간이 초과' in public_error(timeout)
    assert 'dedicated-test-key' not in public_error(ValueError('bad dedicated-test-key'))
    from openai import InternalServerError
    unavailable=InternalServerError('private upstream body',response=httpx.Response(503,request=request),body=None)
    assert 'RunPod 실행 상태' in public_error(unavailable)
    assert 'private upstream body' not in public_error(unavailable)




def test_unsupported_settings_are_not_exposed_as_raw_provider_errors():
    from langchain.agents.structured_output import StructuredOutputValidationError
    from langchain_core.messages import AIMessage
    error=StructuredOutputValidationError('AgentDraft',ValueError('private provider body'),AIMessage(content='{}'))
    result=public_error(error)
    assert '구조화 응답' in result
    assert 'private provider body' not in result
