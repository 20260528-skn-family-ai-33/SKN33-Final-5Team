"""Orchestration stage 1: the single requirement state is a faithful, read-only projection."""
import json

import pytest

from agent_builder.conditions.condition_readiness import judge_conditions, source_catalog
from agent_builder.orchestration.state import project_state, state_issues, shadow_view, digest, Condition, Evidence
from tests.conditions.test_condition_readiness import state as table_state, Fake as ConditionFake

TABLE = [{'id': 't', 'name': '매출.csv', 'kind': 'table', 'format': 'csv', 'columns': ['비용', '지역'],
          'profile': {'비용': {'types': ['number']}}, 'rows': 3}]
SCHEDULED = '매일 오전 9시 한국 시간에 비용이 100만원 초과인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'


def table_ledger(*follow):
    s = table_state(SCHEDULED); s['resources'] = TABLE
    for text in follow:
        s['messages'].append({'role': 'user', 'content': text})
    catalog, _ = source_catalog(s)
    source = next(e['id'] for e in catalog if e['source_id'] == 'user' and e.get('turn') == 0)
    values = {'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000',
              'missing.policy': 'error', 'duplicate.policy': 'keep', 'output.format': 'json'}
    proposal = {'operations': ['filter', 'schedule'], 'unsupported_actions': [],
                'conditions': [{'key': k, 'value': v, 'status': 'specified', 'evidence_ids': [source]} for k, v in values.items()]}
    s['condition_ledger'] = judge_conditions(s, ConditionFake([proposal]))['ledger']
    return s


def test_table_ledger_projects_to_grounded_conditions():
    s = table_ledger()
    value = project_state(s)
    keys = {c.key: c for c in value.conditions}
    assert keys['filter.value'].value == 1000000 and keys['filter.value'].status == 'specified'
    assert keys['schedule.hour'].value == 9
    # schedule.timezone is the application's fixed setting and carries no user evidence.
    assert all(c.evidence and c.evidence[0].turn == 0 for c in value.conditions
               if c.status == 'specified' and c.key != 'schedule.timezone')
    assert value.tasks == [] and state_issues(value, s) == []


def test_schedule_removal_is_an_edit_and_its_slots_are_gone():
    s = table_ledger('예약 없이 한 번만 실행할게요')
    value = project_state(s)
    assert [(e.kind, e.target, e.turn) for e in value.edits] == [('remove', 'schedule', 1)]
    assert not any(c.key.startswith('schedule.') for c in value.conditions)
    assert state_issues(value, s) == []


def test_requirement_tasks_map_their_user_message_to_the_message_index():
    s = {'messages': [{'role': 'user', 'content': '계약서를 검토해줘'}, {'role': 'assistant', 'content': '질문'},
                      {'role': 'user', 'content': '위험 조항만 요약해줘'}],
         'requirement_ledger': {'tasks': [{'id': 'r1', 'source_message': 1, 'description': '위험 조항 요약', 'quote': '위험 조항만 요약해줘',
                                           'depends_on': [], 'spec': {'kind': 'document_analysis'}}], 'unresolved': []}}
    value = project_state(s)
    assert [(t.id, t.kind, t.evidence[0].turn) for t in value.tasks] == [('r1', 'document_analysis', 2)]
    assert state_issues(value, s) == []


def test_capability_only_request_becomes_one_task():
    s = {'messages': [{'role': 'user', 'content': '계약서 해지 조항을 요약해줘'}],
         'capability_assessment': {'decision': 'existing_tools', 'tool_ids': ['read_document', 'compose_answer'],
                                   'missing_conditions': [], 'missing_actions': [], 'user_quotes': ['계약서 해지 조항을 요약해줘']}}
    value = project_state(s)
    assert [(t.id, t.tool_ids) for t in value.tasks] == [('request', ['read_document', 'compose_answer'])]
    assert value.capability['decision'] == 'existing_tools'


def test_file_inputs_keep_per_file_timing():
    s = {'messages': [{'role': 'user', 'content': '방금 첨부한 계약서 PDF를 분석해줘. 비교용 견적서는 실행할 때 첨부할게요.'}], 'resources': []}
    value = project_state(s)
    assert {(i.subject, i.timing) for i in value.inputs} == {('견적서', 'execution'), ('계약서pdf', 'now')}
    assert state_issues(value, s) == []


def test_pending_requirement_question_is_a_missing_condition_and_an_open_question():
    q = {'id': 'q1', 'key': 'requirement.abc', 'source': 'requirement', 'text': '위험 판단 기준을 알려주세요.', 'required': True}
    s = {'messages': [{'role': 'user', 'content': '계약서를 검토해줘'}], 'pending_questions': [q, dict(q)]}
    value = project_state(s)
    assert [(c.key, c.status) for c in value.conditions] == [('requirement.abc', 'missing')]
    assert [x.id for x in value.questions] == ['q1']  # duplicates are not projected twice
    assert state_issues(value, s) == []


def test_invariants_detect_ungrounded_duplicate_and_tampered_state():
    s = table_ledger()
    value = project_state(s)
    forged = value.model_copy(update={'conditions': value.conditions + [
        Condition(key='filter.value', value=1, status='specified', evidence=[Evidence(turn=0, quote='원문에 없는 문장')])]})
    issues = state_issues(forged, s)
    assert any('중복' in i for i in issues) and any('근거' in i for i in issues) and any('버전' in i for i in issues)


def test_version_is_stable_and_changes_with_content():
    s = table_ledger()
    assert project_state(s).version == project_state(s).version == digest(project_state(s))
    assert project_state(table_ledger('예약 없이 한 번만 실행할게요')).version != project_state(s).version


def test_old_checkpoint_without_new_fields_projects_without_error():
    view = shadow_view({'messages': [{'role': 'user', 'content': '안녕'}]})
    assert view['issues'] == [] and view['tasks'] == [] and view['format'] == 'a4b.requirement_state.v1'


@pytest.fixture
def run_flow(tmp_path, monkeypatch):
    def run(mode):
        if mode is None:
            monkeypatch.delenv('A4B_ORCHESTRATION_MODE', raising=False)
        else:
            monkeypatch.setenv('A4B_ORCHESTRATION_MODE', mode)
        import service.handlers.design as worker
        from service.worker import handle
        from infrastructure.persistence.ids import uid
        from tests.design.test_clarification_turns import Fake, assessment, document_planner, ORIGINAL, RISK_Q
        original = worker.build_graph
        cap = Fake([assessment(ORIGINAL, [RISK_Q])])
        monkeypatch.setattr(worker, 'build_graph', lambda *a, **k: original(*a, **{**k, 'capability_mode': 'enforce',
                            'capability_transport': cap, 'condition_mode': 'off', 'verifier_mode': 'off'}))
        folder = tmp_path / (mode or 'default'); folder.mkdir()
        s = handle({'directory': str(folder), 'action': 'new_session', 'model': 't', 'credential_id': 'c'})
        result = handle({'directory': str(folder), 'id': s['id'], 'revision': s['revision'], 'action': 'message', 'content': ORIGINAL},
                        conversation=document_planner([], ORIGINAL))
        monkeypatch.setattr(worker, 'build_graph', original)
        return result
    return run


def test_default_off_response_is_unchanged_and_shadow_only_adds_the_projection(run_flow):
    off = run_flow(None)
    shadow = run_flow('shadow')
    assert 'requirement_state' not in off
    view = shadow['requirement_state']
    assert view['issues'] == [] and [q['text'] for q in view['questions']] == ['어떤 위험 기준을 적용할까요?']
    ignore = {'requirement_state', 'id', 'created_at', 'generation_request_id', 'last_generation_request_id', 'trace'}
    strip = lambda r: json.loads(json.dumps({k: v for k, v in r.items() if k not in ignore}, default=str))
    a, b = strip(off), strip(shadow)
    for key in ('pending_questions', 'draft'):
        for item in (a.get(key) or []) if isinstance(a.get(key), list) else []:
            item.pop('id', None)
        for item in (b.get(key) or []) if isinstance(b.get(key), list) else []:
            item.pop('id', None)
    assert a == b


def test_unknown_mode_is_rejected(monkeypatch):
    from service.handlers.design import _requirement_state
    monkeypatch.setenv('A4B_ORCHESTRATION_MODE', 'on')
    with pytest.raises(ValueError):
        _requirement_state({})


@pytest.mark.parametrize('text', ['실행할 때 PDF 계약서를 넣을 테니 해지 조항을 요약해줘', '실행할 때 계약서 PDF를 넣을 테니 해지 조항을 요약해줘'])
def test_one_file_named_in_either_order_is_one_input(text):
    value = project_state({'messages': [{'role': 'user', 'content': text}], 'resources': []})
    assert [(i.subject, i.timing) for i in value.inputs] == [('계약서pdf', 'execution')]
