"""One explicit model connection for design, execution, resume and retries."""
import os
from langchain_openai import ChatOpenAI


def create_chat_model(api_key, model, **limits):
    provider = os.getenv('A4B_MAIN_PROVIDER', 'openai')
    if provider not in ('openai', 'ollama', 'vllm'):
        raise ValueError('A4B_MAIN_PROVIDER는 openai/ollama/vllm이어야 합니다.')
    options = dict(model=model, api_key=api_key, **limits)
    if provider != 'openai':
        base_url = os.getenv('A4B_MAIN_BASE_URL')
        if not base_url:
            raise ValueError('로컬 모델의 A4B_MAIN_BASE_URL이 필요합니다.')
        timeout=float(os.getenv('A4B_MAIN_TIMEOUT_S','180'))
        if not 1<=timeout<=600:raise ValueError('A4B_MAIN_TIMEOUT_S는 1~600초로 설정하세요.')
        options['timeout']=timeout
        options.update(base_url=base_url, api_key=os.getenv('A4B_MAIN_API_KEY') or api_key or 'local-not-a-secret',
                       default_headers={'User-Agent': 'A4B/0.1'})
        if provider == 'vllm':
            from .qwen_chat_model import QwenChatOpenAI
            options.update(temperature=0.7, top_p=0.8, streaming=True, stream_usage=True, max_retries=0, extra_body={
                'top_k': 20, 'min_p': 0,
                'chat_template_kwargs': {'enable_thinking': False},
                'structured_outputs': {'whitespace_pattern': '[ ]?'},
            })
            return QwenChatOpenAI(**options)
        # /v1 uses reasoning_effort; think is retained for compatible Ollama versions.
        options.update(temperature=0.7, top_p=0.8, reasoning_effort='none', extra_body={'think': False},
                       streaming=True, stream_usage=True, max_retries=0)
        from .ollama_chat_model import OllamaChatOpenAI
        return OllamaChatOpenAI(**options)
    return ChatOpenAI(**options)
