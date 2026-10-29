"""Ollama transport; keep vLLM's reviewed adapter unchanged."""
from langchain_openai import ChatOpenAI
from copy import deepcopy
from .qwen_chat_model import omit_empty_tools


def compatible_schema_payload(payload):
    """Adapt only the Ollama wire copy; application validation stays strict.

    Local 0.33.0 rejects the builder's Unicode shorthand and maxLength=2000
    while compiling a JSON grammar. Required fields, enums and references stay
    intact. Lengths are still checked by the original Pydantic/schema validator.
    """
    result = deepcopy(payload)
    format_spec = result.get('response_format')
    if not isinstance(format_spec, dict) or not isinstance(format_spec.get('json_schema'), dict):
        return result
    schema = format_spec['json_schema'].get('schema')
    def walk(value):
        if isinstance(value, dict):
            if value.get('pattern') == r'^[a-zA-Z][\w-]{0,39}$':
                value['pattern'] = r'^[a-zA-Z][a-zA-Z0-9_-]{0,39}$'
            if value.get('maxLength') == 2000:
                value.pop('maxLength')
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)
    return result


class OllamaChatOpenAI(ChatOpenAI):
    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload=compatible_schema_payload(omit_empty_tools(super()._get_request_payload(input_, stop=stop, **kwargs)))
        from .graph import _timing_event
        _timing_event({'event':'model_request','model':payload.get('model'),
            'max_tokens':payload.get('max_tokens'),'max_completion_tokens':payload.get('max_completion_tokens'),
            'stream':payload.get('stream'),'structured':bool(payload.get('response_format')),
            'tool_count':len(payload.get('tools') or []),'thinking':(payload.get('extra_body') or {}).get('think',payload.get('think'))})
        return payload
