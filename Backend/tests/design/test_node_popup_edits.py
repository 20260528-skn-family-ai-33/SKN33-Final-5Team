"""A popup setting change is a bounded edit, never a new design question."""
from copy import deepcopy
import json

from langgraph.types import Command

from agent_builder.design.graph import build_graph
from agent_builder.design.node_edit import apply_node_edit, proof_issue
from agent_builder.design.nodes.validation import validate_node
from contracts.models import AgentDraft, ExecutionPlan
from infrastructure.persistence.storage import Storage
from infrastructure.persistence.ids import uid
from service.worker import handle
from tests.design.test_gmail_slack_workflow import CHANNEL, Forbidden, state
from tests.workflow_engine.test_workflow import node, bind, edge


PROMPT = 'gmail에서 안읽은 메일 요약 정리해서 slack에 올려줘'


def _graph(store):
    forbidden = Forbidden()
    return build_graph(store, conversation=lambda _: forbidden.invoke(),
                       meaning_transport=forbidden, capability_mode='enforce',
                       capability_transport=forbidden, condition_mode='enforce',
                       condition_transport=forbidden, requirement_mode='enforce',
                       requirement_transport=forbidden, verifier_mode='off')


def _popup(node, key, old, new, label):
    request = (f"`{node['label']}` 작업 (노드 ID: {node['id']})의 설정을 다음과 같이 바꿔줘.\n"
               f'- {label} (arguments_json.{key}): {old} → {new}\n'
               '다른 작업의 설정은 유지해줘.')
    return {'request': request, 'node_id': node['id'], 'label': node['label'],
            'changes': [{'path': f'arguments_json.{key}', 'old_value': old, 'new_value': new}]}


def _ready(graph, config):
    start = state(PROMPT)
    start['session_id'] = config['configurable']['thread_id']
    first = graph.invoke(start, config)
    assert first['status'] == 'needs_clarification'
    ready = graph.invoke(Command(resume={'type': 'message', 'content': CHANNEL}), config)
    assert ready['status'] == 'ready'
    return ready


def test_unread_gmail_slack_count_popup_is_fast_and_preserves_other_settings(tmp_path):
    store = Storage(tmp_path)
    graph, connection = _graph(store)
    config = {'configurable': {'thread_id': 'popup-count'}}
    try:
        ready = _ready(graph, config)
        old = deepcopy(ready['draft'])
        reader = old['workflow']['nodes'][0]
        edit = _popup(reader, 'max_results', 100, 10, '조회 한도')
        revised = graph.invoke(Command(resume={'type': 'message', 'content': edit['request'],
                                                'node_edit': edit}), config)
        assert revised['status'] == 'ready', revised['issues']
        assert not revised['pending_questions'] and not revised['issues']
        assert proof_issue(revised) is None
        nodes = revised['draft']['workflow']['nodes']
        assert json.loads(nodes[0]['settings']['arguments_json'])['max_results'] == 10
        assert '최대 10건' in nodes[0]['label']
        assert nodes[1]['settings']['instructions'] == old['workflow']['nodes'][1]['settings']['instructions'].replace('최대 100건', '최대 10건')
        assert nodes[2] == old['workflow']['nodes'][2]
        assert '최대 10건' in revised['draft']['instructions']
        assert '최대 100건' not in revised['draft']['explanation']
        assert revised['draft']['workflow']['edges'] == old['workflow']['edges']
        assert any(item['node'] == 'node_edit' and item['model_calls'] == 0 for item in revised['trace'])
        saved = graph.invoke(Command(resume={'type': 'confirm'}), config)
        assert saved['status'] == 'saved'
        persisted = store.agent(saved['saved_agent_id'])['config']
        assert json.loads(persisted['workflow']['nodes'][0]['settings']['arguments_json'])['max_results'] == 10
        assert '최대 100건' not in persisted['instructions']
        assert '최대 100건' not in persisted['workflow']['nodes'][1]['settings']['instructions']
    finally:
        connection.close()
        store.close()


def test_gmail_count_popup_keeps_verified_origin_across_repeated_edits(tmp_path):
    store = Storage(tmp_path)
    graph, connection = _graph(store)
    config = {'configurable': {'thread_id': 'popup-count-repeated'}}
    try:
        revised = _ready(graph, config)
        base_turn = None
        for number, (old, new) in enumerate(((100, 10), (10, 20), (20, 30)), 1):
            reader = revised['draft']['workflow']['nodes'][0]
            edit = _popup(reader, 'max_results', old, new, '조회 한도')
            revised = graph.invoke(Command(resume={'type': 'message', 'content': edit['request'],
                                                    'node_edit': edit}), config)
            assert revised['status'] == 'ready', revised['issues']
            assert proof_issue(revised) is None
            assert sum(item['node'] == 'node_edit' and item['model_calls'] == 0
                       for item in revised['trace'] if 'model_calls' in item) == number
            if base_turn is None:
                base_turn = revised['node_edit_proof']['base_turn']
            assert revised['node_edit_proof']['base_turn'] == base_turn
            nodes = revised['draft']['workflow']['nodes']
            assert json.loads(nodes[0]['settings']['arguments_json'])['max_results'] == new
            for text in (nodes[0]['label'], nodes[1]['settings']['instructions'],
                         revised['draft']['instructions'], revised['draft']['explanation']):
                assert f'최대 {new}건' in text
                assert f'최대 {old}건' not in text
        saved = graph.invoke(Command(resume={'type': 'confirm'}), config)
        assert saved['status'] == 'saved'
        persisted = store.agent(saved['saved_agent_id'])['config']
        assert json.loads(persisted['workflow']['nodes'][0]['settings']['arguments_json'])['max_results'] == 30
    finally:
        connection.close()
        store.close()


def test_gmail_count_popup_retry_keeps_verified_origin(tmp_path):
    store = Storage(tmp_path)
    graph, connection = _graph(store)
    config = {'configurable': {'thread_id': 'popup-count-retry'}}
    try:
        ready = _ready(graph, config)
        first = _popup(ready['draft']['workflow']['nodes'][0], 'max_results', 100, 10, '조회 한도')
        revised = graph.invoke(Command(resume={'type': 'message', 'content': first['request'],
                                               'node_edit': first}), config)
        assert revised['status'] == 'ready'
        reader = revised['draft']['workflow']['nodes'][0]
        stale = _popup(reader, 'max_results', 11, 20, '조회 한도')
        failed = graph.invoke(Command(resume={'type': 'message', 'content': stale['request'],
                                              'node_edit': stale}), config)
        assert failed['status'] == 'validation_failed'
        assert '이전 값이 변경' in failed['issues'][0]
        assert failed['node_edit_failure']['base_turn'] == revised['node_edit_proof']['base_turn']
        corrected = _popup(reader, 'max_results', 10, 20, '조회 한도')
        retry = graph.invoke({**failed, 'messages': failed['messages'] +
                              [{'role': 'user', 'content': corrected['request']}],
                              'node_edit': corrected}, config)
        assert retry['status'] == 'ready', retry['issues']
        assert proof_issue(retry) is None
        assert retry['node_edit_proof']['base_turn'] == revised['node_edit_proof']['base_turn']
        nodes = retry['draft']['workflow']['nodes']
        assert json.loads(nodes[0]['settings']['arguments_json'])['max_results'] == 20
        assert '최대 20건' in nodes[1]['settings']['instructions']
        assert '최대 10건' not in retry['draft']['explanation']
    finally:
        connection.close()
        store.close()


def test_stale_or_hidden_popup_value_stops_with_specific_error(tmp_path):
    for kind in ('stale', 'hidden'):
        store = Storage(tmp_path / kind)
        graph, connection = _graph(store)
        config = {'configurable': {'thread_id': 'popup-' + kind}}
        try:
            ready = _ready(graph, config)
            reader = ready['draft']['workflow']['nodes'][0]
            edit = _popup(reader, 'max_results', 99 if kind == 'stale' else 100, 10, '조회 한도')
            if kind == 'hidden':
                edit['changes'][0]['new_value'] = 500
            result = graph.invoke(Command(resume={'type': 'message', 'content': edit['request'],
                                                  'node_edit': edit}), config)
            assert result['status'] == 'validation_failed'
            assert not result.get('pending_questions')
            assert '이전 값이 변경' in result['issues'][0] if kind == 'stale' else '표시된 이전 값 또는 새 값' in result['issues'][0]
            assert result['draft'] == ready['draft']
            if kind == 'stale':
                corrected = _popup(reader, 'max_results', 100, 10, '조회 한도')
                retry = graph.invoke({**result, 'messages': result['messages'] +
                                      [{'role': 'user', 'content': corrected['request']}],
                                      'node_edit': corrected}, config)
                assert retry['status'] == 'ready', retry['issues']
                assert json.loads(retry['draft']['workflow']['nodes'][0]['settings']['arguments_json'])['max_results'] == 10
        finally:
            connection.close()
            store.close()


def test_other_operation_channel_edit_in_saved_agent_session(tmp_path):
    store = Storage(tmp_path)
    graph, connection = _graph(store)
    config = {'configurable': {'thread_id': 'popup-save-original'}}
    try:
        ready = _ready(graph, config)
        saved = graph.invoke(Command(resume={'type': 'confirm'}), config)
        original = store.agent(saved['saved_agent_id'])['config']
        sender = original['workflow']['nodes'][2]
        updated_channel = 'C0CH769U6BY'
        edit = _popup(sender, 'channel_id', CHANNEL, updated_channel, '채널 ID')
        session = handle({'directory': str(tmp_path), 'action': 'new_session',
                          'model': 'gpt-4.1-mini', 'credential_id': uid(),
                          'agent_id': saved['saved_agent_id']})
        revised = handle({'directory': str(tmp_path), 'action': 'message', 'id': session['id'],
                          'revision': session['revision'], 'content': edit['request'],
                          'node_edit': edit, 'generation_request_id': uid()},
                         conversation=lambda _: Forbidden().invoke())
        assert revised['status'] == 'validation_failed'
        assert '다른 단계나 설명에 이전 설정값' in revised['issues'][0]
        nodes = revised['draft']['workflow']['nodes']
        assert json.loads(nodes[2]['settings']['arguments_json'])['channel_id'] == CHANNEL
        assert nodes == original['workflow']['nodes']
        assert not revised['pending_questions']
    finally:
        connection.close()
        store.close()


def test_query_edit_cannot_leave_unread_description_with_different_scope(tmp_path):
    store = Storage(tmp_path)
    graph, connection = _graph(store)
    config = {'configurable': {'thread_id': 'popup-query'}}
    try:
        ready = _ready(graph, config)
        original = deepcopy(ready['draft'])
        reader = original['workflow']['nodes'][0]
        old_query = json.loads(reader['settings']['arguments_json'])['query']
        edit = _popup(reader, 'query', old_query, 'from:boss@example.com', '검색 조건')
        result = graph.invoke(Command(resume={'type': 'message', 'content': edit['request'],
                                              'node_edit': edit}), config)
        assert result['status'] == 'validation_failed'
        assert '검색 조건은 작업 범위' in result['issues'][0]
        assert result['draft'] == original
        assert not result['pending_questions']
    finally:
        connection.close()
        store.close()


def test_numeric_comparison_popup_changes_only_target_filter():
    original = 'qty가 3 이상인 행을 CSV로 저장해줘'
    plan = ExecutionPlan(filters=[{'field': 'qty', 'operator': 'gte', 'value': 3,
                                   'compare_field': None, 'value_type': 'number'}],
                         sort=[], match='all', missing_policy='keep', duplicate_policy='keep',
                         duplicate_field=None, output_format='csv').model_dump()
    workflow = {'entry': 'read', 'nodes': [node('read', 'read_table'),
                node('filter', 'filter_sort', [bind('table', 'read', 'table')], execution_plan=plan),
                node('save', 'write_result', [bind('table', 'filter', 'table')], format='csv')],
                'edges': [edge('read', 'filter'), edge('filter', 'save')]}
    draft = AgentDraft.model_validate({
        'name': '표 조건', 'purpose': '표를 조건에 따라 CSV로 저장',
        'instructions': '표를 조건에 따라 CSV로 저장',
        'tool_ids': ['read_table', 'filter_sort', 'write_result'], 'document_ids': [],
        'requirements': [{'description': original, 'source_id': 'user', 'quote': original}],
        'questions': [], 'unsupported': [], 'explanation': '표 조건을 적용해 CSV로 저장',
        'execution_plan': ExecutionPlan(filters=[], sort=[], match='all', missing_policy='keep',
                                        duplicate_policy='keep', duplicate_field=None,
                                        output_format='csv').model_dump(),
        'workflow': workflow}).model_dump()
    request = ('`filter` 작업 (노드 ID: filter)의 설정을 다음과 같이 바꿔줘.\n'
               '- 조건 1 (qty): 비교 열로 설정. execution_plan.filters[0].value: 3 → null; '
               'execution_plan.filters[0].compare_field: null → "limit"; value_type은 number로 유지.\n'
               '다른 작업의 설정은 유지해줘.')
    edit = {'request': request, 'node_id': 'filter', 'label': 'filter', 'changes': [
        {'path': 'execution_plan.filters[0].value', 'old_value': 3, 'new_value': None},
        {'path': 'execution_plan.filters[0].compare_field', 'old_value': None, 'new_value': 'limit'}]}
    current = {'messages': [{'role': 'user', 'content': original}, {'role': 'user', 'content': request}],
               'draft': draft, 'status': 'ready', 'issues': [], 'resources': [],
               'pending_questions': [], 'trace': [], 'node_edit': edit, 'node_library': [],
               'required_node_refs': []}
    result = apply_node_edit(current)
    assert result['edit_route'] == 'validate', result.get('issues')
    revised = {**current, **result}
    filt = revised['draft']['workflow']['nodes'][1]['settings']['execution_plan']['filters'][0]
    assert filt['value'] is None and filt['compare_field'] == 'limit'
    assert revised['draft']['workflow']['nodes'][0] == draft['workflow']['nodes'][0]
    assert revised['draft']['workflow']['nodes'][2] == draft['workflow']['nodes'][2]
    assert proof_issue(revised) is None
    assert validate_node(revised)['status'] == 'ready'


def test_numeric_argument_accepts_integer_decimal_edits_but_not_text():
    request = '두 표의 금액 차이를 허용 오차로 비교해줘'
    arguments = {'baseline': {'rows': [{'id': 'a', 'amount': 1}]},
                 'candidate': {'rows': [{'id': 'a', 'amount': 1.1}]},
                 'keys': ['id'], 'compare_columns': ['amount'],
                 'absolute_tolerance': 0}
    target = node('diff', 'compare_tables', operation='diff',
                  arguments_json=json.dumps(arguments, ensure_ascii=False))
    draft = AgentDraft.model_validate({
        'name': '표 차이', 'purpose': request, 'instructions': request,
        'tool_ids': ['compare_tables'], 'document_ids': [],
        'requirements': [{'description': request, 'source_id': 'user', 'quote': request}],
        'questions': [], 'unsupported': [], 'explanation': request,
        'execution_plan': ExecutionPlan(filters=[], sort=[], match='all',
                                        missing_policy='keep', duplicate_policy='keep',
                                        duplicate_field=None, output_format='json').model_dump(),
        'workflow': {'entry': 'diff', 'nodes': [target], 'edges': []}}).model_dump()
    current = {'messages': [{'role': 'user', 'content': request}], 'draft': draft,
               'status': 'ready', 'issues': [], 'resources': [], 'pending_questions': [],
               'trace': [], 'node_library': [], 'required_node_refs': []}

    for old, new in [(0, 0.1), (0.1, 1)]:
        edit = _popup(target, 'absolute_tolerance', old, new, 'absolute_tolerance')
        current = {**current, 'messages': current['messages'] +
                   [{'role': 'user', 'content': edit['request']}], 'node_edit': edit}
        result = apply_node_edit(current)
        assert result['edit_route'] == 'validate', result.get('issues')
        current = {**current, **result, 'status': 'ready'}
        target = current['draft']['workflow']['nodes'][0]
        assert json.loads(target['settings']['arguments_json'])['absolute_tolerance'] == new
        assert proof_issue(current) is None
        assert validate_node(current)['status'] == 'ready'

    text_edit = _popup(target, 'absolute_tolerance', 1, '0.5', 'absolute_tolerance')
    blocked = apply_node_edit({**current, 'messages': current['messages'] +
                               [{'role': 'user', 'content': text_edit['request']}],
                               'node_edit': text_edit})
    assert blocked['edit_route'] == 'stop'
    assert '직접 변경할 수 없습니다' in blocked['issues'][0]
