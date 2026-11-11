"""Offline regressions for the selected SDK transport, frozen corpus and turn evidence."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import socket
import sys

import httpx2
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts/verify'), str(ROOT / 'Backend')]
from qwen_live_support import digest, validate_corpus
from qwen_live_trace import Trace
from workflow_engine.catalog.registry import TOOLS

REMOTE = 'https://fixture.proxy.runpod.net/v1'
MODEL = 'qwen-offline-fixture'


def load_runner():
    spec = importlib.util.spec_from_file_location('fixture_runner', ROOT/'scripts/verify/verify-qwen-live-evaluation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_langchain_openai_sdk_counts_httpx2_response_without_network(monkeypatch):
    from infrastructure.llm.ollama_chat_model import OllamaChatOpenAI
    def forbid_network(*args, **kwargs):
        pytest.fail('Synthetic test attempted DNS/network access')
    monkeypatch.setattr(socket, 'getaddrinfo', forbid_network)
    requests = []
    def respond(request):
        requests.append((str(request.url), json.loads(request.content)['model']))
        return httpx2.Response(200, json={'id':'fixture-1','object':'chat.completion','created':0,'model':MODEL,
            'choices':[{'index':0,'message':{'role':'assistant','content':'fixture text'},'finish_reason':'stop'}]})
    tracer = Trace(REMOTE, models=[MODEL])
    with httpx2.Client(transport=httpx2.MockTransport(respond)) as client:
        model = OllamaChatOpenAI(model=MODEL, base_url=REMOTE, api_key='fixture', http_client=client, max_retries=0)
        with tracer.installed():
            result = model.invoke('synthetic transport test')
    assert result.content == 'fixture text'
    assert requests == [(REMOTE+'/chat/completions', MODEL)]
    assert len(tracer.calls) == 1
    call = tracer.calls[0]
    assert (call['status'], call['actual_model'], call['http_dispatch_attempts'], call['http_responses_received']) == ('returned',MODEL,1,1)
    assert call['model_response_observed'] and tracer.counts()['model_responses_received'] == 1


def test_sdk_does_not_follow_a_same_origin_redirect_or_send_to_untracked_path():
    seen = []
    def respond(request):
        seen.append(str(request.url))
        return httpx2.Response(302, headers={'location':REMOTE+'/models'})
    tracer = Trace(REMOTE, models=[MODEL])
    with tracer.installed(), httpx2.Client(transport=httpx2.MockTransport(respond), follow_redirects=True) as client:
        with pytest.raises(OSError,match='redirect'):client.get(REMOTE+'/models')
        with pytest.raises(OSError):client.get(REMOTE+'/unapproved')
        with pytest.raises(OSError):client.post(REMOTE+'/chat/completions')
    assert seen == [REMOTE+'/models']


def test_sdk_async_redirect_guard_closes_the_response(monkeypatch):
    response = httpx2.Response(302, headers={'location':REMOTE+'/models'})
    async def send(client, request):return response
    monkeypatch.setattr(httpx2.AsyncClient, '_send_single_request', send)
    with Trace(REMOTE,models=[MODEL]).installed(), pytest.raises(OSError,match='redirect'):
        coroutine = httpx2.AsyncClient._send_single_request(None,httpx2.Request('GET',REMOTE+'/models'))
        try:coroutine.send(None)
        finally:coroutine.close()
    assert response.is_closed


def fixture_corpus():
    original = json.loads((ROOT/'docs/qwen-live-evaluation-cases.json').read_text(encoding='utf-8'))
    fixture = deepcopy(original)
    fixture.pop('gold_sha256')
    fixture['cases'] = fixture['cases'][:30]
    for case in fixture['cases']:case['split']='holdout'
    fixture['gold_sha256'] = digest(fixture)
    return fixture


def test_new_profile_uses_precommitted_digest_and_protects_original_52():
    catalog = {tool['id']:tool for tool in TOOLS}
    fixture = fixture_corpus()
    assert validate_corpus(fixture,catalog,'runpod-rebuild',fixture['gold_sha256'])
    with pytest.raises(ValueError,match='Original 52'):validate_corpus(fixture,catalog)
    with pytest.raises(ValueError,match='independently frozen'):validate_corpus(fixture,catalog,'runpod-rebuild','different')
    fixture['cases'][0]['request'] += 'changed'
    with pytest.raises(ValueError,match='digest'):validate_corpus(fixture,catalog,'runpod-rebuild',fixture['gold_sha256'])


def test_worker_error_keeps_attempted_followup_and_actual_question_id(tmp_path,monkeypatch):
    runner = load_runner()
    case = fixture_corpus()['cases'][0]
    case.update(resource_state={'resources':[],'gmail_connected_metadata':False}, prior_conversation=[], max_user_answers=1,
                answer_policy=[{'question_terms':['threshold'],'slot':'threshold','text':'20'}])
    def handle(request):
        if request['action']=='new_session':return {'id':'fixture-session','revision':1}
        if request.get('question_id'):
            assert request['question_id']=='real-prior-question'
            raise ValueError('synthetic worker failure')
        return {'id':'fixture-session','revision':2,'status':'needs_clarification',
                'pending_questions':[{'id':'real-prior-question','text':'threshold?'}]}
    monkeypatch.setattr('service.worker.handle',handle)
    record=runner.run_case(case,MODEL,Trace(REMOTE,models=[MODEL]),tmp_path)
    assert record['status']=='failed' and len(record['turns'])==1
    assert [a['status'] for a in record['turn_attempts']]==['returned','worker_error']
    assert record['turn_attempts'][1]['prior_pending_question_ids']==['real-prior-question']
    assert record['turn_attempts'][1]['question_id']=='real-prior-question'


def test_observed_ledger_input_and_output_keep_inherited_owner_state(monkeypatch):
    import agent_builder.design.graph as graph
    monkeypatch.setattr(graph,'_timed_node',lambda name,fn:fn)
    tracer=Trace(REMOTE,models=[MODEL]);tracer.turn_index=1
    before={'condition_ledger':{'slots':{'threshold':20}},'pending_questions':[{'id':'old'}]}
    with tracer.installed():
        graph._timed_node('requirements',lambda state:{'requirement_ledger':{'tasks':[]},'pending_questions':[]})(before)
    stage=tracer.stages[0]
    assert stage['turn_index']==1 and stage['model_call_indices']==[]
    assert stage['ledger_before']==before
    assert stage['ledger_after']=={**before,'requirement_ledger':{'tasks':[]},'pending_questions':[]}
