"""Reported 2026-11-17: '중복주문.csv' dedup + sort request asked again, then never saved.

Real service entry + LangGraph + SQLite; capability, condition and planner replies are
fixtures, so these check routing/state contracts, not the production model.
"""
import json
import re
import tempfile

import pytest

from agent_builder.conditions.condition_readiness import plan_issues, policy_clauses
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
    return {'questions': [], 'unsupported': [], 'execution_plan': plan, 'workflow': {'entry': 'read', 'nodes': nodes, 'edges': []}}


def sort_problems(text, sort):
    state = {'messages': [{'role': 'user', 'content': text}], 'resources': []}
    return [p for p in plan_issues(ready_dedup_ledger(), dedup_draft(sort), state=state) if '정렬' in p]


def test_written_sort_key_and_direction_are_allowed():
    assert sort_problems(REQUEST, SORT) == []
    assert sort_problems('주문번호 중복은 첫 행만 남기고 금액 숫자 내림차순으로 정렬해줘.',
                         [{'field': '금액', 'direction': 'descending', 'value_type': 'number'}]) == []


@pytest.mark.parametrize('text,sort', [
    ('주문번호 중복은 첫 행만 남겨줘.', SORT),                                  # no sort requested
    (REQUEST, [{'field': '주문일', 'direction': 'descending', 'value_type': 'text'}]),  # opposite direction
    (REQUEST, [{'field': '금액', 'direction': 'ascending', 'value_type': 'number'}]),   # another column
    (REQUEST, SORT + [{'field': '금액', 'direction': 'ascending', 'value_type': 'number'}]),
])
def test_unrequested_or_changed_sort_is_still_rejected(text, sort):
    assert sort_problems(text, sort)


def test_dedup_contract_still_rejects_an_added_filter():
    state = {'messages': [{'role': 'user', 'content': REQUEST}], 'resources': []}
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


def capability(messages):
    return dict(decision='existing_tools', tool_ids=['read_table', 'filter_sort', 'write_result'],
                missing_conditions=[], missing_actions=[], reason='fixture', user_quotes=[REQUEST])


def condition(messages):
    ids = sorted(set(re.findall(r'user:\d+:\d+', json.dumps([m.get('content') for m in messages], ensure_ascii=False))))
    values = {'duplicate.field': '주문번호', 'duplicate.policy': 'first', 'missing.policy': 'keep', 'output.format': 'csv'}
    return {'operations': ['deduplicate'], 'unsupported_actions': [],
            'conditions': [{'key': k, 'value': v, 'status': 'specified', 'evidence_ids': ids[-1:]} for k, v in values.items()]}


def node(id, tool, bindings=(), **settings):
    base = dict(file_ids=[], query=None, fields=[], instructions=None, format=None, question=None,
                condition_field=None, condition_operator=None)
    return {'id': id, 'tool': tool, 'label': id, 'bindings': list(bindings), 'settings': {**base, **settings}}


def make_planner(calls, sort=SORT, schedule=None):
    def planner(state):
        calls.append(state['messages'][-1]['content'])
        plan = ExecutionPlan(filters=[], sort=sort, match='all', missing_policy='keep', duplicate_policy='first',
                             duplicate_field='주문번호', output_format='csv').model_dump()
        file_id = state['resources'][0]['id']
        nodes = [node('read', 'read_table', file_ids=[file_id]),
                 node('sort', 'filter_sort', [{'input': 'table', 'node_id': 'read', 'output': 'table'}],
                      execution_plan=plan, instructions=json.dumps(plan, ensure_ascii=False)),
                 node('out', 'write_result', [{'input': 'table', 'node_id': 'sort', 'output': 'table'}], format='csv')]
        edges = [{'source': 'read', 'target': 'sort', 'branch': 'next'}, {'source': 'sort', 'target': 'out', 'branch': 'next'}]
        draft = AgentDraft(name='중복 주문 정리', purpose='중복 제거 후 정렬', instructions='요청대로 처리',
                           tool_ids=['read_table', 'filter_sort', 'write_result'], document_ids=[file_id],
                           requirements=[{'description': '요청', 'source_id': 'user', 'quote': REQUEST}],
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
    return act, saved, fakes


def test_reported_request_is_ready_without_a_question_and_typed_save_saves(service):
    act, saved, fakes = service
    calls = []
    planner = make_planner(calls)
    act('upload', planner, asset=dict(TABLE), rows=ROWS)
    first = act('message', planner, content=REQUEST)
    assert first['status'] == 'ready' and not first['pending_questions'] and not first['issues']
    assert len(calls) == 1
    done = act('message', planner, content='확인완료. 저장')
    assert done['status'] == 'saved' and done['saved_agent_id'] and saved() == 1
    assert len(calls) == 1 and fakes['cap'].calls == 1  # nothing re-planned for the save request
    shown = [m['content'] for m in done['messages'] if m['role'] == 'user']
    assert shown[-1] == '확인완료. 저장'


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
