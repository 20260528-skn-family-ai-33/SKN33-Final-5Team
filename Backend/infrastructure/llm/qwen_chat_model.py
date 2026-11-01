"""vLLM rejects tools=[]; omit it for the final structured response."""
from langchain_openai import ChatOpenAI
from copy import deepcopy
from pydantic import BaseModel
from openai.lib._pydantic import to_strict_json_schema
from langchain_core.runnables import RunnableLambda


def typed_response_format(schema, strict=True):
    return {'type': 'json_schema', 'json_schema': {
        'name': schema.__name__, 'strict': strict,
        'schema': to_strict_json_schema(schema) if strict else schema.model_json_schema()}}


def omit_empty_tools(payload):
    payload=dict(payload)
    if payload.get('tools')==[]:
        payload.pop('tools')
        payload.pop('tool_choice',None)
        payload.pop('parallel_tool_calls',None)
    return payload


def compact_structured_request(payload):
    """vLLM validates structured_outputs before merging response_format.

    Send the identical JSON schema explicitly with whitespace_pattern. Omit the
    whitespace-only object on tool-only calls: it is not a standalone constraint.
    """
    payload = deepcopy(payload)
    extra = payload.get('extra_body') or {}
    constraint = extra.get('structured_outputs')
    if not constraint or 'whitespace_pattern' not in constraint:
        return payload
    response_format = payload.get('response_format') or {}
    if isinstance(response_format, type) and issubclass(response_format, BaseModel):
        response_format = typed_response_format(response_format)
    if response_format.get('type') == 'json_schema':
        constraint['json'] = response_format['json_schema']['schema']
        payload.pop('response_format')
    elif not any(constraint.get(k) is not None for k in ('json','regex','choice','grammar','json_object','structural_tag')):
        extra.pop('structured_outputs')
    return payload


class QwenChatOpenAI(ChatOpenAI):
    def _get_request_payload(self,input_,*,stop=None,**kwargs):
        return compact_structured_request(omit_empty_tools(super()._get_request_payload(input_,stop=stop,**kwargs)))

    def with_structured_output(self, schema=None, *, method='json_schema', include_raw=False, strict=None, **kwargs):
        if method != 'json_schema' or not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            return super().with_structured_output(schema, method=method, include_raw=include_raw, strict=strict, **kwargs)
        # The vLLM path sends JSON constraints through extra_body, so use the
        # dictionary parser rather than the SDK's missing `parsed` attachment.
        # Restore the declared Pydantic return type expected by runtime nodes.
        json_schema = typed_response_format(schema, strict is not False)['json_schema']['schema']
        runnable = super().with_structured_output(json_schema,
            method=method, include_raw=include_raw, strict=strict, **kwargs)
        def restore_type(value):
            if not include_raw:
                return schema.model_validate(value)
            result = dict(value)
            if result.get('parsed') is not None:
                try:
                    result['parsed'] = schema.model_validate(result['parsed'])
                except Exception as error:
                    result.update(parsed=None, parsing_error=error)
            return result
        return runnable | RunnableLambda(restore_type)
