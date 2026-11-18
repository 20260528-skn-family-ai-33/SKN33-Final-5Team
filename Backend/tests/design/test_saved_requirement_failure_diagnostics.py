"""Saved failures retain useful diagnoses without reviving history or leaking inputs."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from agent_builder.design.failure_notice import requirement_failure_reason
from agent_builder.design.presentation import public_requirement_errors
from agent_builder.design.requirement_contracts import RequirementPlan
from infrastructure.persistence.generation_history import GenerationHistory
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage


GENERIC = ('입력하신 조건은 보존했습니다. 현재 이 요청을 처리할 수 없습니다. '
           '변경 사항은 적용하거나 저장하지 않았습니다.')
LEGACY = '요청 업무 목록을 확정하지 못했습니다. private_token / private_customer_value'


def failed_state():
    return {'messages': [{'role': 'user', 'content': '비용 조건으로 JSON을 만들어줘.'},
                         {'role': 'assistant', 'content': GENERIC}],
            'status': 'validation_failed', 'issues': [GENERIC],
            'requirement_failure': {'kind': 'processing_error', 'retryable': True,
                                    'stage': 'requirements', 'attempt': 2,
                                    'request_fingerprint': 'same-input'},
            'trace': [{'node': 'requirements', 'status': 'failed', 'detail': GENERIC,
                       'failure_kind': 'model_output_error', 'error_type': 'ValueError'},
                      {'node': 'requirements', 'label': '단계 소요 시간', 'elapsed_ms': 41000}]}


def test_current_saved_generic_failure_is_diagnosed_without_another_identical_retry():
    current = failed_state()
    original = deepcopy(current)
    shown = public_requirement_errors(current)
    failure = shown['requirement_failure']
    assert failure['retryable'] is False and failure['reason_code'] == 'invalid_model_response'
    assert failure['attempt'] == 2 and failure['request_fingerprint'] == 'same-input'
    assert failure['reason'] in shown['messages'][-1]['content']
    assert failure['action'] in shown['issues'][0]
    assert '2' in failure['attempt_notice'] and '관리자' in failure['action']
    assert shown['trace'][0]['detail'] == shown['messages'][-1]['content']
    assert current == original


def test_current_failed_trace_can_identify_saved_generic_model_output_error():
    current = failed_state()
    current.pop('requirement_failure')
    shown = public_requirement_errors(current)
    assert shown['requirement_failure']['retryable'] is False
    assert shown['requirement_failure']['failure_kind'] == 'model_output_error'


@pytest.mark.parametrize('status', ['ready', 'saved', 'needs_clarification'])
def test_historical_legacy_message_does_not_create_a_current_failure(status):
    current = {'messages': [{'role': 'user', 'content': '이전 요청'},
                            {'role': 'assistant', 'content': LEGACY},
                            {'role': 'user', 'content': '현재 요청'},
                            {'role': 'assistant', 'content': '현재 요청은 확인됐습니다.'}],
               'status': status, 'issues': [],
               'trace': [{'node': 'requirements', 'detail': LEGACY}]}
    shown = public_requirement_errors(current)
    assert 'requirement_failure' not in shown
    assert 'private_' not in json.dumps(shown)
    assert shown['messages'][-1] == current['messages'][-1]
    assert shown['status'] == status


def test_historical_generic_error_and_a_new_question_are_not_rewritten_as_current_error():
    current = failed_state()
    current['messages'] += [{'role': 'user', 'content': '새 조건으로 해줘.'},
                            {'role': 'assistant', 'content': '대상 열은 무엇인가요?'}]
    current.pop('requirement_failure')
    current['trace'].append({'node': 'condition_check', 'status': 'failed', 'detail': '새 오류'})
    shown = public_requirement_errors(current)
    assert 'requirement_failure' not in shown
    assert shown['messages'] == current['messages']


def test_diagnosed_metadata_and_user_text_are_preserved():
    current = failed_state()
    current['messages'][0]['content'] = LEGACY
    current['requirement_failure'].update(
        reason_code='invalid_model_response', reason='필수 작업 항목이 누락됐습니다.',
        action='관리자가 응답 계약을 확인해야 합니다.', retryable=False)
    original = deepcopy(current['requirement_failure'])
    shown = public_requirement_errors(current)
    assert shown['requirement_failure'] == original
    assert shown['messages'][0] == current['messages'][0]
    assert original['reason'] in shown['messages'][-1]['content']


def test_current_legacy_failure_is_sanitized_and_preserves_retained_draft_notice():
    current = failed_state()
    current['messages'][-1]['content'] = LEGACY
    current['issues'] = [LEGACY]
    current['draft'] = {'workflow': {'nodes': []}}
    shown = public_requirement_errors(current)
    assert shown['requirement_failure']['retryable'] is False
    assert '이전 워크플로우 초안은 유지' in shown['messages'][-1]['content']
    assert 'private_' not in json.dumps(shown)


def test_legacy_failure_without_diagnostics_does_not_invent_a_specific_cause():
    current = {'messages': [{'role': 'user', 'content': '새 요청'},
                            {'role': 'assistant', 'content': LEGACY}],
               'status': 'validation_failed', 'issues': [LEGACY], 'trace': []}
    shown = public_requirement_errors(current)
    assert shown['requirement_failure']['reason_code'] == 'processing_error'
    assert '구체적인 원인을 확인하지 못했습니다' in shown['requirement_failure']['reason']
    assert shown['requirement_failure']['retryable'] is False


def _invalid_plan_error():
    try:
        RequirementPlan.model_validate({'tasks': 'private_customer_value', 'unresolved': []})
    except ValidationError as error:
        return error
    raise AssertionError('fixture must be invalid')


@pytest.mark.parametrize('error', [
    ValueError('업무 ID·선행 연결·원문 인용은 앱 코드가 부여합니다.'),
    ValueError('요구사항의 사용자 원문 근거를 확인하지 못했습니다.'),
    json.JSONDecodeError('private_response_body', 'private_customer_value', 0),
    _invalid_plan_error(),
    ValueError('private_response_body private_customer_value Bearer private_token'),
    TypeError('private_response_body private_customer_value'),
])
def test_admin_requirement_contract_failure_uses_only_allowlisted_reason(tmp_path, error):
    storage = Storage(tmp_path)
    try:
        session = storage.new_session('test', uid())
        audit = GenerationHistory.start(storage, {'action': 'message', 'id': session['id'],
            'revision': session['revision'], 'content': '안전한 시험 요청', 'generation_request_id': uid()})
        audit.enter('requirements')
        audit.error(error)
        record = audit.finish({'status': 'validation_failed', 'issues': [GENERIC]})
        assert record['reason_code'] == 'invalid_model_response'
        assert record['reason'] == requirement_failure_reason(error)
        assert record['events'][-1]['reason'] == record['reason']
        serialized = json.dumps(record, ensure_ascii=False)
        assert 'private_' not in serialized and str(error) not in serialized
    finally:
        storage.close()


def test_admin_requirement_timeout_retains_transport_diagnosis(tmp_path):
    storage = Storage(tmp_path)
    try:
        session = storage.new_session('test', uid())
        audit = GenerationHistory.start(storage, {'action': 'message', 'id': session['id'],
            'revision': 0, 'content': '안전한 시험 요청', 'generation_request_id': uid()})
        audit.enter('requirements')
        audit.error(TimeoutError('private_response_body'))
        record = audit.finish({'status': 'validation_failed'})
        assert record['reason_code'] == 'model_timeout'
        assert '응답 시간' in record['reason'] and 'private_' not in json.dumps(record)
    finally:
        storage.close()
