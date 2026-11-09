"""Persisted 4B references must work on a 9B-only server after explicit opt-in."""
import io
import json
import urllib.error

import httpx
import pytest

from infrastructure.llm.model_aliases import model_aliases, resolve_ollama_model
from infrastructure.llm.model_factory import create_chat_model
from infrastructure.persistence.storage import Storage
from service.worker import handle
from tests.design.test_direct_conversation import draft as native_draft
from tests.design.test_builder import draft, initial
from tests.workflow_engine.test_workflow import flow, node, edge
from workflow_engine.runner import run_workflow


OLD = 'a4b-qwen35-4b-16k'
NEW = 'a4b-qwen35-9b-final664-pilot32k'
CAPABILITY = 'a4b-qwen35-9b-previous-pilot32k'
ALIASES = json.dumps({OLD: NEW})


@pytest.fixture
def migration(monkeypatch):
    for key, value in {
        'A4B_STORAGE': 'sqlite', 'A4B_MAIN_PROVIDER': 'ollama',
        'A4B_MAIN_BASE_URL': 'https://model.invalid/v1', 'A4B_MAIN_MODEL': NEW,
        'A4B_OLLAMA_MODEL_ALIASES': ALIASES, 'A4B_CAPABILITY_MODEL': CAPABILITY,
        'A4B_CAPABILITY_MODE': 'enforce', 'A4B_PLANNER_MODE': 'direct',
        'A4B_PLANNER_PROMPT': 'v4', 'A4B_CONDITION_MODE': 'off',
        'A4B_REQUIREMENT_MODE': 'off', 'A4B_VERIFIER_MODE': 'off',
    }.items():
        monkeypatch.setenv(key, value)
    for key in ('A4B_MAIN_API_KEY', 'A4B_TIMING_LOG', 'A4B_BUSINESS_RECORDS'):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize('raw', ['[]', 'null', 'not json', '{"old":null}', '{"old":"bad model"}',
    '{"old":"new","old:latest":"different"}', '{"old":"next","next":"final"}', '{"old":"old:latest"}'])
def test_invalid_aliases_fail_before_calling_a_model(monkeypatch, raw):
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', raw)
    with pytest.raises(ValueError, match='A4B_OLLAMA_MODEL_ALIASES'):
        model_aliases()


def test_aliases_are_explicit_and_handle_tags_without_changing_registry_ports(monkeypatch):
    source = 'registry.test:443/team/old'
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', json.dumps({source: NEW}))
    assert resolve_ollama_model(source) == resolve_ollama_model(source + ':latest') == NEW
    assert resolve_ollama_model(source + ':other') == source + ':other'
    assert resolve_ollama_model('qwen3.5:4b') == 'qwen3.5:4b'
    for empty in ('', '  ', '{}'):
        monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', empty)
        assert resolve_ollama_model(source) == source


@pytest.mark.parametrize('stored_model,enabled,success', [
    (OLD, False, False), (OLD, True, True), (OLD + ':latest', True, True),
    (NEW, True, True), (OLD + ':other', True, False),
])
def test_existing_session_uses_9b_without_rewriting_its_model(tmp_path, monkeypatch, migration, stored_model, enabled, success):
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', ALIASES if enabled else '')
    prompt = '입력 표를 JSON으로 저장해줘'
    calls = []
    def respond(request, timeout):
        model = json.loads(request.data)['model']; calls.append(model)
        if model not in (NEW, CAPABILITY):
            raise urllib.error.HTTPError(request.full_url, 404, 'model not found', {}, io.BytesIO())
        content = {'decision': 'existing_tools', 'tool_ids': ['read_table', 'write_result'],
                   'missing_conditions': [], 'missing_actions': [], 'reason': '표 저장', 'user_quotes': [prompt]} if model == CAPABILITY else native_draft()
        return io.BytesIO(json.dumps({'message': {'content': json.dumps(content)}, 'done_reason': 'stop'}).encode())
    monkeypatch.setattr('urllib.request.urlopen', respond)
    directory = str(tmp_path)
    session = handle({'directory': directory, 'action': 'new_session', 'model': stored_model, 'credential_id': 'local-model'})
    store = Storage(directory)
    store.add_asset({'id': 'asset-1', 'name': '거래.csv', 'kind': 'table', 'columns': ['금액'], 'profile': {}}, [{'금액': 100}])
    session['file_ids'] = ['asset-1']
    session = store.update_session(session, session['revision'])
    store.close()
    request = {'directory': directory, 'action': 'message', 'id': session['id'],
               'revision': session['revision'], 'content': prompt, 'api_key': 'local-not-a-secret'}
    if success:
        result = handle(request)
        assert result['status'] == 'ready'
        assert result['capability_assessment']['decision'] == 'existing_tools'
    else:
        with pytest.raises(ValueError, match='404'):
            handle(request)
    assert calls == [CAPABILITY, NEW if success else stored_model]
    store = Storage(directory)
    assert store.session(session['id'])['model'] == stored_model
    store.close()


@pytest.fixture
def chat_wire(monkeypatch, migration):
    """Use the real OpenAI-compatible client and parser, with an in-memory HTTP server."""
    from infrastructure.llm import ollama_chat_model
    original = ollama_chat_model.OllamaChatOpenAI
    calls = []
    def respond(request):
        payload = json.loads(request.content); calls.append(payload['model'])
        if payload['model'] != NEW:
            return httpx.Response(404, json={'error': {'message': 'model not found', 'type': 'not_found'}})
        content = json.dumps({'facts': [{'field': '제목', 'value': '테스트', 'source_id': 'sample:1:0', 'quote': '제목: 테스트'}]})
        # Both streaming and structured-output requests must use the resolved wire model.
        body = {'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': NEW,
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}]}
        if payload.get('stream'):
            body['object'] = 'chat.completion.chunk'
            choice = body['choices'][0]; choice['delta'] = choice.pop('message')
            return httpx.Response(200, text='data: ' + json.dumps(body) + '\n\ndata: [DONE]\n\n', headers={'Content-Type': 'text/event-stream'})
        return httpx.Response(200, json=body)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(ollama_chat_model, 'OllamaChatOpenAI', lambda **options: original(**options, http_client=client))
        yield calls


def saved_legacy_workflow(directory, *, wait=False):
    store = Storage(directory)
    workflow = flow()
    if wait:
        workflow['nodes'].insert(0, node('ask', 'ask_user', question='계속할까요?'))
        workflow['edges'].insert(0, edge('ask', 'read')); workflow['entry'] = 'ask'
    config = {**draft(), 'workflow': workflow, 'tool_ids': [n['tool'] for n in workflow['nodes']]}
    agent_id = store.save_agent({**initial(), 'model': OLD, 'credential_id': 'local-model', 'draft': config, 'confirmed': True})
    store.add_asset({'id': 'sample', 'name': 'sample.txt', 'kind': 'document', 'format': 'txt',
                     'text': '제목: 테스트', 'pages': ['제목: 테스트'], 'columns': []}, [])
    return store, agent_id


@pytest.mark.parametrize('snapshot', [False, True])
def test_saved_workflow_and_pinned_job_snapshot_call_9b(tmp_path, chat_wire, snapshot):
    store, agent_id = saved_legacy_workflow(tmp_path)
    before = store.agent(agent_id)
    run = run_workflow(store, agent_id, '추출', 'local-not-a-secret', file_ids=['sample'],
                       agent_snapshot=before if snapshot else None)
    assert run['status'] == 'succeeded', run.get('error')
    assert chat_wire == [NEW]
    assert run['model'] == OLD and store.agent(agent_id) == before
    assert store.agent_version(agent_id, 1)['model'] == OLD
    store.close()


def test_paused_workflow_resumes_after_restart_with_9b(tmp_path, monkeypatch, chat_wire):
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', '')
    store, agent_id = saved_legacy_workflow(tmp_path, wait=True)
    paused = run_workflow(store, agent_id, '추출', 'local-not-a-secret', file_ids=['sample'])
    assert paused['status'] == 'needs_input' and not chat_wire
    store.close()
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', ALIASES)
    store = Storage(tmp_path)
    resumed = run_workflow(store, agent_id, '추출', 'local-not-a-secret', run_id=paused['id'], answer='계속')
    assert resumed['status'] == 'succeeded', resumed.get('error')
    assert resumed['id'] == paused['id'] and resumed['model'] == OLD
    assert chat_wire == [NEW]
    store.close()


def test_failed_4b_run_retries_on_9b_without_replaying_completed_nodes(tmp_path, monkeypatch, chat_wire):
    store, agent_id = saved_legacy_workflow(tmp_path)
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', '')
    failed = run_workflow(store, agent_id, '추출', 'local-not-a-secret', file_ids=['sample'])
    assert failed['status'] == 'failed' and chat_wire == [OLD]
    store.close()
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', ALIASES)
    store = Storage(tmp_path)
    result = handle({'directory': str(tmp_path), 'action': 'retry_run', 'id': failed['id'], 'api_key': 'local-not-a-secret'})
    assert result['status'] == 'succeeded', result.get('error')
    assert result['id'] == failed['id'] and result['model'] == OLD
    assert chat_wire == [OLD, NEW]
    assert sum(step['node'] == 'read' and step['status'] == 'completed' for step in result['steps']) == 1
    store.close()


def test_alias_does_not_redirect_a_selected_openai_account(monkeypatch, migration):
    monkeypatch.setattr('infrastructure.llm.model_factory.ChatOpenAI', lambda **options: options)
    result = create_chat_model('real-openai-key', OLD)
    assert result['model'] == OLD and 'base_url' not in result
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'vllm')
    monkeypatch.setattr('infrastructure.llm.qwen_chat_model.QwenChatOpenAI', lambda **options: options)
    assert create_chat_model('', OLD)['model'] == OLD


def test_new_9b_design_can_reuse_old_workflow_without_changing_pinned_version(tmp_path, monkeypatch, chat_wire):
    from workflow_engine.library.reusable_workflows import catalog, pin_dependencies
    store, child_id = saved_legacy_workflow(tmp_path)
    old = store.agent(child_id)
    assert child_id in [item['id'] for item in catalog(store, NEW, 'local-model')]
    reuse = node('child', 'saved_workflow', agent_id=child_id, agent_version=1, output_node='out', output_port='artifact')
    config = {**draft(), 'tool_ids': ['saved_workflow'], 'workflow': {'entry': 'child', 'nodes': [reuse], 'edges': []}}
    parent = store.save_agent({**initial(), 'model': NEW, 'credential_id': 'local-model', 'draft': config, 'confirmed': True})
    assert store.agent(parent)['workflow_dependencies']['child'] == old
    result = run_workflow(store, parent, '추출', 'local-not-a-secret', file_ids=['sample'])
    assert result['status'] == 'succeeded', result.get('error')
    assert chat_wire == [NEW] and store.agent(child_id) == old
    assert not catalog(store, NEW, 'different-account')
    with pytest.raises(ValueError, match='같은 모델'):
        pin_dependencies(store, config, NEW, 'different-account')
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', '')
    assert child_id not in [item['id'] for item in catalog(store, NEW, 'local-model')]
    with pytest.raises(ValueError, match='같은 모델'):
        pin_dependencies(store, config, NEW, 'local-model')
    store.close()
