"""The human-readable popup contract survives validation, save, and reopen."""
from copy import deepcopy
import json
import re

import pytest
from langgraph.types import Command

from agent_builder.design.node_edit import apply_node_edit, proof_issue
from agent_builder.design.nodes.validation import validate_node
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from service.handlers.design import _public_node_edit_messages
from service.worker import handle
from tests.design.test_gmail_slack_workflow import Forbidden, state
from tests.design.test_node_edit_descriptions import _table
from tests.design.test_node_popup_edits import _graph


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _request(node, rows, changes):
    return {'node_id': node['id'], 'label': node['label'], 'changes': changes,
            'request': _json(node['label']) + ' 작업의 설정을 다음과 같이 바꿔주세요.\n'
                       + '\n'.join(rows) + '\n다른 작업의 설정은 유지해주세요.'}


def _count_request(node, old, new):
    return _request(node, [f'- 조회 한도: {_json(old)} → {_json(new)}'], [
        {'path': 'arguments_json.max_results', 'old_value': old, 'new_value': new}])


def _read_limit(draft):
    return json.loads(draft['workflow']['nodes'][0]['settings']['arguments_json'])['max_results']


def _assert_friendly_messages(messages):
    text = '\n'.join(message['content'] for message in messages)
    assert not re.search(r'arguments_json|execution_plan|value_type|노드 ID:', text)


def test_human_gmail_two_step_edit_save_reopen_and_edit_again_needs_no_model(tmp_path):
    store = Storage(tmp_path)
    graph, connection = _graph(store)
    config = {'configurable': {'thread_id': 'human-mail-count'}}
    try:
        start = state('Gmail 최근 10개 메일 요약해줘')
        start['session_id'] = config['configurable']['thread_id']
        ready = graph.invoke(start, config)
        assert ready['status'] == 'ready', ready.get('issues')
        assert [node['tool'] for node in ready['draft']['workflow']['nodes']] == ['email.list', 'compose_answer']
        assert _read_limit(ready['draft']) == 10
        original_requirements = deepcopy(ready['draft']['requirements'])
        edit = _count_request(ready['draft']['workflow']['nodes'][0], 10, 5)
        revised = graph.invoke(Command(resume={'type': 'message', 'content': edit['request'], 'node_edit': edit}), config)
        assert revised['status'] == 'ready', revised.get('issues')
        assert not revised['pending_questions']
        assert _read_limit(revised['draft']) == 5
        assert '최대 5건' in revised['draft']['workflow']['nodes'][1]['settings']['instructions']
        assert '10건' not in revised['draft']['instructions'] and '10개' not in revised['draft']['explanation']
        assert revised['draft']['requirements'] == original_requirements
        assert proof_issue(revised) is None
        assert validate_node(revised)['status'] == 'ready'
        _assert_friendly_messages(revised['messages'])
        saved = graph.invoke(Command(resume={'type': 'confirm'}), config)
        assert saved['status'] == 'saved'
        stored = store.agent(saved['saved_agent_id'])['config']
        assert _read_limit(stored) == 5

        session = handle({'directory': str(tmp_path), 'action': 'new_session',
                          'model': 'gpt-4.1-mini', 'credential_id': uid(),
                          'agent_id': saved['saved_agent_id']})
        next_edit = _count_request(stored['workflow']['nodes'][0], 5, 7)
        reopened = handle({'directory': str(tmp_path), 'action': 'message',
                           'id': session['id'], 'revision': session['revision'],
                           'content': next_edit['request'], 'node_edit': next_edit,
                           'generation_request_id': uid()}, conversation=lambda _: Forbidden().invoke())
        assert reopened['status'] == 'ready', reopened.get('issues')
        assert not reopened['pending_questions']
        assert _read_limit(reopened['draft']) == 7
        assert '최대 7건' in reopened['draft']['workflow']['nodes'][1]['settings']['instructions']
        assert reopened['draft']['requirements'] == original_requirements
        _assert_friendly_messages(reopened['messages'])
        persisted_again = handle({'directory': str(tmp_path), 'action': 'confirm',
                                  'id': session['id'], 'revision': reopened['revision'],
                                  'generation_request_id': uid()})
        assert persisted_again['status'] == 'saved'
        assert _read_limit(store.agent(persisted_again['saved_agent_id'])['config']) == 7
        raw = graph.get_state({'configurable': {'thread_id': session['id']}}).values
        assert proof_issue(raw) is None
        edits = [entry for entry in raw['trace'] if entry.get('node') == 'node_edit' and 'model_calls' in entry]
        assert edits and all(entry['model_calls'] == 0 for entry in edits)
    finally:
        connection.close()
        store.close()


@pytest.mark.parametrize('new', [0, 501, 10.5])
def test_human_gmail_count_rejects_invalid_values_with_actionable_range(tmp_path, new):
    store = Storage(tmp_path)
    graph, connection = _graph(store)
    config = {'configurable': {'thread_id': 'invalid-human-mail-' + str(new)}}
    try:
        start = state('Gmail 최근 10개 메일 요약해줘')
        start['session_id'] = config['configurable']['thread_id']
        ready = graph.invoke(start, config)
        before = deepcopy(ready['draft'])
        edit = _count_request(before['workflow']['nodes'][0], 10, new)
        blocked = graph.invoke(Command(resume={'type': 'message', 'content': edit['request'], 'node_edit': edit}), config)
        assert blocked['status'] == 'validation_failed'
        assert blocked['draft'] == before
        assert blocked['node_edit_proof'] is None
        assert not blocked['pending_questions']
        assert '1~500건' in blocked['issues'][0] and '정수' in blocked['issues'][0]
        assert '다시 요청' in blocked['issues'][0]
        _assert_friendly_messages(blocked['messages'])
    finally:
        connection.close()
        store.close()


def test_human_csv_numeric_pair_refreshes_current_rules_mirror_descriptions_and_proof():
    draft = _table(1000000.0)
    before = deepcopy(draft)
    node = draft['workflow']['nodes'][1]
    edit = _request(node, ['- 조건 1 (amount) 비교 기준: 숫자 1000000 → 숫자 1500000'], [
        {'path': 'execution_plan.filters[0].value', 'old_value': 1000000.0, 'new_value': 1500000},
        {'path': 'execution_plan.filters[0].compare_field', 'old_value': None, 'new_value': None},
    ])
    current = {'messages': [{'role': 'user', 'content': '100만원 이상 자료를 CSV로 저장해줘'},
                            {'role': 'user', 'content': edit['request']}],
               'draft': draft, 'status': 'ready', 'issues': [], 'resources': [],
               'pending_questions': [], 'trace': [], 'node_edit': edit,
               'node_library': [], 'required_node_refs': []}
    result = apply_node_edit(current)
    assert result['edit_route'] == 'validate', result.get('issues')
    revised = {**current, **result}
    settings = revised['draft']['workflow']['nodes'][1]['settings']
    assert settings['execution_plan']['filters'][0]['value'] == 1500000
    assert settings['execution_plan']['filters'][0]['compare_field'] is None
    assert json.loads(settings['instructions']) == settings['execution_plan']
    for key in ('purpose', 'instructions', 'explanation'):
        assert '100만원' not in revised['draft'][key]
        assert '1000000' not in revised['draft'][key]
    assert '1500000' in revised['draft']['instructions']
    assert revised['draft']['requirements'] == before['requirements']
    assert revised['draft']['execution_plan'] == before['execution_plan']
    assert revised['draft']['workflow']['nodes'][0] == before['workflow']['nodes'][0]
    assert revised['draft']['workflow']['nodes'][2] == before['workflow']['nodes'][2]
    assert draft == before
    assert proof_issue(revised) is None
    assert validate_node(revised)['status'] == 'ready'
    _assert_friendly_messages(revised['messages'])
    forged = deepcopy(revised)
    forged['draft']['workflow']['nodes'][1]['settings']['execution_plan']['filters'][0]['value'] = 2000000
    assert proof_issue(forged)


def test_saved_legacy_request_success_and_failure_are_projected_without_mutating_records():
    request = ('`메일 읽기` 작업 (노드 ID: mail-internal)의 설정을 다음과 같이 바꿔줘.\n'
               '- 조회 한도 (arguments_json.max_results): 10 → 5\n'
               '다른 작업의 설정은 유지해줘.')
    success = ('mail-internal 설정을 변경했습니다: arguments_json.max_results: 10 → 5. '
               '다른 작업의 실행 설정은 유지했습니다. 저장 전에 변경된 값을 확인해 주세요.')
    failure = '노드의 이전 값이 변경되었습니다. 설정창을 다시 열어 주세요: arguments_json.max_results'
    messages = [{'role': 'user', 'content': request}, {'role': 'assistant', 'content': success},
                {'role': 'assistant', 'content': failure},
                {'role': 'user', 'content': '사용자 열 arguments_json.max_results는 원래 이름을 보존해주세요.'},
                {'role': 'assistant', 'content': '사용자 열 arguments_json.max_results를 보존했습니다.'}]
    original = deepcopy(messages)
    shown = _public_node_edit_messages(messages)
    assert messages == original
    for message in shown[:3]:
        assert not re.search(r'arguments_json|mail-internal|노드 ID:', message['content'])
    assert '조회 한도: 10 → 5' in shown[0]['content']
    assert '조회 한도: 10 → 5' in shown[1]['content']
    assert '이전 값이 변경' in shown[2]['content'] and '다시 열고' in shown[2]['content']
    assert shown[3:] == original[3:]
    assert _public_node_edit_messages(shown) == shown


def test_legacy_projection_preserves_literal_user_paths_in_labels_and_values():
    request = ('`arguments_json.max_results` 작업 (노드 ID: literal-user-name)의 설정을 다음과 같이 바꿔줘.\n'
               '- 검색 조건 (arguments_json.query): execution_plan.filters[0].value → arguments_json.max_results\n'
               '다른 작업의 설정은 유지해줘.')
    messages = [{'role': 'user', 'content': request}]
    original = deepcopy(messages)
    shown = _public_node_edit_messages(messages)
    assert messages == original
    assert shown[0]['content'].startswith('"arguments_json.max_results" 작업의 설정')
    assert '- 검색 조건: "execution_plan.filters[0].value" → "arguments_json.max_results"' in shown[0]['content']
    assert '(arguments_json.query)' not in shown[0]['content']
    assert 'literal-user-name' not in shown[0]['content']
