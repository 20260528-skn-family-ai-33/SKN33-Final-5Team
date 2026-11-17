"""A processing error must explain ownership and recovery, not invent missing inputs."""
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError
from pydantic import BaseModel, ValidationError

from agent_builder.conditions.clarification_core import system_failure
from agent_builder.design.failed_design import retained_design
from agent_builder.design.failure_notice import requirement_failure_details, requirement_failure_notice
from agent_builder.design.requirement_ledger import make_requirement_check
from tests.design.test_requirement_clarification import Extractor, current


def test_invalid_model_output_keeps_answers_and_reports_the_actual_stage():
    state = current('보고서를 만들어줘.', 'manual')
    before = deepcopy(state)
    model = Extractor({'tasks': 'private-model-body', 'unresolved': []})
    result = make_requirement_check('fixture', model, 'enforce')(state)
    failure = result['requirement_failure']
    assert result['status'] == 'validation_failed'
    assert failure['stage'] == 'requirements'
    assert failure['reason_code'] == 'invalid_model_response'
    assert not failure['retryable']
    assert '작업 목록' in failure['reason'] and '잘못된 형식' in failure['reason']
    assert '관리자' in failure['action']
    assert result['pending_questions'] == []
    assert state == before
    assert len(model.calls) == 2  # The contract repair already failed inside this request.
    notice = result['messages'][-1]['content']
    assert '작업 목록으로 정리하는 단계' in notice and '저장을 중단' in notice
    assert 'private-model-body' not in notice
    assert '다시 시도' not in notice and '더 구체적으로' not in notice


def test_missing_model_field_is_not_presented_as_a_missing_user_answer():
    class Task(BaseModel):
        name: str
    with pytest.raises(ValidationError) as caught:
        Task.model_validate({'provider_body': 'private-model-body'})
    details = requirement_failure_details('model_output_error', caught.value)
    assert 'AI가 작업 목록에 필요한 필수 항목을 누락' in details['reason']
    assert not details['retryable']
    assert 'private-model-body' not in str(details)


@pytest.mark.parametrize('error,code', [
    (APITimeoutError(request=httpx.Request('POST', 'https://private.example/key')), 'model_timeout'),
    (APIConnectionError(request=httpx.Request('POST', 'https://private.example/key')), 'model_connection_failed'),
    (RateLimitError('private body', response=httpx.Response(429, request=httpx.Request('POST', 'https://private.example/key')), body=None), 'model_rate_limited'),
])
def test_only_identified_transient_errors_offer_one_retry(error, code):
    first = requirement_failure_details('system_error', error)
    repeated = requirement_failure_details('system_error', error, attempt=2)
    assert first['reason_code'] == code and first['retryable']
    assert not repeated['retryable'] and '2번째 시도도 실패' in repeated['attempt_notice']
    assert '2번째' not in repeated['action']
    assert 'private' not in str(first) + str(repeated)


def test_access_failure_requires_connection_repair_instead_of_identical_requests():
    error = AuthenticationError('private body', response=httpx.Response(401,
        request=httpx.Request('POST', 'https://private.example/key')), body=None)
    details = requirement_failure_details('system_error', error)
    assert details['reason_code'] == 'model_auth_failed' and not details['retryable']
    assert '사용 권한' in details['action'] and '관리자' in details['action']
    assert 'private' not in str(details)


def test_unknown_failure_does_not_guess_a_missing_field_or_connection_problem():
    details = requirement_failure_details('system_error', RuntimeError('private raw reply'))
    assert not details['retryable']
    assert '원인을 확인하지 못했습니다' in details['reason']
    assert '연결 상태' not in details['reason'] and 'private' not in str(details)


@pytest.mark.parametrize('stage', ['capability_check', 'condition_check'])
def test_contract_errors_in_other_gates_also_stop_retry_promises(stage):
    failure = system_failure(stage, 'ConditionContractError', 'AI 응답 검증에 실패했습니다.')
    assert not failure['retryable']
    assert failure['reason'] and '관리자' in failure['action']


def test_repeated_transport_failure_has_consistent_chat_and_panel_guidance():
    state = current('보고서를 만들어줘.', 'manual')
    notice = requirement_failure_notice('model_error', TimeoutError(), stage='condition_check')
    first = system_failure('condition_check', 'TimeoutError', notice, state=state)
    repeated = system_failure('condition_check', 'TimeoutError', notice,
                              state={**state, 'system_failure': first})
    assert repeated['attempt'] == 2 and not repeated['retryable']
    assert repeated['action'] in repeated['message']
    assert '관리자' in repeated['message'] and '다시 시도' not in repeated['message']


def test_previous_verified_draft_is_retained_for_nonretryable_processing_failure():
    approved = {'status': 'ready', 'draft': {'workflow': {'nodes': []}}, 'resources': []}
    failed = {'status': 'validation_failed', 'draft': None, 'resources': [],
              'requirement_failure': {'kind': 'processing_error', 'retryable': False}}
    graph = SimpleNamespace(get_state_history=lambda *args, **kwargs: iter([
        SimpleNamespace(values=approved, next=('await_approval',))]))
    recovered = retained_design(failed, graph, {})
    assert recovered['draft'] == approved['draft']
    assert recovered['status'] == 'validation_failed'
    assert not recovered['requirement_failure']['retryable']
    assert failed['draft'] is None


def test_allowlisted_domain_validation_reason_is_preserved_without_echoing_values():
    error = ValueError('조회 개수가 사용자 원문의 수량과 다릅니다.')
    notice = requirement_failure_notice('model_output_error', error, retained_draft=True)
    assert 'AI가 해석한 조회 건수가 요청한 건수와 일치하지 않습니다' in notice
    assert '이전 워크플로우 초안은 유지' in notice and '아직 적용되지' in notice
