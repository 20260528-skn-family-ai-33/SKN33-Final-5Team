from agent_builder import model_factory


def test_openai_default_does_not_inherit_local_options(monkeypatch):
    monkeypatch.delenv('A4B_MAIN_PROVIDER', raising=False)
    monkeypatch.setattr(model_factory,'ChatOpenAI',lambda **kw:kw)
    result=model_factory.create_chat_model('test-key','gpt-4.1-mini',timeout=70)
    assert result==dict(api_key='test-key',model='gpt-4.1-mini',timeout=70)


def test_ollama_uses_explicit_local_endpoint_without_vllm_options(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:11434/v1')
    monkeypatch.setattr('agent_builder.ollama_chat_model.OllamaChatOpenAI',lambda **kw:kw)
    result=model_factory.create_chat_model('','qwen3:8b-q4_K_M')
    assert result['base_url']=='http://127.0.0.1:11434/v1'
    assert result['extra_body']=={'think':False}
    assert result['temperature']==0.7 and result['top_p']==0.8


def test_vllm_uses_supplied_transport(monkeypatch):
    from agent_builder import qwen_chat_model
    monkeypatch.setenv('A4B_MAIN_PROVIDER','vllm')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:8000/v1')
    monkeypatch.setattr(qwen_chat_model,'QwenChatOpenAI',lambda **kw:kw)
    result=model_factory.create_chat_model('','Qwen/Qwen3-8B')
    assert result['extra_body']['structured_outputs']['whitespace_pattern']=='[ ]?'
    assert result['streaming'] and result['stream_usage']
    assert result['max_retries']==0


def test_optional_svm_hints_do_not_treat_finish_as_a_prediction(monkeypatch):
    from agent_builder.tool_recommendation import recommend_for_state
    monkeypatch.setenv('A4B_TOOL_ADVICE_MODE','hint')
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
    monkeypatch.setattr('agent_builder.ollama_chat_model.OllamaChatOpenAI',lambda **kw:kw)
    assert model_factory.create_chat_model('','qwen3:8b',timeout=70)['timeout']==240
    monkeypatch.setenv('A4B_MAIN_TIMEOUT_S','0')
    with pytest.raises(ValueError,match='A4B_MAIN_TIMEOUT_S'):model_factory.create_chat_model('','qwen3:8b')


def test_local_provider_never_reuses_the_openai_key(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:11434/v1')
    monkeypatch.delenv('A4B_MAIN_API_KEY',raising=False)
    monkeypatch.setattr('agent_builder.ollama_chat_model.OllamaChatOpenAI',lambda **kw:kw)
    result=model_factory.create_chat_model('','qwen3:8b')
    assert result['api_key']=='local-not-a-secret'
    monkeypatch.setenv('A4B_MAIN_API_KEY','dedicated-local-key')
    assert model_factory.create_chat_model('','qwen3:8b')['api_key']=='dedicated-local-key'


def test_selected_openai_account_is_not_routed_to_the_local_server(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','http://127.0.0.1:11434/v1')
    monkeypatch.setattr(model_factory,'ChatOpenAI',lambda **kw:dict(kw,target='openai'))
    monkeypatch.setattr('agent_builder.ollama_chat_model.OllamaChatOpenAI',lambda **kw:dict(kw,target='local'))
    assert model_factory.create_chat_model('sk-real','gpt-4.1-mini')['target']=='openai'
    assert 'base_url' not in model_factory.create_chat_model('sk-real','gpt-4.1-mini')
    for local_key in ('',None,model_factory.LOCAL_MODEL_API_KEY):
        assert model_factory.create_chat_model(local_key,'qwen3:8b')['target']=='local'
