from infrastructure.llm import model_factory
import pytest


@pytest.mark.parametrize('provider', ['ollama', 'vllm'])
@pytest.mark.parametrize('retries', [0, 1, 2])
def test_local_transport_honors_bounded_requested_retries(monkeypatch, provider, retries):
    monkeypatch.setenv('A4B_MAIN_PROVIDER', provider)
    monkeypatch.setenv('A4B_MAIN_BASE_URL', 'http://127.0.0.1:8000/v1')
    target = 'ollama_chat_model.OllamaChatOpenAI' if provider == 'ollama' else 'qwen_chat_model.QwenChatOpenAI'
    monkeypatch.setattr('infrastructure.llm.' + target, lambda **kw: kw)
    assert model_factory.create_chat_model('', 'local', max_retries=retries)['max_retries'] == retries


@pytest.mark.parametrize('retries', [-1, 3, True, '1'])
def test_local_transport_rejects_unbounded_retry_settings(monkeypatch, retries):
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL', 'http://127.0.0.1:8000/v1')
    with pytest.raises(ValueError, match='max_retries'):
        model_factory.create_chat_model('', 'local', max_retries=retries)


def test_openai_default_does_not_inherit_local_options(monkeypatch):
    monkeypatch.delenv('A4B_MAIN_PROVIDER', raising=False)
    monkeypatch.setattr(model_factory,'ChatOpenAI',lambda **kw:kw)
    result=model_factory.create_chat_model('test-key','gpt-4.1-mini',timeout=70)
    assert result==dict(api_key='test-key',model='gpt-4.1-mini',timeout=70)


def test_ollama_uses_explicit_local_endpoint_without_vllm_options(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:11434/v1')
    monkeypatch.setattr('infrastructure.llm.ollama_chat_model.OllamaChatOpenAI',lambda **kw:kw)
    result=model_factory.create_chat_model('','qwen3:8b-q4_K_M')
    assert result['base_url']=='http://127.0.0.1:11434/v1'
    assert result['extra_body']=={'think':False}
    assert result['temperature']==0.7 and result['top_p']==0.8


def test_vllm_uses_supplied_transport(monkeypatch):
    from infrastructure.llm import qwen_chat_model
    monkeypatch.setenv('A4B_MAIN_PROVIDER','vllm')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:8000/v1')
    monkeypatch.setattr(qwen_chat_model,'QwenChatOpenAI',lambda **kw:kw)
    result=model_factory.create_chat_model('','Qwen/Qwen3-8B')
    assert result['extra_body']['structured_outputs']['whitespace_pattern']=='[ ]?'
    assert result['streaming'] and result['stream_usage']
    assert result['max_retries']==0


def test_legacy_hints_do_not_treat_finish_as_a_prediction(monkeypatch):
    from ml.advice.tool_recommendation import recommend_for_state
    monkeypatch.setenv('A4B_TOOL_ADVICE_MODE','hint')
    monkeypatch.setenv('A4B_TOOL_ADVICE_BACKEND','legacy')
    result=recommend_for_state({'messages':[dict(role='user',content='stock이 3 이상인 행만 CSV로 저장해줘')],
                              'resources':[dict(kind='table',columns=['code','stock'])]})
    ids=[c['tool_id'] for c in result['ml_candidates']]
    assert 'finish' not in ids and 'saved_workflow' not in ids
    assert len(ids)==5 and 'read_table' in ids


def test_remote_timeout_is_configurable_and_bounded(monkeypatch):
    import pytest
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:11434/v1')
    monkeypatch.setenv('A4B_MAIN_TIMEOUT_S','240')
    monkeypatch.setattr('infrastructure.llm.ollama_chat_model.OllamaChatOpenAI',lambda **kw:kw)
    assert model_factory.create_chat_model('','qwen3:8b',timeout=70)['timeout']==240
    monkeypatch.setenv('A4B_MAIN_TIMEOUT_S','0')
    with pytest.raises(ValueError,match='A4B_MAIN_TIMEOUT_S'):model_factory.create_chat_model('','qwen3:8b')


def test_local_provider_never_reuses_the_openai_key(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:11434/v1')
    monkeypatch.delenv('A4B_MAIN_API_KEY',raising=False)
    monkeypatch.setattr('infrastructure.llm.ollama_chat_model.OllamaChatOpenAI',lambda **kw:kw)
    result=model_factory.create_chat_model('','qwen3:8b')
    assert result['api_key']=='local-not-a-secret'
    monkeypatch.setenv('A4B_MAIN_API_KEY','dedicated-local-key')
    assert model_factory.create_chat_model('','qwen3:8b')['api_key']=='dedicated-local-key'


def test_selected_openai_account_is_not_routed_to_the_local_server(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:11434/v1')
    monkeypatch.setattr(model_factory,'ChatOpenAI',lambda **kw:dict(kw,target='openai'))
    monkeypatch.setattr('infrastructure.llm.ollama_chat_model.OllamaChatOpenAI',lambda **kw:dict(kw,target='local'))
    assert model_factory.create_chat_model('sk-real','gpt-4.1-mini')['target']=='openai'
    assert 'base_url' not in model_factory.create_chat_model('sk-real','gpt-4.1-mini')
    for local_key in ('',None,model_factory.LOCAL_MODEL_API_KEY):
        assert model_factory.create_chat_model(local_key,'qwen3:8b')['target']=='local'


def test_ollama_preserves_explicit_planner_sampling_options(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:11434/v1')
    monkeypatch.setattr('infrastructure.llm.ollama_chat_model.OllamaChatOpenAI',lambda **kw:kw)
    result=model_factory.create_chat_model('','qwen3.5:4b',temperature=0.2,presence_penalty=0)
    assert result['temperature']==0.2 and result['presence_penalty']==0


def test_deployed_finetune_limits_override_local_sampling_only(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','https://example.test/v1')
    monkeypatch.setenv('A4B_OLLAMA_TEMPERATURE','0')
    monkeypatch.setenv('A4B_OLLAMA_NUM_PREDICT','4096')
    monkeypatch.setattr('infrastructure.llm.ollama_chat_model.OllamaChatOpenAI',lambda **kw:kw)
    monkeypatch.setattr(model_factory,'ChatOpenAI',lambda **kw:kw)
    local=model_factory.create_chat_model('','final664',temperature=0.2,max_tokens=9000)
    assert local['temperature']==0 and local['max_tokens']==4096
    assert local['reasoning_effort']=='none' and local['extra_body']['think'] is False
    assert model_factory.create_chat_model('','final664',max_tokens=1000)['max_tokens']==1000
    external=model_factory.create_chat_model('real-key','external',temperature=0.2,max_tokens=9000)
    assert external['temperature']==0.2 and external['max_tokens']==9000


@pytest.mark.parametrize('provider', ['ollama', 'vllm'])
@pytest.mark.parametrize('status,attempts', [(503,2),(401,1)])
def test_local_client_retries_transient_http_once_without_retrying_auth(monkeypatch, provider, status, attempts):
    import httpx
    import json
    from openai import AuthenticationError
    monkeypatch.setenv('A4B_MAIN_PROVIDER', provider)
    monkeypatch.setenv('A4B_MAIN_BASE_URL', 'https://fixture.test/v1')
    monkeypatch.delenv('A4B_MAIN_API_KEY', raising=False)
    monkeypatch.delenv('A4B_OLLAMA_MODEL_ALIASES', raising=False)
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(status, json={'error':{'message':'fixture failure'}}, headers={'retry-after':'0.01'})
        body = dict(id='fixture',object='chat.completion.chunk',created=1,model='fixture',
            choices=[dict(index=0,delta={'role':'assistant','content':'요약 완료'},finish_reason='stop')])
        return httpx.Response(200,text='data: '+json.dumps(body)+'\n\ndata: [DONE]\n\n',
                              headers={'Content-Type':'text/event-stream'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        model = model_factory.create_chat_model('', 'fixture', max_retries=1, http_client=client)
        if status == 401:
            with pytest.raises(AuthenticationError):
                model.invoke('요약해줘')
        else:
            assert model.invoke('요약해줘').content == '요약 완료'
    assert len(calls) == attempts
