"""Recovery controls must recover the persisted graph, not become business input."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.failure_recovery import normalize_active_failures, project_recovery_questions
from tests.design.test_clarification_core import service  # real service/SQLite fixture


@pytest.mark.parametrize('key', ['system_failure', 'requirement_failure'])
def test_old_timeout_gets_a_safe_diagnosis_and_keeps_its_retry(key):
    state = {'status': 'validation_failed', key: {
        'stage': 'capability_check', 'error_type': 'APITimeoutError', 'attempt': 1,
        'retryable': True, 'message': 'old failure'}}
    before = deepcopy(state)
    failure = normalize_active_failures(state)[key]
    assert failure['retryable'] and failure['reason_code'] == 'model_timeout'
    assert failure['reason'] and failure['action']
    assert state == before


def test_old_unknown_failure_does_not_invent_a_cause_or_remove_explicit_retry_permission():
    failure = normalize_active_failures({'status': 'validation_failed', 'system_failure': {
        'retryable': True, 'error_type': 'UnknownOldError'}})['system_failure']
    assert failure['retryable'] and failure['reason_code'] == 'processing_error'
    assert '원인을 확인하지 못했습니다' in failure['reason']


def test_known_contract_failure_keeps_its_prose_and_does_not_offer_an_ineffective_retry():
    record = {'failure_kind': 'model_output_error', 'error_type': 'ValueError', 'retryable': True,
              'reason': '기록된 원인', 'action': '관리자 확인', 'attempt': 1}
    failure = normalize_active_failures({'status': 'validation_failed', 'requirement_failure': record})['requirement_failure']
    assert not failure['retryable']
    assert failure['reason'] == record['reason'] and failure['action'] == record['action']


def test_old_repeated_failure_moves_the_exact_repeat_prefix_to_its_own_field():
    action = '같은 조건의 2번째 시도도 실패했습니다. 관리자에게 실패 단계와 이 안내를 전달해주세요.'
    failure = normalize_active_failures({'status': 'validation_failed', 'system_failure': {
        'reason': '기록된 원인', 'action': action, 'attempt': 2, 'retryable': True}})['system_failure']
    assert failure['action'] == '관리자에게 실패 단계와 이 안내를 전달해주세요.'
    assert failure['attempt_notice'].count('2번째 시도도 실패') == 1
    assert not failure['retryable']


def test_metadata_from_a_previous_failure_is_not_activated_during_a_real_question():
    state = {'status': 'needs_clarification', 'system_failure': {'retryable': True, 'error_type': 'TimeoutError'},
             'draft': {'questions': ['기준값은 얼마인가요?']}}
    assert normalize_active_failures(state) == state
    state['requirement_failure'] = {'kind': 'execution_mode_questions', 'retryable': True}
    assert project_recovery_questions(state)['requirement_failure'] is None


@pytest.mark.parametrize('status', ['ready', 'saved', 'needs_clarification'])
def test_old_translated_question_does_not_reactivate_a_failure_after_its_answer(status):
    from agent_builder.design.failure_recovery import projected_recovery_failure
    state = {'status': status, 'messages': [
        {'role': 'user', 'content': '파일로 저장해줘'},
        {'role': 'assistant', 'content': '어떤 文件 형식으로 만들까요?'},
        {'role': 'user', 'content': 'CSV로 해줘'}],
        'draft': {'questions': ['선별할 기준값은 얼마인가요?'] if status == 'needs_clarification' else []},
        'pending_questions': []}
    assert project_recovery_questions(state).get('requirement_failure') is None
    assert projected_recovery_failure(state) is None


@pytest.mark.parametrize('key', ['system_failure', 'requirement_failure'])
def test_a_stale_question_cannot_replace_the_current_failure_diagnosis(key):
    from agent_builder.design.failure_recovery import projected_recovery_failure
    failure = {'kind': 'processing_error', 'reason': '기록된 원인', 'action': '관리자 확인', 'retryable': False}
    state = {'status': 'validation_failed', key: failure,
             'draft': {'questions': ['어떤 文件 형식으로 만들까요?']},
             'messages': [{'role': 'user', 'content': '파일로 저장해줘'},
                          {'role': 'assistant', 'content': '어떤 文件 형식으로 만들까요?'}]}
    shown = project_recovery_questions(state)
    assert shown[key] == failure
    assert projected_recovery_failure(state) is None
    assert '다시 시도' not in shown['messages'][-1]['content']


def test_a_changed_request_shows_its_new_failed_stage_instead_of_the_previous_failure(service):
    from tests.design.test_clarification_core import REQUEST, analysis_planner, RequirementTransport
    from tests.design.test_clarification_turns import Fake, assessment
    box, act, saved = service
    box['cap'] = Fake([assessment(REQUEST)])
    box['requirement'] = RequirementTransport([{}, {}])
    calls = []
    failed = act('message', analysis_planner(calls, REQUEST), content=REQUEST)
    assert failed['requirement_failure']['stage'] == 'requirements'

    class Unavailable:
        def invoke(self, messages):
            raise TimeoutError('fixture')

    box['cap'] = Unavailable()
    changed = REQUEST + '. 위약금 조항을 우선 확인해줘'
    second = act('message', analysis_planner(calls, changed), content=changed)
    assert second['status'] == 'validation_failed'
    assert second['requirement_failure'] is None
    assert second['system_failure']['stage'] == 'capability_check'
    assert second['system_failure']['reason_code'] == 'model_timeout'
    assert second['system_failure']['attempt'] == 1
    assert [m['content'] for m in second['messages'] if m['role'] == 'user'] == [REQUEST, changed]
    assert calls == [] and saved() == 0


@pytest.mark.parametrize('retry_control', [True, False], ids=['retry-control', 'same-request'])
def test_upload_after_a_processing_failure_keeps_recovery_and_the_answered_request(service, tmp_path, retry_control):
    from agent_builder.design.graph import build_graph
    from infrastructure.persistence.storage import Storage
    from service.worker import handle
    from tests.design.test_attachment_is_not_a_request import DOC
    from tests.design.test_clarification_core import REQUEST, RequirementTransport, analysis_planner, extraction
    from tests.design.test_clarification_turns import Fake, assessment

    box, act, saved = service
    box['cap'] = Fake([assessment(REQUEST)] * 4)

    class QuestionThenTimeout:
        schema = None
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return {'content': json.dumps(extraction('위험 판단 기준을 알려주세요.'), ensure_ascii=False)}
            raise TimeoutError('private_model_response Bearer private_token')

    transport = QuestionThenTimeout()
    box['requirement'] = transport
    calls = []
    first = act('message', analysis_planner(calls, REQUEST), content=REQUEST)
    answer = '위약금이 있는 조항을 위험으로 봐줘'
    failed = act('message', analysis_planner(calls, REQUEST), content=answer,
                 question_id=first['pending_questions'][0]['id'])
    assert failed['status'] == 'validation_failed'
    assert failed['requirement_failure']['reason_code'] == 'model_timeout'
    assert failed['requirement_failure']['retryable'] and calls == []
    assert 'private_' not in json.dumps(failed)
    history = handle({'directory': str(tmp_path), 'action': 'generation_history', 'id': first['id']})
    assert 'private_' not in json.dumps(history)  # The model observer also sees the private exception.

    def persisted():
        storage = Storage(tmp_path)
        graph, connection = build_graph(storage, conversation=lambda _: {})
        try:
            return deepcopy(graph.get_state({'configurable': {'thread_id': first['id']}}).values)
        finally:
            connection.close()
            storage.close()

    before = persisted()
    assert len(before['requirement_answers']) == len(before['clarification_answers']) == 1
    uploaded = act('upload', asset=dict(DOC), rows=[])
    assert transport.calls == 2 and calls == []
    assert uploaded['status'] == 'needs_clarification'
    assert uploaded['requirement_failure']['reason_code'] == 'model_timeout'
    assert uploaded['requirement_failure']['retryable']
    assert uploaded['requirement_failure']['recovery_pending'] is True
    assert not uploaded['pending_questions'] and not uploaded['draft']['questions']
    after_upload = persisted()
    shown = act('session')
    assert shown['requirement_failure'] == uploaded['requirement_failure']
    assert persisted() == after_upload  # Reading the recovery view never edits a checkpoint.

    box['requirement'] = RequirementTransport([extraction()])
    incoming = []
    planner = analysis_planner(calls, REQUEST)

    def capture(state):
        incoming.append(deepcopy(state))
        return planner(state)

    retry = '앞서 전달한 조건을 유지하고 업무 흐름 생성을 다시 시도해 주세요.' if retry_control else answer
    recovered = act('message', capture, content=retry)
    assert recovered['status'] == 'ready' and recovered['requirement_failure'] is None
    assert len(incoming) == 1 and saved() == 0
    assert [m['content'] for m in incoming[0]['messages'] if m['role'] == 'user'] == [REQUEST, answer]
    for key in ('condition_answers', 'requirement_answers', 'clarification_answers'):
        assert incoming[0].get(key) == before.get(key)
    assert [m['content'] for m in recovered['messages'] if m['role'] == 'user'] == [REQUEST, answer]
    assert 'private_' not in json.dumps(recovered)


def _interrupted_session(tmp_path, monkeypatch, question, response):
    from agent_builder.design.graph import build_graph
    from agent_builder.design.temporal_context import ensure_temporal_context
    from infrastructure.persistence.storage import Storage
    from service.worker import handle
    from tests.design.test_builder import initial
    from tests.design.test_runtime_file_questions import question_draft

    for name in ('CAPABILITY', 'CONDITION', 'REQUIREMENT', 'VERIFIER'):
        monkeypatch.setenv(f'A4B_{name}_MODE', 'off')
    session = handle({'directory': str(tmp_path), 'action': 'new_session', 'model': 'm',
                      'credential_id': 'local', 'execution_mode': 'manual'})
    storage = Storage(tmp_path)
    graph, connection = build_graph(storage, conversation=lambda _: {})
    config = {'configurable': {'thread_id': session['id']}}
    draft = question_draft(question)
    draft['question_responses'] = [response]
    state = {**initial(), 'session_id': session['id'], 'execution_mode': 'manual',
             'model': 'm', 'credential_id': 'local',
             'messages': [{'role': 'user', 'content': '매출이 높은 주문만 정리해줘'},
                          {'role': 'assistant', 'content': '선별할 기준 값은 얼마인가요?'},
                          {'role': 'user', 'content': '100만원 이상으로 해줘'}],
             'condition_answers': [],
             'requirement_answers': [{'turn': 2, 'answer': '100만원 이상으로 해줘', 'questions': [],
                                      'question_ids': [], 'candidate_keys': []}],
             'clarification_answers': [],
             'draft': draft, 'status': 'needs_clarification', 'route': 'await_input',
             'issues': [], 'pending_questions': [], 'system_failure': None, 'requirement_failure': None}
    state.update(ensure_temporal_context(state))
    try:
        # Install an actual old await_input interrupt, including its original
        # question payload, rather than merely fabricating a public response.
        graph.update_state(config, state, as_node='supervisor')
        graph.invoke(None, config)
        before = graph.get_state(config)
        assert any(task.interrupts for task in before.tasks)
        return session, deepcopy(before.values)
    finally:
        connection.close()
        storage.close()


@pytest.mark.parametrize('key', ['system_failure', 'requirement_failure'])
def test_public_legacy_timeout_keeps_its_known_cause_when_old_generic_chat_is_sanitized(tmp_path, monkeypatch, key):
    from agent_builder.design.graph import build_graph
    from infrastructure.persistence.storage import Storage
    from service.worker import handle
    session, before = _interrupted_session(tmp_path, monkeypatch, '실행 시각은 몇 시인가요?', {'type': 'time'})
    storage = Storage(tmp_path)
    graph, connection = build_graph(storage, conversation=lambda _: {})
    try:
        graph.update_state({'configurable': {'thread_id': session['id']}}, {
            'status': 'validation_failed', 'route': 'invalid',
            key: {'error_type': 'APITimeoutError', 'retryable': True, 'stage': 'requirements', 'attempt': 1},
            'messages': before['messages'] + [{'role': 'assistant',
                'content': '입력하신 조건은 보존했습니다. 현재 이 요청을 처리할 수 없습니다.'}]
        }, as_node='supervisor')
    finally:
        connection.close()
        storage.close()
    shown = handle({'directory': str(tmp_path), 'action': 'session', 'id': session['id']})
    failure = shown[key]
    assert failure['reason_code'] == 'model_timeout' and failure['retryable']
    assert '응답 시간' in failure['reason'] and '작업 간 연결' not in failure['reason']
    if key == 'requirement_failure':
        assert failure['reason'] in shown['messages'][-1]['content']


@pytest.mark.parametrize('metadata,code,retryable', [
    ({}, 'invalid_model_response', False),
    ({'error_type': 'APITimeoutError'}, 'model_timeout', True),
])
def test_public_legacy_requirement_failure_uses_current_trace_without_overriding_known_transport_cause(
        tmp_path, monkeypatch, metadata, code, retryable):
    from agent_builder.design.graph import build_graph
    from infrastructure.persistence.storage import Storage
    from service.worker import handle

    session, original = _interrupted_session(tmp_path, monkeypatch, '실행 시각은 몇 시인가요?', {'type': 'time'})
    config = {'configurable': {'thread_id': session['id']}}
    storage = Storage(tmp_path)
    graph, connection = build_graph(storage, conversation=lambda _: {})
    try:
        graph.update_state(config, {
            'status': 'validation_failed', 'route': 'invalid',
            'requirement_failure': {'kind': 'processing_error', 'stage': 'requirements',
                                    'retryable': True, 'attempt': 1, **metadata},
            'messages': original['messages'] + [{'role': 'assistant', 'content':
                '입력하신 조건은 보존했습니다. 현재 이 요청을 처리할 수 없습니다.'}],
            'issues': ['요청 업무 목록을 확정하지 못했습니다. private_model_response Bearer private_token'],
            'trace': [{'node': 'requirements', 'status': 'failed', 'failure_kind': 'model_output_error',
                       'error_type': 'ValueError', 'detail':
                       '요청 업무 목록을 확정하지 못했습니다. private_model_response Bearer private_token'},
                      {'node': 'requirements', 'label': '단계 소요 시간', 'elapsed_ms': 1000}],
        }, as_node='supervisor')
        before = deepcopy(graph.get_state(config))
    finally:
        connection.close()
        storage.close()

    shown = handle({'directory': str(tmp_path), 'action': 'session', 'id': session['id']})
    failure = shown['requirement_failure']
    assert failure['reason_code'] == code and failure['retryable'] is retryable
    assert failure['reason'] in shown['messages'][-1]['content']
    assert failure['action'] in shown['issues'][0]
    assert 'private_' not in json.dumps(shown)
    storage = Storage(tmp_path)
    graph, connection = build_graph(storage, conversation=lambda _: {})
    try:
        after = graph.get_state(config)
        assert after.values == before.values
        assert after.config == before.config and after.next == before.next
    finally:
        connection.close()
        storage.close()


@pytest.mark.parametrize('question,response,kind', [
    ('실행 시각은 몇 시인가요?', {'type': 'time'}, 'execution_mode_questions'),
    ('选择筛选条件是什么?', {'type': 'text'}, 'question_language_error'),
])
def test_public_recovery_button_restarts_the_interrupted_request_without_adding_a_turn(
        tmp_path, monkeypatch, question, response, kind):
    from agent_builder.capabilities.capability_gate import input_fingerprint
    from agent_builder.design.graph import build_graph
    from infrastructure.persistence.storage import Storage
    from service.worker import handle
    from tests.design.test_runtime_file_questions import question_draft

    session, before = _interrupted_session(tmp_path, monkeypatch, question, response)
    shown = handle({'directory': str(tmp_path), 'action': 'session', 'id': session['id']})
    assert shown['requirement_failure']['kind'] == kind and shown['requirement_failure']['retryable']
    assert not shown['pending_questions'] and not shown['draft']['questions']
    assert before.get('requirement_failure') is None  # projection is read-only
    calls = []

    def planner(state):
        calls.append(deepcopy(state))
        return {'draft': question_draft('결과 파일 형식을 선택해 주세요. (CSV, JSON)'), 'confirmed': False}

    retry = '앞서 전달한 조건을 유지하고 업무 흐름 생성을 다시 시도해 주세요.'
    recovered = handle({'directory': str(tmp_path), 'action': 'message', 'id': session['id'],
                        'revision': shown['revision'], 'content': retry,
                        'generation_request_id': '00000000-0000-4000-8000-000000000081'}, conversation=planner)
    assert len(calls) == 1
    assert [m for m in calls[0]['messages'] if m['role'] == 'user'] == [
        m for m in before['messages'] if m['role'] == 'user']
    assert input_fingerprint(calls[0]) == input_fingerprint(before)
    for key in ('condition_answers', 'requirement_answers', 'clarification_answers'):
        assert calls[0].get(key) == before.get(key)
    assert recovered['draft']['questions'] and recovered.get('requirement_failure') is None
    assert all(message['content'] != retry for message in recovered['messages'])
    storage = Storage(tmp_path)
    graph, connection = build_graph(storage, conversation=lambda _: {})
    try:
        after = graph.get_state({'configurable': {'thread_id': session['id']}})
        assert any(task.interrupts for task in after.tasks)
        assert not after.values.get('requirement_failure')
        assert not storage.agents()  # recovery still requires validation and approval
    finally:
        connection.close()
        storage.close()
