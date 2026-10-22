"""Ollama transport; keep vLLM's reviewed adapter unchanged."""
from langchain_openai import ChatOpenAI
from .qwen_chat_model import omit_empty_tools


class OllamaChatOpenAI(ChatOpenAI):
    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        return omit_empty_tools(super()._get_request_payload(input_, stop=stop, **kwargs))
