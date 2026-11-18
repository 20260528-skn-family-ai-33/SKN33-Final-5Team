"""Reported 2026-11-17: '중복주문.csv' dedup + sort request asked again, then never saved.

Real service entry + LangGraph + SQLite; capability, condition and planner replies are
fixtures, so these check routing/state contracts, not the production model.
"""
import io
import json
import re
import tempfile

import pytest

from agent_builder.conditions.condition_readiness import plan_issues, policy_clauses, sort_request
from agent_builder.design.presentation import public_draft_explanation
from agent_builder.design.save_intent import is_save_request
from contracts.models import AgentDraft, ExecutionPlan
from infrastructure.persistence.storage import Storage
from service.worker import handle

COLUMNS = ['주문번호', '고객명', '주문일', '금액']
ROWS = [{'주문번호': 'A1', '고객명': '김', '주문일': '2026-11-02', '금액': 100},
        {'주문번호': 'A1', '고객명': '김', '주문일': '2026-11-02', '금액': 100},
        {'주문번호': 'B2', '고객명': '이', '주문일': '2026-11-01', '금액': 200}]
TABLE = {'name': '중복주문.csv', 'kind': 'table', 'format': 'csv', 'columns': COLUMNS, 'version': 1,
         'hash': 'h', 'profile': {}, 'text': ''}
REQUEST = ('중복주문.csv에서 주문번호가 같은 행은 첫 행만 남기고, 주문일 오름차순으로 정렬해서 CSV로 저장해줘. '
           '빈 값은 그대로 유지해.')
SORT = [{'field': '주문일', 'direction': 'ascending', 'value_type': 'text'}]


# --- cause 1: a file name is not a duplicate-policy subject -------------------------------

@pytest.mark.parametrize('name', ['중복주문.csv', '중복_주문목록.xlsx', '빈값정리.json'])
def test_file_name_does_not_swallow_the_written_policy(name):
    text = f'{name}에서 주문번호가 같은 행은 첫 행만 남기고 CSV로 저장해줘. 빈 값은 그대로 유지해.'
    clauses = {key: value for key, value, _ in policy_clauses(text, COLUMNS)}
    assert clauses == {'duplicate.policy': 'first', 'missing.policy': 'keep'}


def test_policy_words_outside_file_names_still_count():
    assert policy_clauses('중복은 마지막 행만 남겨줘.', COLUMNS) == [('duplicate.policy', 'last', '중복은 마지막 행만 남겨줘')]
    assert policy_clauses('중복주문.csv를 CSV로 저장해줘.', COLUMNS) == []


# --- cause 2: a sort the user wrote is not an addition to a dedup contract -----------------

def ready_dedup_ledger():
    slot = lambda key, value: {'key': key, 'status': 'specified', 'value': value, 'evidence': []}
    return {'decision': 'ready', 'operations': ['deduplicate'],
            'slots': {'duplicate.field': slot('duplicate.field', '주문번호'),
                      'duplicate.policy': slot('duplicate.policy', 'first'),
                      'missing.policy': slot('missing.policy', 'keep'), 'output.format': slot('output.format', 'csv')}}


def dedup_draft(sort, filters=()):
    plan = ExecutionPlan(filters=list(filters), sort=sort, match='all', missing_policy='keep',
                         duplicate_policy='first', duplicate_field='주문번호', output_format='csv').model_dump()
    nodes = [{'id': 'read', 'tool': 'read_table', 'label': 'read', 'bindings': [], 'settings': {}},
             {'id': 'sort', 'tool': 'filter_sort', 'label': 'sort',
              'bindings': [{'input': 'table', 'node_id': 'read', 'output': 'table'}], 'settings': {'execution_plan': plan}},
             {'id': 'out', 'tool': 'write_result', 'label': 'out',
              'bindings': [{'input': 'table', 'node_id': 'sort', 'output': 'table'}], 'settings': {'format': 'csv'}}]
    edges = [{'source': 'read', 'target': 'sort', 'branch': 'next'}, {'source': 'sort', 'target': 'out', 'branch': 'next'}]
    return {'questions': [], 'unsupported': [], 'execution_plan': plan, 'workflow': {'entry': 'read', 'nodes': nodes, 'edges': edges}}


PLANNED = ['주문번호', '고객명', '주문일', '예정주문일', '금액']
PLANNED_REQUEST = REQUEST.replace('주문일 오름차순', '예정주문일 오름차순')


def table_state(text, columns=COLUMNS):
    return {'messages': [{'role': 'user', 'content': text}], 'resources': [{'id': 't', 'kind': 'table', 'columns': columns}]}


def sort_problems(text, sort, columns=COLUMNS):
    return [p for p in plan_issues(ready_dedup_ledger(), dedup_draft(sort), state=table_state(text, columns)) if '정렬' in p]


def test_requested_sorts_use_whole_attached_column_names():
    from agent_builder.conditions.condition_readiness import requested_sorts
    assert requested_sorts(table_state(REQUEST)) == [{'field': '주문일', 'direction': 'ascending', 'value_type': None}]
    assert requested_sorts(table_state(PLANNED_REQUEST, PLANNED)) == [
        {'field': '예정주문일', 'direction': 'ascending', 'value_type': None}]
    # "예정주문일" written while only 주문일 is attached is not a 주문일 sort.
    assert requested_sorts(table_state(PLANNED_REQUEST, COLUMNS)) == []
    assert requested_sorts(table_state('금액을 숫자 기준으로 내림차순, 주문일은 오름차순으로 정렬해줘.')) == [
        {'field': '금액', 'direction': 'descending', 'value_type': 'number'},
        {'field': '주문일', 'direction': 'ascending', 'value_type': None}]
    later = table_state(REQUEST)
    later['messages'] += [{'role': 'assistant', 'content': '확인'}, {'role': 'user', 'content': '주문일 내림차순으로 바꿔줘'}]
    assert requested_sorts(later) == [{'field': '주문일', 'direction': 'descending', 'value_type': None}]


def test_written_sort_key_and_direction_are_allowed():
    assert sort_problems(REQUEST, SORT) == []
    assert sort_problems('주문번호 중복은 첫 행만 남기고 금액 숫자 내림차순으로 정렬해줘.',
                         [{'field': '금액', 'direction': 'descending', 'value_type': 'number'}]) == []
    assert sort_problems(PLANNED_REQUEST, [{'field': '예정주문일', 'direction': 'ascending', 'value_type': 'text'}], PLANNED) == []


@pytest.mark.parametrize('text,sort,columns', [
    ('주문번호 중복은 첫 행만 남겨줘.', SORT, COLUMNS),                                    # no sort requested
    (REQUEST, [], COLUMNS),                                                              # requested sort dropped (PR #142 P1)
    (REQUEST, [{'field': '주문일', 'direction': 'descending', 'value_type': 'text'}], COLUMNS),  # opposite direction
    (REQUEST, [{'field': '금액', 'direction': 'ascending', 'value_type': 'number'}], COLUMNS),   # another column
    (REQUEST, SORT + [{'field': '금액', 'direction': 'ascending', 'value_type': 'number'}], COLUMNS),
    (PLANNED_REQUEST, SORT, PLANNED),                                                    # 주문일 for 예정주문일 (PR #142 P1)
    ('주문번호 중복은 첫 행만 남기고 금액 숫자 내림차순으로 정렬해줘.',
     [{'field': '금액', 'direction': 'descending', 'value_type': 'text'}], COLUMNS),         # written type changed
])
def test_missing_unrequested_or_changed_sort_is_rejected(text, sort, columns):
    assert sort_problems(text, sort, columns)


def test_sort_before_the_output_path_is_judged_on_the_executable_path():
    draft = dedup_draft(SORT)
    draft['workflow']['edges'] = []  # the sort node is not on the path to the output
    draft['workflow']['entry'] = 'out'
    assert any('정렬' in p for p in plan_issues(ready_dedup_ledger(), draft, state=table_state(REQUEST)))


def test_dedup_contract_still_rejects_an_added_filter():
    state = table_state(REQUEST)
    added = [{'field': '금액', 'operator': 'gt', 'value': 100, 'compare_field': None, 'value_type': 'number'}]
    assert any('선별' in p for p in plan_issues(ready_dedup_ledger(), dedup_draft(SORT, added), state=state))


# --- cause 3: a typed save request is the save button --------------------------------------

@pytest.mark.parametrize('text', ['저장', '확인완료. 저장', '확인 완료, 저장해줘', '네 저장해 주세요', '좋아요 이대로 저장!', 'OK 저장'])
def test_save_only_messages(text):
    assert is_save_request(text)


@pytest.mark.parametrize('text', ['저장 형식을 JSON으로 바꿔줘', '저장하기 전에 금액 열도 넣어줘', '결과를 저장할 폴더는 어디야?',
                                  '저장하지 마', '정렬 빼고 저장해줘', '저장 위치 바꿔줘', ''])
def test_change_requests_are_not_save_requests(text):
    assert not is_save_request(text)


class Fake:
    def __init__(self, make):
        self.make = make; self.calls = 0
    def invoke(self, messages):
        self.calls += 1
        return {'content': json.dumps(self.make(messages), ensure_ascii=False)}


CURRENT = {'request': REQUEST}


def capability(messages):
    return dict(decision='existing_tools', tool_ids=['read_table', 'filter_sort', 'write_result'],
                missing_conditions=[], missing_actions=[], reason='fixture', user_quotes=[CURRENT['request']])


def condition(messages):
    ids = sorted(set(re.findall(r'user:\d+:\d+', json.dumps([m.get('content') for m in messages], ensure_ascii=False))))
    values = {'duplicate.field': '주문번호', 'duplicate.policy': 'first', 'missing.policy': 'keep', 'output.format': 'csv'}
    return {'operations': ['deduplicate'], 'unsupported_actions': [],
            'conditions': [{'key': k, 'value': v, 'status': 'specified', 'evidence_ids': ids[-1:]} for k, v in values.items()]}


def node(id, tool, bindings=(), **settings):
    base = dict(file_ids=[], query=None, fields=[], instructions=None, format=None, question=None,
                condition_field=None, condition_operator=None)
    return {'id': id, 'tool': tool, 'label': id, 'bindings': list(bindings), 'settings': {**base, **settings}}


def make_planner(calls, sort=SORT, schedule=None, bypass=False, sort_first=False):
    def planner(state):
        calls.append(state['messages'][-1]['content'])
        chosen = sort(state) if callable(sort) else sort
        plan = ExecutionPlan(filters=[], sort=chosen, match='all', missing_policy='keep', duplicate_policy='first',
                             duplicate_field='주문번호', output_format='csv').model_dump()
        file_id = state['resources'][0]['id']
        nodes = [node('read', 'read_table', file_ids=[file_id]),
                 node('sort', 'filter_sort', [{'input': 'table', 'node_id': 'read', 'output': 'table'}],
                      execution_plan=plan, instructions=json.dumps(plan, ensure_ascii=False)),
                 node('out', 'write_result', [{'input': 'table', 'node_id': 'sort', 'output': 'table'}], format='csv')]
        edges = [{'source': 'read', 'target': 'sort', 'branch': 'next'}, {'source': 'sort', 'target': 'out', 'branch': 'next'}]
        if bypass:
            # read -> dedup -> sort -> out, but out consumes the dedup table (PR #142 review 4).
            dedup = {**plan, 'sort': []}
            nodes[1:1] = [node('dedup', 'filter_sort', [{'input': 'table', 'node_id': 'read', 'output': 'table'}],
                               execution_plan=dedup, instructions=json.dumps(dedup, ensure_ascii=False))]
            nodes[2]['bindings'] = [{'input': 'table', 'node_id': 'dedup', 'output': 'table'}]
            nodes[3]['bindings'] = [{'input': 'table', 'node_id': 'dedup', 'output': 'table'}]
            edges = [{'source': 'read', 'target': 'dedup', 'branch': 'next'},
                     {'source': 'dedup', 'target': 'sort', 'branch': 'next'},
                     {'source': 'sort', 'target': 'out', 'branch': 'next'}]
        if sort_first:
            # read -> sort only -> dedup only (consumes the sorted table) -> out (PR #142 review 6).
            sorting = {**plan, 'duplicate_policy': 'keep', 'duplicate_field': None}
            dedup = {**plan, 'sort': []}
            nodes = [nodes[0],
                     node('sort', 'filter_sort', [{'input': 'table', 'node_id': 'read', 'output': 'table'}],
                          execution_plan=sorting, instructions=json.dumps(sorting, ensure_ascii=False)),
                     node('dedup', 'filter_sort', [{'input': 'table', 'node_id': 'sort', 'output': 'table'}],
                          execution_plan=dedup, instructions=json.dumps(dedup, ensure_ascii=False)),
                     node('out', 'write_result', [{'input': 'table', 'node_id': 'dedup', 'output': 'table'}], format='csv')]
            edges = [{'source': 'read', 'target': 'sort', 'branch': 'next'},
                     {'source': 'sort', 'target': 'dedup', 'branch': 'next'},
                     {'source': 'dedup', 'target': 'out', 'branch': 'next'}]
        draft = AgentDraft(name='중복 주문 정리', purpose='중복 제거 후 정렬', instructions='요청대로 처리',
                           tool_ids=['read_table', 'filter_sort', 'write_result'], document_ids=[file_id],
                           requirements=[{'description': '요청', 'source_id': 'user', 'quote': CURRENT['request']}],
                           questions=[], question_options=[], unsupported=[], explanation='x', execution_plan=plan,
                           workflow={'entry': 'read', 'nodes': nodes, 'edges': edges}, schedule=schedule).model_dump()
        draft = public_draft_explanation(draft)
        return {'draft': draft, 'confirmed': False, 'saved_agent_id': None,
                'messages': state['messages'] + [{'role': 'assistant', 'content': draft['explanation']}],
                'repair_count': state.get('repair_count', 0) + 1}
    return planner


@pytest.fixture
def service(monkeypatch):
    import service.handlers.design as worker
    for key, value in {'A4B_CAPABILITY_MODE': 'enforce', 'A4B_CONDITION_MODE': 'enforce',
                       'A4B_REQUIREMENT_MODE': 'off', 'A4B_VERIFIER_MODE': 'off'}.items():
        monkeypatch.setenv(key, value)
    fakes = {'cap': Fake(capability), 'cond': Fake(condition)}
    original = worker.build_graph

    def wrapped(*args, **kwargs):
        kwargs.update(capability_mode='enforce', capability_transport=fakes['cap'], condition_mode='enforce',
                      condition_transport=fakes['cond'], verifier_mode='off')
        return original(*args, **kwargs)
    monkeypatch.setattr(worker, 'build_graph', wrapped)
    directory = tempfile.mkdtemp()
    box = {'s': handle({'directory': directory, 'action': 'new_session', 'model': 'm', 'credential_id': 'local'})}

    def act(action, planner, **fields):
        box['s'] = handle({'directory': directory, 'action': action, 'id': box['s']['id'],
                           'revision': box['s']['revision'], **fields}, conversation=planner)
        return box['s']

    def saved():
        store = Storage(directory)
        try:return len(store.agents())
        finally:store.close()

    def run(agent_id):
        from workflow_engine.runner import run_workflow
        store = Storage(directory)
        try:return run_workflow(store, agent_id, '저장한 조건으로 실행', api_key='')
        finally:store.close()
    fakes['run'] = run
    CURRENT['request'] = REQUEST
    return act, saved, fakes


def press_save(act, planner):
    """The save button; a refused save leaves the session as it was."""
    try:
        return act('confirm', planner)
    except Exception:
        return act('session', planner)


@pytest.mark.parametrize('typed', ['확인완료. 저장', '저장 버튼', '저장해도 돼요'])
def test_typed_save_points_to_the_button_and_never_saves(service, typed):
    act, saved, fakes = service
    calls = []
    planner = make_planner(calls)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    first = act('message', planner, content=REQUEST)
    assert first['status'] == 'ready' and not first['pending_questions'] and not first['issues']
    assert len(calls) == 1
    reply = act('message', planner, content=typed)
    assert reply['status'] == 'ready' and saved() == 0 and not reply.get('saved_agent_id')
    assert len(calls) == 1 and fakes['cap'].calls == 1  # nothing re-planned for the save request
    assert [m['content'] for m in reply['messages'] if m['role'] == 'user'][-1] == typed
    assert '저장 버튼' in reply['messages'][-1]['content']
    done = press_save(act, planner)  # the approval wait is intact
    assert done['status'] == 'saved' and done['saved_agent_id'] and saved() == 1


def test_typed_save_on_a_blocked_draft_explains_instead_of_replanning(service):
    act, saved, fakes = service
    calls = []
    unrequested = [{'field': '금액', 'direction': 'descending', 'value_type': 'number'}]
    planner = make_planner(calls, sort=unrequested)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    first = act('message', planner, content=REQUEST)
    assert first['status'] == 'validation_failed' and first['issues']
    before = len(calls)
    reply = act('message', planner, content='확인완료. 저장')
    assert len(calls) == before and saved() == 0 and reply['status'] == 'validation_failed'
    assert reply['messages'][-1]['role'] == 'assistant'
    assert reply['messages'][-1]['content'].startswith('초안을 검증하지 못해 아직 저장할 수 없습니다.')


def test_typed_save_with_a_schedule_points_to_the_button():
    from types import SimpleNamespace
    from service.handlers.design import _chat_save_reply
    review = SimpleNamespace(tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={'kind': 'review'})])])
    state = {'status': 'ready', 'draft': {'workflow': {'nodes': []}, 'schedule': {'frequency': 'daily'}}}
    assert '저장 버튼' in _chat_save_reply(state, review)
    waiting = SimpleNamespace(tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={'kind': 'clarification'})])])
    assert '질문' in _chat_save_reply({'status': 'needs_clarification', 'draft': {'questions': ['어느 열?']}}, waiting)


def test_a_change_request_still_replans(service):
    act, saved, fakes = service
    calls = []
    planner = make_planner(calls)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    act('message', planner, content=REQUEST)
    act('message', planner, content='저장 형식을 JSON으로 바꿔줘')
    assert len(calls) >= 2 and calls[1] == '저장 형식을 JSON으로 바꿔줘' and saved() == 0


PLANNED_TABLE = {**TABLE, 'columns': PLANNED}
PLANNED_ROWS = [{**ROWS[0], '예정주문일': '2026-01-01'}, {**ROWS[1], '예정주문일': '2026-01-01'},
                {**ROWS[2], '예정주문일': '2026-01-02'}]
PLANNED_SORT = [{'field': '예정주문일', 'direction': 'ascending', 'value_type': 'text'}]


@pytest.mark.parametrize('table,rows,request_text,sort,order', [
    (TABLE, ROWS, REQUEST, SORT, ['B2', 'A1']),
    (PLANNED_TABLE, PLANNED_ROWS, PLANNED_REQUEST, PLANNED_SORT, ['A1', 'B2']),
])
def test_correct_sort_saves_by_chat_and_runs_in_the_requested_order(service, table, rows, request_text, sort, order):
    act, saved, fakes = service
    CURRENT['request'] = request_text
    calls = []
    planner = make_planner(calls, sort=sort)
    act('upload', planner, asset=dict(table), rows=rows)
    assert act('message', planner, content=request_text)['status'] == 'ready'
    done = press_save(act, planner)
    assert done['status'] == 'saved' and saved() == 1
    result = fakes['run'](done['saved_agent_id'])
    assert result['status'] == 'succeeded'
    assert csv_order(result) == order == [row['주문번호'] for row in result['rows']]


@pytest.mark.parametrize('table,rows,request_text,sort', [
    (TABLE, ROWS, REQUEST, []),                           # PR #142 P1: the requested sort is dropped
    (PLANNED_TABLE, PLANNED_ROWS, PLANNED_REQUEST, SORT),  # PR #142 P1: 주문일 used for 예정주문일
])
def test_draft_without_the_requested_sort_cannot_be_saved(service, table, rows, request_text, sort):
    act, saved, fakes = service
    CURRENT['request'] = request_text
    calls = []
    planner = make_planner(calls, sort=sort)
    act('upload', planner, asset=dict(table), rows=rows)
    first = act('message', planner, content=request_text)
    assert first['status'] == 'validation_failed' and any('요청한 정렬' in issue for issue in first['issues'])
    reply = act('message', planner, content='확인완료. 저장')
    assert saved() == 0 and reply['status'] == 'validation_failed'
    assert '요청한 정렬' in reply['messages'][-1]['content']
    with pytest.raises(ValueError):
        act('confirm', planner)
    assert saved() == 0


# --- PR #142 review 2: a later "정렬 빼고" withdraws the sort; "열을 기준으로" is a sort ---------

BASIS_REQUEST = REQUEST.replace('주문일 오름차순', '주문일 열을 기준으로 오름차순')


@pytest.mark.parametrize('phrase', ['주문일 열을 기준으로 오름차순', '주문일을 기준으로 오름차순', '주문일 기준 정렬은 오름차순',
                                    '주문일로 오름차순', '주문일 컬럼 기준 오름차순', '주문일 값 기준 오름차순'])
def test_sort_wording_between_column_and_direction(phrase):
    from agent_builder.conditions.condition_readiness import requested_sorts
    assert requested_sorts(table_state(REQUEST.replace('주문일 오름차순', phrase))) == [
        {'field': '주문일', 'direction': 'ascending', 'value_type': None}]


def conversation(*turns, columns=COLUMNS):
    messages = []
    for index, text in enumerate(turns):
        if index:
            messages.append({'role': 'assistant', 'content': '확인'})
        messages.append({'role': 'user', 'content': text})
    return {'messages': messages, 'resources': [{'id': 't', 'kind': 'table', 'columns': columns}]}


@pytest.mark.parametrize('withdrawal', ['정렬 빼고 저장해줘', '정렬은 하지 마', '정렬 없이 해줘', '입력 순서 그대로 둬'])
def test_later_withdrawal_cancels_the_earlier_sort(withdrawal):
    from agent_builder.conditions.condition_readiness import sort_request
    state = conversation(REQUEST, withdrawal)
    assert sort_request(state)['kind'] == 'cancelled'
    assert not [p for p in plan_issues(ready_dedup_ledger(), dedup_draft([]), state=state) if '정렬' in p]
    assert [p for p in plan_issues(ready_dedup_ledger(), dedup_draft(SORT), state=state) if '정렬' in p]
    # An unrelated later answer does not bring the withdrawn sort back.
    assert sort_request(conversation(REQUEST, withdrawal, '첫 행만 남김'))['kind'] == 'cancelled'
    # A later explicit sort request replaces the withdrawal.
    again = conversation(REQUEST, withdrawal, '역시 주문일 내림차순으로 정렬해줘')
    assert sort_request(again)['sorts'] == [{'field': '주문일', 'direction': 'descending', 'value_type': None}]


def test_negated_key_in_the_same_turn_is_not_requested():
    from agent_builder.conditions.condition_readiness import requested_sorts
    assert requested_sorts(table_state('주문일 오름차순 말고 금액 내림차순으로 정렬해줘')) == [
        {'field': '금액', 'direction': 'descending', 'value_type': None}]


LATEST = [{'field': '주문일', 'direction': 'descending', 'value_type': 'text'}]


@pytest.mark.parametrize('text,sort', [
    ('주문번호 중복은 첫 행만 남기고 날짜 순으로 정렬해서 주문일을 보고 싶어', SORT),
    ('주문번호 중복은 첫 행만 남기고 최신순으로 정리해줘', LATEST),          # 주문일 is never written
    ('주문번호 중복은 첫 행만 남기고 날짜순으로 정렬해줘', SORT),
])
def test_unreadable_sort_request_requires_a_sort_but_trusts_its_column(text, sort):
    assert sort_request(table_state(text))['kind'] == 'unparsed'
    assert [p for p in plan_issues(ready_dedup_ledger(), dedup_draft([]), state=table_state(text)) if '정렬' in p]
    assert not [p for p in plan_issues(ready_dedup_ledger(), dedup_draft(sort), state=table_state(text)) if '정렬' in p]


def withdrawn_sort(state):
    users = [m['content'] for m in state['messages'] if m['role'] == 'user']
    return [] if any('정렬 빼고' in text for text in users) else SORT


@pytest.mark.parametrize('planner_sort,saves,order', [
    (withdrawn_sort, True, ['A1', 'B2']),   # follows the withdrawal: saved, input order kept
    (SORT, False, None),                    # keeps the withdrawn sort: blocked
])
def test_withdrawing_the_sort_in_a_later_turn(service, planner_sort, saves, order):
    act, saved, fakes = service
    calls = []
    planner = make_planner(calls, sort=planner_sort)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    assert act('message', planner, content=REQUEST)['status'] == 'ready'
    changed = act('message', planner, content='정렬 빼고 저장해줘')
    assert not changed['pending_questions']
    done = press_save(act, planner)
    if saves:
        assert changed['status'] == 'ready' and done['status'] == 'saved' and saved() == 1
        result = fakes['run'](done['saved_agent_id'])
        assert result['status'] == 'succeeded' and csv_order(result) == order == [row['주문번호'] for row in result['rows']]
    else:
        assert changed['status'] == 'validation_failed' and any('정렬' in issue for issue in changed['issues'])
        assert done['status'] == 'validation_failed' and saved() == 0


@pytest.mark.parametrize('planner_sort,saves,order', [
    (SORT, True, ['B2', 'A1']),
    ([], False, None),
])
def test_column_basis_wording_is_enforced_end_to_end(service, planner_sort, saves, order):
    act, saved, fakes = service
    CURRENT['request'] = BASIS_REQUEST
    calls = []
    planner = make_planner(calls, sort=planner_sort)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    first = act('message', planner, content=BASIS_REQUEST)
    assert not first['pending_questions']
    done = press_save(act, planner)
    if saves:
        assert first['status'] == 'ready' and done['status'] == 'saved' and saved() == 1
        result = fakes['run'](done['saved_agent_id'])
        assert result['status'] == 'succeeded' and csv_order(result) == order == [row['주문번호'] for row in result['rows']]
    else:
        assert first['status'] == 'validation_failed' and any('요청한 정렬' in issue for issue in first['issues'])
        assert done['status'] == 'validation_failed' and saved() == 0


LATEST_REQUEST = REQUEST.replace('주문일 오름차순으로 정렬해서', '최신순으로 정렬해서')


@pytest.mark.parametrize('planner_sort,saves,order,issue', [
    (LATEST, True, ['A1', 'B2'], None),   # 주문일 내림차순 for "최신순": saved although 주문일 is never written
    ([], False, None, '정렬이 없습니다'),   # sort dropped: still blocked
    (SORT, False, None, '정렬 방향'),       # 오름차순 for "최신순" (PR #142 review 3): blocked
])
def test_latest_first_wording_saves_a_sorted_draft_end_to_end(service, planner_sort, saves, order, issue):
    act, saved, fakes = service
    CURRENT['request'] = LATEST_REQUEST
    calls = []
    planner = make_planner(calls, sort=planner_sort)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    first = act('message', planner, content=LATEST_REQUEST)
    assert not first['pending_questions']
    done = press_save(act, planner)
    if saves:
        assert first['status'] == 'ready' and done['status'] == 'saved' and saved() == 1
        result = fakes['run'](done['saved_agent_id'])
        assert result['status'] == 'succeeded' and csv_order(result) == order == [row['주문번호'] for row in result['rows']]
    else:
        assert first['status'] == 'validation_failed' and any(issue in text for text in first['issues'])
        assert done['status'] == 'validation_failed' and saved() == 0


def test_sort_direction_is_not_a_vague_duplicate_policy():
    text = '주문번호가 같은 행은 첫 행만 남기고, 최신순으로 정렬해서 CSV로 저장해줘.'
    assert policy_clauses(text, COLUMNS) == [('duplicate.policy', 'first', text[:-1])]
    assert policy_clauses('중복은 첫 행만 남기고 금액 높은 순으로 정렬해줘.', COLUMNS)[0][1] == 'first'
    # "keep the latest duplicate" is still vague and left to a question.
    assert policy_clauses('중복은 최신 것만 남겨줘.', COLUMNS)[0][1] == 'conflict'


# --- PR #142 review 3: column and direction checked each on its own; file names are not wording --

def test_unparsed_sort_hint_reads_key_and_direction_separately():
    hint = lambda text: {k: sort_request(table_state(text))[k] for k in ('kind', 'fields', 'direction')}
    assert hint('최신순으로 정렬해줘') == {'kind': 'unparsed', 'fields': [], 'direction': 'descending'}
    assert hint('정렬 기준은 주문일이야') == {'kind': 'unparsed', 'fields': ['주문일'], 'direction': None}
    assert hint('주문일 기준으로 정렬해줘')['fields'] == ['주문일']
    assert hint('주문일 순으로 정렬해줘')['fields'] == ['주문일']
    assert hint('정렬은 금액으로 해줘')['fields'] == ['금액']
    assert hint('정렬 기준은 주문일이 아니라 금액이야')['fields'] == []
    assert hint('내림차순 말고 오래된 순으로 정렬')['direction'] == 'ascending'
    assert hint('날짜 순으로 정렬해서 주문일을 보고 싶어') == {'kind': 'unparsed', 'fields': [], 'direction': None}


def test_unparsed_sort_rejects_the_wrong_key_or_direction():
    key = '주문번호 중복은 첫 행만 남기고 정렬해줘. 정렬 기준은 주문일이야.'
    by_number = [{'field': '주문번호', 'direction': 'ascending', 'value_type': 'text'}]
    assert sort_problems(key, by_number)
    assert not sort_problems(key, SORT) and not sort_problems(key, LATEST)
    latest = '주문번호 중복은 첫 행만 남기고 최신순으로 정렬해줘'
    assert sort_problems(latest, SORT)
    assert not sort_problems(latest, LATEST)


def test_file_name_is_not_a_sort_instruction():
    text = '정렬대상.csv에서 주문번호가 같은 행은 첫 행만 남기고 CSV로 저장해줘.'
    assert sort_request(table_state(text))['kind'] == 'none'
    assert not sort_problems(text, [])
    assert sort_problems(text, SORT)  # the single dedup contract still rejects an added sort
    assert sort_request(table_state('최신순.csv를 주문일 오름차순으로 정렬해줘'))['sorts'] == [
        {'field': '주문일', 'direction': 'ascending', 'value_type': None}]


FILE_ONLY_REQUEST = '정렬대상.csv에서 주문번호가 같은 행은 첫 행만 남기고 CSV로 저장해줘. 빈 값은 그대로 유지해.'


@pytest.mark.parametrize('planner_sort,saves', [([], True), (SORT, False)])
def test_file_named_like_a_sort_saves_a_dedup_only_draft_end_to_end(service, planner_sort, saves):
    act, saved, fakes = service
    CURRENT['request'] = FILE_ONLY_REQUEST
    planner = make_planner([], sort=planner_sort)
    act('upload', planner, asset=dict(TABLE, name='정렬대상.csv'), rows=ROWS)
    first = act('message', planner, content=FILE_ONLY_REQUEST)
    assert not first['pending_questions']
    done = press_save(act, planner)
    if saves:
        assert first['status'] == 'ready' and done['status'] == 'saved' and saved() == 1
        result = fakes['run'](done['saved_agent_id'])
        assert result['status'] == 'succeeded' and csv_order(result) == ['A1', 'B2']
    else:
        assert first['status'] == 'validation_failed' and done['status'] == 'validation_failed' and saved() == 0


KEY_LATER_REQUEST = REQUEST.replace('주문일 오름차순으로 정렬해서', '정렬해서')
BY_NUMBER = [{'field': '주문번호', 'direction': 'descending', 'value_type': 'text'}]


@pytest.mark.parametrize('planner_sort,saves,order', [
    (SORT, True, ['B2', 'A1']),    # 주문일 오름차순 after "정렬 기준은 주문일이야": saved
    (BY_NUMBER, False, None),      # 주문번호 sort (PR #142 review 3): blocked
])
def test_sort_key_named_in_a_later_turn_end_to_end(service, planner_sort, saves, order):
    act, saved, fakes = service
    CURRENT['request'] = KEY_LATER_REQUEST
    planner = make_planner([], sort=planner_sort)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    act('message', planner, content=KEY_LATER_REQUEST)
    changed = act('message', planner, content='정렬 기준은 주문일이야')
    assert not changed['pending_questions']
    done = press_save(act, planner)
    if saves:
        assert changed['status'] == 'ready' and done['status'] == 'saved' and saved() == 1
        result = fakes['run'](done['saved_agent_id'])
        assert result['status'] == 'succeeded' and csv_order(result) == order == [row['주문번호'] for row in result['rows']]
    else:
        assert changed['status'] == 'validation_failed' and any('주문일' in i for i in changed['issues'])
        assert done['status'] == 'validation_failed' and saved() == 0


# --- PR #142 review 4: negated withdrawal, follow-ups keep unsaid parts, file data path ----

def csv_order(result):
    """주문번호 order in the CSV file the run produced, not the run's response rows."""
    import csv as _csv
    content = result['artifact']['content'].lstrip('\ufeff')
    return [row['주문번호'] for row in _csv.DictReader(io.StringIO(content))]


def test_negated_withdrawal_keeps_the_sort():
    for kept in ['정렬은 취소하지 말고 저장해줘', '정렬 빼지 마', '정렬은 지우지 말고 그대로 둬', '정렬 제외하지 마']:
        assert sort_request(conversation(REQUEST, kept))['sorts'] == [
            {'field': '주문일', 'direction': 'ascending', 'value_type': None}], kept
    assert sort_request(conversation(REQUEST, '정렬은 취소해줘'))['kind'] == 'cancelled'


def test_follow_up_changes_only_what_it_says():
    desc = REQUEST.replace('주문일 오름차순', '주문일 내림차순')
    same_key = sort_request(conversation(desc, '정렬 기준은 주문일이야'))['sorts']
    assert same_key == [{'field': '주문일', 'direction': 'descending', 'value_type': None}]
    new_key = sort_request(conversation(desc, '정렬 기준은 금액이야'))['sorts']
    assert new_key == [{'field': '금액', 'direction': 'descending', 'value_type': None}]
    new_direction = sort_request(conversation(REQUEST, '최신순으로 바꿔줘'))['sorts']
    assert new_direction == [{'field': '주문일', 'direction': 'descending', 'value_type': None}]
    # An unrelated later answer or a bare "정렬해서 저장" changes nothing.
    assert sort_request(conversation(desc, '첫 행만 남김', '정렬해서 저장해줘'))['sorts'] == same_key


def test_writer_that_bypasses_the_sort_has_no_sort():
    from agent_builder.conditions.condition_readiness import _final_sort
    draft = dedup_draft(SORT)
    assert _final_sort(draft) == SORT
    dedup = {**draft['execution_plan'], 'sort': []}
    nodes = draft['workflow']['nodes']
    nodes.insert(1, {'id': 'dedup', 'tool': 'filter_sort', 'label': 'dedup', 'settings': {'execution_plan': dedup},
                     'bindings': [{'input': 'table', 'node_id': 'read', 'output': 'table'}]})
    nodes[2]['bindings'] = [{'input': 'table', 'node_id': 'dedup', 'output': 'table'}]
    nodes[3]['bindings'] = [{'input': 'table', 'node_id': 'dedup', 'output': 'table'}]
    draft['workflow']['edges'] = [{'source': 'read', 'target': 'dedup', 'branch': 'next'},
                                  {'source': 'dedup', 'target': 'sort', 'branch': 'next'},
                                  {'source': 'sort', 'target': 'out', 'branch': 'next'}]
    assert _final_sort(draft) == []
    nodes[3]['bindings'] = [{'input': 'table', 'node_id': 'sort', 'output': 'table'}]
    assert _final_sort(draft) == SORT


def kept_sort(state):
    return SORT


@pytest.mark.parametrize('planner_sort,saves,order', [
    (SORT, True, ['B2', 'A1']),   # keeps 주문일 오름차순 after "취소하지 말고": saved
    ([], False, None),            # drops the sort although the user said to keep it: blocked
])
def test_keep_sort_reply_end_to_end(service, planner_sort, saves, order):
    act, saved, fakes = service
    calls = []
    planner = make_planner(calls, sort=SORT)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    assert act('message', planner, content=REQUEST)['status'] == 'ready'
    planner = make_planner(calls, sort=planner_sort)
    changed = act('message', planner, content='정렬은 취소하지 말고 저장해줘')
    done = press_save(act, planner)
    if saves:
        assert done['status'] == 'saved' and saved() == 1
        result = fakes['run'](done['saved_agent_id'])
        assert result['status'] == 'succeeded' and csv_order(result) == order
    else:
        assert changed['status'] == 'validation_failed' and saved() == 0


DESC_REQUEST = REQUEST.replace('주문일 오름차순', '주문일 내림차순')


@pytest.mark.parametrize('planner_sort,saves,order', [
    (LATEST, True, ['A1', 'B2']),   # keeps 내림차순 after "정렬 기준은 주문일이야": saved
    (SORT, False, None),            # flips to 오름차순: blocked
])
def test_column_reconfirmation_keeps_the_direction_end_to_end(service, planner_sort, saves, order):
    act, saved, fakes = service
    CURRENT['request'] = DESC_REQUEST
    calls = []
    planner = make_planner(calls, sort=LATEST)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    assert act('message', planner, content=DESC_REQUEST)['status'] == 'ready'
    planner = make_planner(calls, sort=planner_sort)
    changed = act('message', planner, content='정렬 기준은 주문일이야')
    assert not changed['pending_questions']
    done = press_save(act, planner)
    if saves:
        assert changed['status'] == 'ready' and done['status'] == 'saved' and saved() == 1
        result = fakes['run'](done['saved_agent_id'])
        assert result['status'] == 'succeeded' and csv_order(result) == order
    else:
        assert changed['status'] == 'validation_failed' and saved() == 0


@pytest.mark.parametrize('bypass,saves', [(False, True), (True, False)])
def test_csv_must_come_from_the_sorted_table_end_to_end(service, bypass, saves):
    act, saved, fakes = service
    planner = make_planner([], sort=SORT, bypass=bypass)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    first = act('message', planner, content=REQUEST)
    done = press_save(act, planner)
    if saves:
        assert first['status'] == 'ready' and done['status'] == 'saved' and saved() == 1
        result = fakes['run'](done['saved_agent_id'])
        assert result['status'] == 'succeeded' and csv_order(result) == ['B2', 'A1']
    else:
        assert first['status'] == 'validation_failed' and any('정렬' in i for i in first['issues'])
        assert done['status'] == 'validation_failed' and saved() == 0


# --- PR #142 review 5 ------------------------------------------------------------------------

def test_a_typed_save_word_answering_an_open_question_is_not_intercepted():
    from types import SimpleNamespace
    from service.handlers.design import _waiting_for_answers
    asking = SimpleNamespace(tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={'kind': 'clarification'})])])
    review = SimpleNamespace(tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={'kind': 'review'})])])
    assert _waiting_for_answers({'pending_questions': [{'id': 'q', 'key': 'duplicate.field'}]}, asking)
    assert _waiting_for_answers({'draft': {'questions': ['어느 열이 기준인가요?']}}, asking)
    assert not _waiting_for_answers({'status': 'ready', 'draft': {'workflow': {}}}, review)


def test_negated_direction_is_not_the_requested_one():
    text = '주문번호 중복은 첫 행만 남기고 주문일 내림차순이 아니라 주문일 오름차순으로 정렬해줘'
    assert sort_request(table_state(text))['sorts'] == [{'field': '주문일', 'direction': 'ascending', 'value_type': None}]
    assert not sort_problems(text, SORT) and sort_problems(text, LATEST)
    assert sort_request(table_state('주문일 오름차순 아니고 주문일 내림차순으로 정렬'))['sorts'][0]['direction'] == 'descending'


NEGATED_REQUEST = REQUEST.replace('주문일 오름차순으로', '주문일 내림차순이 아니라 주문일 오름차순으로')


@pytest.mark.parametrize('planner_sort,saves', [(SORT, True), (LATEST, False)])
def test_negated_direction_end_to_end(service, planner_sort, saves):
    act, saved, fakes = service
    CURRENT['request'] = NEGATED_REQUEST
    planner = make_planner([], sort=planner_sort)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    first = act('message', planner, content=NEGATED_REQUEST)
    done = press_save(act, planner)
    if saves:
        assert first['status'] == 'ready' and done['status'] == 'saved'
        assert csv_order(fakes['run'](done['saved_agent_id'])) == ['B2', 'A1']
    else:
        assert first['status'] == 'validation_failed' and saved() == 0


def test_file_name_policy_change_keeps_the_original_quote_as_evidence():
    from agent_builder.conditions.condition_readiness import explicit_policies, source_catalog
    text = '중복은 첫 행만 남기고 결과.csv로 저장해줘'
    clauses = policy_clauses(text, COLUMNS)
    assert clauses[0][:2] == ('duplicate.policy', 'first') and '결과.csv' in clauses[0][2]
    state = conversation('중복은 마지막 행만 남겨줘', text)
    state['resources'] = [dict(TABLE, id='t')]
    catalog, _ = source_catalog(state)
    assert explicit_policies(state, catalog)['duplicate.policy']['value'] == 'first'


def test_sort_word_inside_a_quoted_value_is_not_a_sort_request():
    text = '상태가 "정렬 완료"인 행만 골라 JSON으로 저장해줘. 빈 값은 오류로 처리하고 중복은 유지해.'
    assert sort_request(table_state(text, COLUMNS + ['상태']))['kind'] == 'none'


def test_a_new_task_drops_the_previous_sort():
    state = conversation(REQUEST, '새로운 작업: 주문번호가 같은 행은 첫 행만 남기고 CSV로 저장')
    assert sort_request(state)['kind'] == 'none'
    assert sort_request(conversation(REQUEST, '첫 행만 남김'))['kind'] == 'sorts'


def ready_ledger(**extra):
    slot = lambda value: {'status': 'specified', 'value': value}
    slots = {'duplicate.field': slot('주문번호'), 'duplicate.policy': slot('first'),
             'missing.policy': slot('keep'), 'output.format': slot('csv')}
    slots.update({key: slot(value) for key, value in extra.items()})
    return {'decision': 'ready', 'operations': ['deduplicate'], 'slots': slots}


def sort_bounds(schema):
    sort = schema['$defs']['ExecutionPlan']['properties']['sort']
    item = schema['$defs'][sort['items']['$ref'].rsplit('/', 1)[1]]['properties']
    return sort.get('minItems', 0), sort.get('maxItems', 5), item


@pytest.mark.parametrize('version', ['v6', 'v7', 'v8'])
def test_direct_generator_schema_admits_the_requested_sort(monkeypatch, version):
    """Through the real Direct generator: the schema handed to the model (PR #142 review 5/6)."""
    from tests.design.test_direct_conversation import Fake, draft, planner, state as base_state
    monkeypatch.setenv('A4B_PLANNER_PROMPT', version)

    def schema_for(text):
        st = base_state()
        st['resources'] = [{'id': 'asset-1', 'name': '중복주문.csv', 'kind': 'table', 'columns': COLUMNS, 'profile': {}}]
        st['messages'] = [{'role': 'user', 'content': text}]
        st.update(condition_enforced=True, condition_ledger=ready_ledger())
        fake = Fake(draft(), draft(), draft())
        try:
            planner(monkeypatch, fake)(st)
        except ValueError:
            pass  # the canned graph draft does not fit v8 linear steps; only the schema matters
        return fake.schema

    low, high, item = sort_bounds(schema_for(REQUEST))
    assert low == 0 and high == 1 and item['field']['enum'] == ['주문일'] and item['direction']['enum'] == ['ascending']
    low, high, item = sort_bounds(schema_for('주문번호 중복은 첫 행만 남기고 최신순으로 정렬해서 CSV로 저장해줘'))
    assert low <= high and high >= 1 and item['direction']['enum'] == ['descending']
    no_sort = schema_for('주문번호 중복은 첫 행만 남기고 CSV로 저장해줘')
    assert no_sort['$defs']['ExecutionPlan']['properties']['sort']['maxItems'] == 0


def test_linear_schema_admits_the_requested_sort():
    from agent_builder.design.nodes.direct_conversation import decoder_schema
    from agent_builder.design.linear_plan import schema_for
    st = table_state(REQUEST)
    schema = schema_for(decoder_schema('v8'), {'condition_enforced': True, 'condition_ledger': ready_ledger(),
                                               'resources': st['resources'], 'messages': st['messages']})
    low, high, item = sort_bounds(schema)
    assert low == 0 and high == 1 and item['field']['enum'] == ['주문일']


# --- PR #142 review 6 ------------------------------------------------------------------------

def test_quoted_sort_operand_is_kept():
    assert sort_request(table_state('"주문일 오름차순"으로 정렬해줘'))['sorts'] == [
        {'field': '주문일', 'direction': 'ascending', 'value_type': None}]
    assert sort_request(table_state('상태가 "정렬 완료"인 행만 골라줘', COLUMNS + ['상태']))['kind'] == 'none'


def test_cancelling_a_written_sort():
    assert sort_request(conversation(REQUEST, '주문일 오름차순 정렬 빼줘'))['kind'] == 'cancelled'
    assert sort_request(conversation(REQUEST, '주문일 오름차순으로 정렬하지 마'))['kind'] == 'cancelled'


def test_sort_inside_an_unquoted_value_is_not_wording():
    assert sort_request(table_state('상태가 정렬완료와 같은 행만 골라 JSON으로 저장해줘', COLUMNS + ['상태']))['kind'] == 'none'
    assert sort_request(table_state('주문일 기준으로 정렬해줘'))['kind'] == 'unparsed'


def test_file_name_after_a_period_keeps_the_policy_sentence():
    from agent_builder.conditions.condition_readiness import explicit_policies, source_catalog
    text = '중복은 첫 행만 남겨줘.결과.csv도 첨부했어.'
    assert policy_clauses(text, COLUMNS)[0][:2] == ('duplicate.policy', 'first')
    state = conversation('중복은 마지막 행만 남겨줘', text)
    state['resources'] = [dict(TABLE, id='t')]
    catalog, _ = source_catalog(state)
    assert explicit_policies(state, catalog)['duplicate.policy']['value'] == 'first'


def test_an_old_no_sort_policy_does_not_override_a_new_sort_request():
    state = conversation('주문번호 중복은 첫 행만 남기고 정렬은 하지 마',
                         '새로운 작업: 주문번호 중복은 첫 행만 남기고 주문일 오름차순으로 정렬해줘')
    ledger = ready_dedup_ledger()
    ledger['slots']['sort.policy'] = {'key': 'sort.policy', 'status': 'specified', 'value': 'none', 'evidence': []}
    problems = lambda sort: [p for p in plan_issues(ledger, dedup_draft(sort), state=state) if '정렬' in p]
    assert not problems(SORT) and problems([])
    same_task = conversation('주문번호 중복은 첫 행만 남기고 정렬은 하지 마', '역시 주문일 오름차순으로 정렬해줘')
    assert not [p for p in plan_issues(ledger, dedup_draft(SORT), state=same_task) if '정렬' in p]


def test_question_marked_save_request_is_a_save_request():
    assert is_save_request('저장해도 돼요?') and is_save_request('확인완료. 저장?')


SORT_FIRST_ROWS = [{'주문번호': 'A1', '고객명': '김', '주문일': '2026-11-01', '금액': 100},
                   {'주문번호': 'A1', '고객명': '김', '주문일': '2026-11-07', '금액': 100},
                   {'주문번호': 'B2', '고객명': '이', '주문일': '2026-11-04', '금액': 200}]
SORT_FIRST_REQUEST = ('중복주문.csv를 주문일 내림차순으로 먼저 정렬한 뒤 주문번호가 같은 행은 첫 행만 남기고 CSV로 저장해줘. '
                      '빈 값은 그대로 유지해.')


@pytest.mark.parametrize('sort_first,saves', [(True, True), (False, False)])
def test_sort_before_dedup_keeps_the_latest_row_end_to_end(service, sort_first, saves):
    act, saved, fakes = service
    CURRENT['request'] = SORT_FIRST_REQUEST
    planner = make_planner([], sort=LATEST, sort_first=sort_first)
    act('upload', planner, asset=dict(TABLE), rows=SORT_FIRST_ROWS)
    first = act('message', planner, content=SORT_FIRST_REQUEST)
    done = press_save(act, planner)
    if saves:
        assert first['status'] == 'ready' and done['status'] == 'saved', first['issues']
        result = fakes['run'](done['saved_agent_id'])
        import csv as _csv
        rows = list(_csv.DictReader(io.StringIO(result['artifact']['content'].lstrip('\ufeff'))))
        assert [(r['주문번호'], r['주문일']) for r in rows] == [('A1', '2026-11-07'), ('B2', '2026-11-04')]
    else:
        assert first['status'] == 'validation_failed' and any('정렬한 뒤 중복' in i for i in first['issues'])
        assert saved() == 0


def test_final_resort_cannot_fix_a_wrong_sort_before_dedup(service):
    act, saved, _ = service
    CURRENT['request'] = SORT_FIRST_REQUEST
    make_split_draft = make_planner([], sort=LATEST, sort_first=True)

    def planner(state):
        result = make_split_draft(state)
        workflow = result['draft']['workflow']
        _, first_sort, dedup, writer = workflow['nodes']
        correct_sort = first_sort['settings']['execution_plan']
        wrong_sort = {**correct_sort, 'sort': SORT}
        first_sort['settings'].update(execution_plan=wrong_sort, instructions=json.dumps(wrong_sort))
        final_sort = node('final_sort', 'filter_sort', [{'input': 'table', 'node_id': dedup['id'], 'output': 'table'}],
                          execution_plan=correct_sort, instructions=json.dumps(correct_sort))
        workflow['nodes'].insert(-1, final_sort)
        writer['bindings'][0]['node_id'] = final_sort['id']
        workflow['edges'][-1]['target'] = final_sort['id']
        workflow['edges'].append({'source': final_sort['id'], 'target': writer['id'], 'branch': 'next'})
        return result

    act('upload', planner, asset=dict(TABLE), rows=SORT_FIRST_ROWS)
    result = act('message', planner, content=SORT_FIRST_REQUEST)
    assert result['status'] == 'validation_failed'
    assert any('정렬한 뒤 중복' in issue for issue in result['issues'])
    press_save(act, planner)
    assert saved() == 0
