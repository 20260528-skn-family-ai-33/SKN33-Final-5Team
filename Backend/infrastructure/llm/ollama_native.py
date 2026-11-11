"""Keep LangChain's OpenAI contracts while sending strict native Ollama chat.

Ollama 0.40.13 does not forward `shift` through /v1. This client boundary
translates the wire protocol only; agent tools and structured-output parsing
remain in LangChain/the OpenAI SDK. No model weights or planner rules change.
"""
import json
import time
from uuid import uuid4

import httpx

from infrastructure.llm.ollama_settings import integer_setting, output_limit


def native_request(request, num_ctx):
    if not request.url.path.endswith('/chat/completions'):
        raise ValueError('Ollama native transport supports chat completions only.')
    body = json.loads(request.content)
    if body.get('n', 1) != 1:
        raise ValueError('Ollama native transport requires n=1.')
    options = {'num_ctx': num_ctx, 'num_predict': body.get('max_tokens') or output_limit()}
    for key in ('temperature', 'top_p', 'seed', 'stop', 'frequency_penalty', 'presence_penalty'):
        if body.get(key) is not None:
            options[key] = body[key]
    if isinstance(options.get('stop'), str):
        options['stop'] = [options['stop']]
    messages, names = [], {}
    for item in body['messages']:
        message = {'role': 'system' if item['role'] == 'developer' else item['role'],
                   'content': item.get('content') or ''}
        if isinstance(message['content'], list):
            texts, images = [], []
            for part in message['content']:
                if part.get('type') == 'text':
                    texts.append(part['text'])
                elif part.get('type') == 'image_url':
                    url = part['image_url']
                    url = url.get('url') if isinstance(url, dict) else url
                    if not isinstance(url, str) or not url.startswith('data:image/') or ';base64,' not in url:
                        raise ValueError('Ollama images require an inline base64 image.')
                    images.append(url.split(';base64,', 1)[1])
                else:
                    raise ValueError('Unsupported Ollama message content type.')
            message['content'] = '\n'.join(texts)
            if images:
                message['images'] = images
        if not isinstance(message['content'], str):
            raise ValueError('Ollama message content must be text.')
        if item.get('tool_calls'):
            calls = []
            for call in item['tool_calls']:
                fn = call['function']
                args = fn['arguments']
                args = json.loads(args) if isinstance(args, str) else args
                if not isinstance(args, dict):
                    raise ValueError('Tool arguments must be a JSON object.')
                names[call.get('id')] = fn['name']
                calls.append({'id': call.get('id', ''), 'function': {'name': fn['name'], 'arguments': args}})
            message['tool_calls'] = calls
        if item['role'] == 'tool':
            message['tool_name'] = item.get('name') or names.get(item.get('tool_call_id'), '')
            message['tool_call_id'] = item.get('tool_call_id', '')
        messages.append(message)
    payload = {'model': body['model'], 'messages': messages,
               'stream': bool(body.get('stream')), 'think': False,
               'shift': False, 'truncate': False, 'keep_alive': -1, 'options': options}
    tools = body.get('tools')
    if tools and body.get('tool_choice') != 'none':
        if any(t.get('type') != 'function' for t in tools):
            raise ValueError('Ollama supports function tools only.')
        # Native Ollama and the pinned /v1 adapter do not enforce tool_choice.
        # Retain the same tool catalog; DeepAgent checks which tools were read.
        payload['tools'] = tools
    spec = body.get('response_format') or {}
    if spec.get('type') == 'json_schema':
        payload['format'] = spec['json_schema']['schema']
    elif spec.get('type') == 'json_object':
        payload['format'] = 'json'
    elif spec and spec.get('type') != 'text':
        raise ValueError('Unsupported Ollama response format.')
    prefix = request.url.path.removesuffix('/chat/completions').removesuffix('/v1')
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ('content-length', 'content-type')}
    native = httpx.Request('POST', request.url.copy_with(path=prefix + '/api/chat'),
                           headers=headers, json=payload, extensions=dict(request.extensions))
    return native, payload['stream']


class Completion:
    """Translate complete native tool calls and NDJSON chunks to SDK messages."""
    def __init__(self, request):
        self.request = request
        self.id = 'chatcmpl-' + uuid4().hex
        self.created = int(time.time())
        self.tool_index = 0
        self.done = False

    def convert(self, raw, *, stream):
        if raw.get('error'):
            raise httpx.RemoteProtocolError('Ollama returned an inference error: ' + str(raw['error'])[:300], request=self.request)
        if not isinstance(raw.get('message'), dict):
            raise httpx.RemoteProtocolError('Ollama response has no chat message.', request=self.request)
        message = raw['message']
        converted = {'role': 'assistant', 'content': message.get('content') or ''}
        calls = []
        for call in message.get('tool_calls') or []:
            fn = call['function']
            args = fn['arguments']
            if isinstance(args, str):
                args = json.loads(args)
            if not isinstance(args, dict):
                raise httpx.RemoteProtocolError('Ollama returned invalid tool arguments.', request=self.request)
            tool = {'id': call.get('id') or 'call_' + uuid4().hex, 'type': 'function',
                    'function': {'name': fn['name'], 'arguments': json.dumps(args, ensure_ascii=False)}}
            if stream:
                tool['index'] = self.tool_index
            self.tool_index += 1
            calls.append(tool)
        if calls:
            converted['tool_calls'] = calls
        self.done = bool(raw.get('done'))
        reason = raw.get('done_reason')
        finish = ('length' if reason == 'length' else 'tool_calls' if self.tool_index else 'stop') if self.done else None
        result = {'id': self.id, 'object': 'chat.completion.chunk' if stream else 'chat.completion',
                  'created': self.created, 'model': raw.get('model', ''),
                  'choices': [{'index': 0, 'delta' if stream else 'message': converted, 'finish_reason': finish}]}
        if self.done:
            prompt, generated = raw.get('prompt_eval_count', 0), raw.get('eval_count', 0)
            result['usage'] = {'prompt_tokens': prompt, 'completion_tokens': generated, 'total_tokens': prompt + generated}
        return result

    def event(self, line):
        return ('data: ' + json.dumps(self.convert(json.loads(line), stream=True), ensure_ascii=False) + '\n\n').encode()

    def initial_event(self):
        # The SDK creates its snapshot from the first chunk; finish_reason on
        # that chunk bypasses its length check. Emit the standard role chunk
        # first, including when Ollama sends the entire answer in one record.
        raw = {'model': json.loads(self.request.content)['model'],
               'message': {'role': 'assistant', 'content': ''}, 'done': False}
        return self.event(json.dumps(raw))

    def require_done(self):
        if not self.done:
            raise httpx.RemoteProtocolError('Ollama stream ended before its completion record.', request=self.request)


class NativeStream(httpx.SyncByteStream):
    def __init__(self, response, request):
        self.response, self.completion = response, Completion(request)

    def __iter__(self):
        try:
            yield self.completion.initial_event()
            for line in self.response.iter_lines():
                if line.strip():
                    yield self.completion.event(line)
            self.completion.require_done()
            yield b'data: [DONE]\n\n'
        finally:
            self.close()

    def close(self):
        self.response.close()


class AsyncNativeStream(httpx.AsyncByteStream):
    def __init__(self, response, request):
        self.response, self.completion = response, Completion(request)

    async def __aiter__(self):
        try:
            yield self.completion.initial_event()
            async for line in self.response.aiter_lines():
                if line.strip():
                    yield self.completion.event(line)
            self.completion.require_done()
            yield b'data: [DONE]\n\n'
        finally:
            await self.aclose()

    async def aclose(self):
        await self.response.aclose()


def translated_response(response, request, streaming, stream_type):
    if not response.is_success:
        return response
    if streaming:
        return httpx.Response(response.status_code, request=request,
                              headers={'content-type': 'text/event-stream'}, stream=stream_type(response, request))
    raw = response.json()
    completion = Completion(request)
    body = completion.convert(raw, stream=False)
    completion.require_done()
    return httpx.Response(response.status_code, request=request, json=body)


class NativeClient(httpx.Client):
    def __init__(self, upstream=None):
        super().__init__()
        self.upstream = upstream
        self.num_ctx = integer_setting('A4B_OLLAMA_NUM_CTX', 16384, 4096, 131072)

    def send(self, request, **kwargs):
        native, streaming = native_request(request, self.num_ctx)
        response = (self.upstream.send if self.upstream is not None else super().send)(native, **kwargs)
        try:
            if not streaming and response.is_success:
                response.read()
            return translated_response(response, request, streaming, NativeStream)
        finally:
            if not streaming:
                response.close()


class AsyncNativeClient(httpx.AsyncClient):
    def __init__(self, upstream=None):
        super().__init__()
        self.upstream = upstream
        self.num_ctx = integer_setting('A4B_OLLAMA_NUM_CTX', 16384, 4096, 131072)

    async def send(self, request, **kwargs):
        native, streaming = native_request(request, self.num_ctx)
        response = await (self.upstream.send if self.upstream is not None else super().send)(native, **kwargs)
        try:
            if not streaming and response.is_success:
                await response.aread()
            return translated_response(response, request, streaming, AsyncNativeStream)
        finally:
            if not streaming:
                await response.aclose()
