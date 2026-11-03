"""Ollama transport; keep vLLM's reviewed adapter unchanged."""
from langchain_openai import ChatOpenAI
from copy import deepcopy
from infrastructure.llm.qwen_chat_model import omit_empty_tools


def compatible_schema_payload(payload):
    """Adapt only the Ollama wire copy; application validation stays strict.

    Avoid large bounded-string grammars and unused Pydantic definitions on the
    local decoder. Required fields, enums and referenced definitions stay intact.
    Lengths are still checked by the original Pydantic/schema validator.
    """
    result = deepcopy(payload)
    # LangChain converts max_tokens to OpenAI's max_completion_tokens. Ollama
    # 0.33.x ignores that newer field and falls back to the model's num_predict.
    # Keep the application-selected budget on the Ollama wire copy only.
    if 'max_completion_tokens' in result:
        result.setdefault('max_tokens', result.pop('max_completion_tokens'))
    format_spec = result.get('response_format')
    if not isinstance(format_spec, dict) or not isinstance(format_spec.get('json_schema'), dict):
        return result
    schema = format_spec['json_schema'].get('schema')
    def walk(value):
        if isinstance(value, dict):
            if value.get('pattern') == r'^[a-zA-Z][\w-]{0,39}$':
                value['pattern'] = r'^[a-zA-Z][a-zA-Z0-9_-]{0,39}$'
            value.pop('maxLength',None)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)
    if isinstance(schema,dict) and isinstance(schema.get('$defs'),dict):
        definitions=schema['$defs'];used=set()
        def references(value):
            if isinstance(value,dict):
                ref=value.get('$ref','')
                if isinstance(ref,str) and ref.startswith('#/$defs/'):
                    name=ref.split('/')[2].replace('~1','/').replace('~0','~')
                    if name not in used:
                        used.add(name)
                        references(definitions.get(name,{}))
                for key,child in value.items():
                    if key!='$defs':references(child)
            elif isinstance(value,list):
                for child in value:references(child)
        references(schema)
        schema['$defs']={k:v for k,v in definitions.items() if k in used}
    return result


class OllamaChatOpenAI(ChatOpenAI):
    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload=compatible_schema_payload(omit_empty_tools(super()._get_request_payload(input_, stop=stop, **kwargs)))
        from agent_builder.design.graph import _timing_event
        _timing_event({'event':'model_request','model':payload.get('model'),
            'max_tokens':payload.get('max_tokens'),'max_completion_tokens':payload.get('max_completion_tokens'),
            'stream':payload.get('stream'),'structured':bool(payload.get('response_format')),
            'tool_count':len(payload.get('tools') or []),'thinking':(payload.get('extra_body') or {}).get('think',payload.get('think'))})
        return payload
