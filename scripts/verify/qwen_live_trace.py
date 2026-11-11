"""Observe existing synchronous transports without replacing model outputs or prompts."""
from contextlib import ExitStack, contextmanager
import io
import json
import os
import socket
import time
import urllib.request
from urllib.parse import urlsplit
from unittest.mock import patch

from qwen_live_support import digest, sanitize
from qwen_live_policy import endpoint_policy, assert_model_url, canonical_model
from qwen_live_storage import InstrumentationFatal, instrumentation_failure


class BudgetExceeded(RuntimeError): pass


class Trace:
    def __init__(self, endpoint, max_calls=100, *, allow_local_endpoint=None, models=(), provider='ollama'):
        self.policy=endpoint_policy(endpoint,allow_local_endpoint)
        self.host=self.policy['host'];self.provider=provider
        self.models={canonical_model(model,provider) for model in models}
        self.max_calls=max_calls; self.calls=[]; self.stages=[]; self.stage='probe'; self.turn_index=None
        self.on_change=lambda:None
        self.active_call=None;self.instrumentation_error=None
        self.secrets=[v for k,v in os.environ.items() if v and any(t in k.upper() for t in ('API_KEY','ACCESS_TOKEN','REFRESH_TOKEN','PASSWORD'))]

    def notify(self):
        try:self.on_change()
        except InstrumentationFatal as error:
            self.instrumentation_error=error
            raise
        except Exception as error:
            fatal=instrumentation_failure('trace_snapshot_callback',error)
            self.instrumentation_error=fatal
            raise fatal from None

    def start(self, transport, model, request_settings=None):
        if len(self.calls)>=self.max_calls: raise BudgetExceeded('Evaluation call budget exhausted')
        if self.models and canonical_model(model,self.provider) not in self.models:
            raise ValueError('Evaluation blocks an unselected model')
        item={'stage':self.stage,'turn_index':self.turn_index,'transport':transport,'requested_model':model,'actual_model':None,
              'kind':'live','status':'started','started_monotonic':time.monotonic(),
              'dispatch_state':'not_dispatched','http_dispatch_attempts':0,'http_responses_received':0,
              'model_response_observed':False,'request_settings':request_settings or {}}
        self.calls.append(item);self.notify();return item

    def dispatch(self,item):
        # Persist uncertainty BEFORE entering the HTTP transport. If killed after this
        # snapshot, dispatch is unknown; it must not be counted as a server inference.
        item.update(status='dispatch_pending',dispatch_state='dispatch_pending')
        try:self.notify()
        except InstrumentationFatal:
            item.update(status='instrumentation_failed',dispatch_state='dispatched' if item['http_dispatch_attempts'] else 'not_dispatched')
            raise
        item.update(status='dispatched',dispatch_state='dispatched')
        item['http_dispatch_attempts']+=1

    def counts(self):
        return {'model_call_records':len(self.calls),
                'dispatched_model_requests':sum(c['http_dispatch_attempts'] for c in self.calls),
                'model_responses_received':sum(c['model_response_observed'] for c in self.calls),
                'pending_model_call_records':sum(c['status'] in ('started','dispatch_pending','dispatched') for c in self.calls)}

    def finish(self,item,**values):
        item.update(sanitize(values,self.secrets))
        if values.get('status')=='returned' and item['http_responses_received'] and values.get('actual_model'):
            item['model_response_observed']=True
        if 'started_monotonic' in item:item['elapsed_seconds']=round(time.monotonic()-item.pop('started_monotonic'),4)
        if values.get('status')=='returned' and canonical_model(values.get('actual_model'),self.provider)!=canonical_model(item['requested_model'],self.provider):
            item.update(status='failed',error_type='ModelIdentityMismatch')
            self.notify()
            raise ValueError('Actual response model differs from the requested model')
        self.notify()

    @contextmanager
    def installed(self):
        from langchain_core.language_models.chat_models import BaseChatModel
        import httpx
        from openai import _base_client as sdk_client
        import agent_builder.design.graph as graph_module
        original_resolve=socket.getaddrinfo
        original_urlopen=urllib.request.urlopen
        original_generate=BaseChatModel._generate_with_cache
        original_timed=graph_module._timed_node
        sdk_http = getattr(sdk_client, 'httpx2', None) or getattr(sdk_client, 'httpx', None)
        if sdk_http is None:
            raise RuntimeError('Cannot observe the installed OpenAI SDK HTTP transport')
        tracer=self

        def resolve(host,port,*args,**kwargs):
            host=host.decode() if isinstance(host,bytes) else host
            if host != tracer.host or str(port)!=str(tracer.policy['port']): raise OSError('Evaluation blocks non-model network destination or port')
            return original_resolve(host,port,*args,**kwargs)

        def redirect(*args,**kwargs):
            raise OSError('Evaluation blocks HTTP redirects')

        def sdk_call(request):
            assert_model_url(request.url,tracer.policy)
            if request.url.path == '/api/chat':
                raise OSError('Native chat requires the observed Ollama transport')
            if request.url.path != '/v1/chat/completions':
                return None
            if tracer.active_call is None:
                raise OSError('SDK chat requires an active model call record')
            tracer.dispatch(tracer.active_call)
            return tracer.active_call

        def sync_hook(send):
            def guarded(client, request):
                item = sdk_call(request)
                response = send(client, request)
                if item: item['http_responses_received'] += 1
                if response.is_redirect:
                    response.close()
                    raise OSError('Evaluation blocks HTTP redirects')
                return response
            return guarded

        def async_hook(send):
            async def guarded(client, request):
                item = sdk_call(request)
                response = await send(client, request)
                if item: item['http_responses_received'] += 1
                if response.is_redirect:
                    await response.aclose()
                    raise OSError('Evaluation blocks HTTP redirects')
                return response
            return guarded

        def urlopen(request,*args,**kwargs):
            url=request.full_url if hasattr(request,'full_url') else str(request)
            assert_model_url(url,tracer.policy)
            if urlsplit(url).path == '/v1/chat/completions':
                raise OSError('SDK chat requires the observed OpenAI transport')
            is_chat=urlsplit(url).path.endswith('/api/chat')
            body=json.loads(request.data) if is_chat else {}
            item=tracer.start('ollama_native',body.get('model'),{k:body[k] for k in ('think','stream','options') if k in body}) if is_chat else None
            try:
                if item:tracer.dispatch(item)
                response=original_urlopen(request,*args,**kwargs)
                if not item:return response
                item['http_responses_received']+=1
                with response: data=response.read()
                raw=json.loads(data)
                tracer.finish(item,status='returned',actual_model=raw.get('model'),raw_output=raw.get('message'),
                              usage={k:raw.get(k) for k in ('prompt_eval_count','eval_count','total_duration','done_reason')})
                return io.BytesIO(data)
            except Exception as error:
                if item:tracer.finish(item,status='failed',error_type=type(error).__name__)
                raise

        def generate(model,*args,**kwargs):
            item=tracer.start('existing_langchain_client',getattr(model,'model_name',None),
                {k:getattr(model,k,None) for k in ('temperature','top_p','max_tokens','max_retries')})
            previous=tracer.active_call;tracer.active_call=item
            try:
                result=original_generate(model,*args,**kwargs)
                outputs=[]; actual=None
                for generation in result.generations:
                    message=generation.message
                    metadata=getattr(message,'response_metadata',{})
                    actual=metadata.get('model_name') or metadata.get('model') or actual
                    outputs.append({'content':message.content,'tool_calls':getattr(message,'tool_calls',[]),
                                    'additional_kwargs':getattr(message,'additional_kwargs',{}),
                                    'response_metadata':metadata,'usage_metadata':getattr(message,'usage_metadata',None)})
                actual=actual or (result.llm_output or {}).get('model_name') or (result.llm_output or {}).get('model')
                tracer.finish(item,status='returned',actual_model=actual,raw_output=outputs,
                              usage=result.llm_output,raw_kind='SDK assembled response, not HTTP stream frames')
                return result
            except Exception as error:
                tracer.finish(item,status='failed',error_type=type(error).__name__);raise
            finally:tracer.active_call=previous

        def timed(name,fn):
            wrapped=original_timed(name,fn)
            def run(state):
                previous=tracer.stage;tracer.stage=name
                started=time.monotonic()
                calls_before = len(tracer.calls)
                ledger_keys = ('condition_ledger','condition_enforced','condition_required','condition_route',
                               'requirement_ledger','requirement_fingerprint','requirement_digest','pending_questions')
                observes_ledger = name in ('condition_check','requirements','condition_audit','validate')
                before = sanitize({k:state[k] for k in ledger_keys if k in state},tracer.secrets) if observes_ledger else None
                try:
                    result=wrapped(state)
                    fields=('request_meaning','capability_assessment','condition_ledger','requirement_ledger',
                            'pending_questions','status','draft','issues','semantic_issues','trace')
                    observation={'stage':name,'turn_index':tracer.turn_index,'elapsed_seconds':time.monotonic()-started,
                        'model_call_indices':list(range(calls_before,len(tracer.calls))),
                        'parsed_state':{k:result[k] for k in fields if isinstance(result,dict) and k in result}}
                    if observes_ledger:
                        observation['ledger_before']=before
                        observation['ledger_after']={**before,**{k:result[k] for k in ledger_keys if isinstance(result,dict) and k in result}}
                    tracer.stages.append(sanitize(observation,tracer.secrets))
                    return result
                finally:tracer.stage=previous
            return run
        with ExitStack() as stack:
            stack.enter_context(patch.object(socket,'getaddrinfo',resolve))
            stack.enter_context(patch.object(urllib.request,'urlopen',urlopen))
            stack.enter_context(patch.object(urllib.request.HTTPRedirectHandler,'redirect_request',redirect))
            # The installed SDK can use a distinct httpx2 module. Guard the
            # actual SDK classes as well as application HTTPX clients, once each.
            for cls in dict.fromkeys((httpx.Client, sdk_http.Client)):
                stack.enter_context(patch.object(cls, '_send_single_request', sync_hook(cls._send_single_request)))
            for cls in dict.fromkeys((httpx.AsyncClient, sdk_http.AsyncClient)):
                stack.enter_context(patch.object(cls, '_send_single_request', async_hook(cls._send_single_request)))
            stack.enter_context(patch.object(BaseChatModel,'_generate_with_cache',generate))
            stack.enter_context(patch.object(graph_module,'_timed_node',timed))
            yield self
