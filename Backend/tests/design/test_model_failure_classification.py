"""Transport failures keep safe, consistent diagnoses across chat and audit history."""
import io
import json
import urllib.error

import httpx
import pytest
from openai import BadRequestError, InternalServerError

from agent_builder.conditions.clarification_core import system_failure
from agent_builder.design.failure_notice import requirement_failure_details, requirement_failure_notice
from agent_builder.design.nodes.direct_conversation import OllamaPlanner
from infrastructure.llm.failure_classification import ModelTransportError
from infrastructure.persistence.generation_history import GenerationHistory, error_info
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage


PRIVATE = 'private-customer-value private-token https://private.example/model'


def sdk_error(status, *, body=None):
    response=httpx.Response(status,request=httpx.Request('POST','https://private.example/model'))
    cls=InternalServerError if status>=500 else BadRequestError
    return cls(PRIVATE,response=response,body=body)


@pytest.mark.parametrize('status',[500,502,503,504,524])
@pytest.mark.parametrize('wrapped',[False,True])
def test_server_errors_use_status_and_cause_before_contract_error_kind(status,wrapped):
    error=sdk_error(status)
    if wrapped:
        outer=ValueError(PRIVATE)
        outer.__cause__=error
        error=outer
    first=requirement_failure_details('model_output_error',error)
    repeated=requirement_failure_details('model_output_error',error,attempt=2)
    assert first['reason_code']=='model_server_error' and first['failure_kind']=='model_error'
    assert first['retryable'] and not repeated['retryable']
    assert error_info(error,'request_analysis')['reason_code']==first['reason_code']
    assert 'private' not in json.dumps(first)+json.dumps(repeated)


def test_legacy_status_metadata_and_derived_sdk_classes_share_the_policy():
    class ProviderUnavailable(InternalServerError):
        pass
    error=ProviderUnavailable(PRIVATE,response=httpx.Response(503,
        request=httpx.Request('POST','https://private.example/model')),body=None)
    assert requirement_failure_details('model_error',error)['retryable']
    legacy=requirement_failure_details('model_error','HTTPError',http_status=503)
    assert legacy['reason_code']=='model_server_error' and legacy['retryable']
    context=requirement_failure_details('model_error','BadRequestError',http_status=400,
                                         error_code='context_length_exceeded')
    assert context['reason_code']=='context_budget_exceeded' and not context['retryable']


@pytest.mark.parametrize('cause,code',[
    (TimeoutError(PRIVATE),'model_timeout'),
    (urllib.error.URLError(PRIVATE),'model_connection_failed'),
    (urllib.error.URLError(TimeoutError(PRIVATE)),'model_timeout'),
])
def test_native_safe_exception_keeps_transport_kind_when_only_class_name_is_carried(cause,code):
    safe=ModelTransportError.from_exception(cause)
    direct=requirement_failure_details('model_error',safe)
    legacy=system_failure('condition_check',type(safe).__name__,'안전한 안내')
    assert direct['reason_code']==legacy['reason_code']==code
    assert legacy['failure_kind']=='model_error' and legacy['retryable']
    assert 'private' not in str(safe)+json.dumps(legacy)


@pytest.mark.parametrize('error',[
    sdk_error(400,body={'code':'context_length_exceeded','message':PRIVATE}),
    sdk_error(400,body={'error':{'type':'context_window_exceeded','message':PRIVATE}}),
    sdk_error(400,body={'message':'the input length exceeds the context length '+PRIVATE}),
])
def test_context_rejection_is_an_input_limit_not_an_output_contract_error(error):
    details=requirement_failure_details('system_error',error)
    assert details['reason_code']=='context_budget_exceeded' and details['failure_kind']=='model_input_error'
    assert not details['retryable'] and '입력 길이' in details['reason']
    assert '작업 간 연결' not in details['reason'] and 'private' not in json.dumps(details)
    assert error_info(error,'request_analysis')['reason_code']=='context_budget_exceeded'


def test_unknown_bad_request_does_not_invent_context_overflow():
    details=requirement_failure_details('system_error',sdk_error(400,body={'message':PRIVATE}))
    assert details['reason_code']=='model_request_rejected' and not details['retryable']
    assert '구체적인 거부 원인을 확인하지 못했습니다' in details['reason']
    assert 'private' not in json.dumps(details)


@pytest.mark.parametrize('error_type',['StructuredOutputError','StructuredOutputValidationError',
                                      'MultipleStructuredOutputsError'])
def test_structured_output_errors_have_one_canonical_kind_for_all_callers(error_type):
    error=type(error_type,(Exception,),{})(PRIVATE)
    for candidate in (error,error_type):
        direct=requirement_failure_details('model_error',candidate,stage='condition_check')
        core=system_failure('condition_check',candidate,'안전한 기존 안내')
        assert direct['failure_kind']==core['failure_kind']=='model_output_error'
        assert direct['reason_code']==core['reason_code']=='invalid_model_response'
        assert not core['retryable']
        assert 'private' not in json.dumps(direct)+json.dumps(core)


@pytest.mark.parametrize('status,body,code',[
    (503,{'error':PRIVATE},'model_server_error'),
    (400,{'error':'input length exceeds the context length '+PRIVATE},'context_budget_exceeded'),
])
def test_native_ollama_wraps_http_failures_safely_and_audits_the_actual_cause(monkeypatch,tmp_path,status,body,code):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','https://private.example/model/v1')
    monkeypatch.setenv('A4B_OLLAMA_GATEWAY_RETRIES','0')
    error=urllib.error.HTTPError('https://private.example/model/api/chat',status,PRIVATE,None,
        io.BytesIO(json.dumps(body).encode()))
    def unavailable(*args,**kwargs):
        raise error
    monkeypatch.setattr('urllib.request.urlopen',unavailable)
    with pytest.raises(ModelTransportError) as caught:
        OllamaPlanner('fixture').invoke([{'role':'user','content':'안전한 시험 요청'}])
    assert 'private' not in str(caught.value)
    details=requirement_failure_details('system_error',caught.value)
    assert details['reason_code']==code
    assert details['retryable'] is (status==503)
    string_boundary=system_failure('condition_check',type(caught.value).__name__,'안전한 안내')
    assert string_boundary['reason_code']==code
    assert string_boundary['retryable']==details['retryable']
    storage=Storage(tmp_path)
    try:
        session=storage.new_session('fixture',uid())
        audit=GenerationHistory.start(storage,{'action':'message','id':session['id'],'revision':0,
            'content':'안전한 시험 요청','generation_request_id':uid()})
        audit.enter('requirements');audit.error(caught.value)
        record=audit.finish({'status':'validation_failed'})
        assert record['reason_code']==code and 'private' not in json.dumps(record)
        assert '작업 간 연결' not in record['reason']
    finally:
        storage.close()


def test_attempt_description_is_separate_and_appears_once_in_chat():
    details=requirement_failure_details('model_error',TimeoutError(),attempt=2)
    assert '2번째' in details['attempt_notice'] and '2번째' not in details['action']
    notice=requirement_failure_notice('model_error',TimeoutError(),attempt=2)
    assert notice.count('2번째')==1


@pytest.mark.parametrize('code,kind,retryable',[
    ('model_auth_failed','model_error',False),
    ('invalid_model_response','model_output_error',False),
    ('model_server_error','model_error',True),
    ('model_timeout','model_error',True),
    ('model_connection_failed','model_error',True),
    ('model_rate_limited','model_error',True),
    ('model_request_rejected','model_input_error',False),
    ('context_budget_exceeded','model_input_error',False),
    ('generation_step_limit','model_error',False),
    ('condition_state_invalid','condition_state_error',False),
])
def test_canonical_metadata_only_failure_codes_follow_the_same_policy(code,kind,retryable):
    details=requirement_failure_details('system_error',error_code=code)
    assert details['reason_code']==code and details['failure_kind']==kind
    assert details['retryable'] is retryable
    assert not requirement_failure_details('system_error',error_code=code,attempt=2)['retryable']


@pytest.mark.parametrize('status,metadata,expected',[
    (503,'model_auth_failed','model_server_error'),
    (503,'context_budget_exceeded','model_server_error'),
    (401,'model_server_error','model_auth_failed'),
    (400,'invalid_model_response','model_request_rejected'),
])
def test_actual_response_status_overrides_conflicting_legacy_metadata(status,metadata,expected):
    error=sdk_error(status)
    outer=ValueError(PRIVATE)
    outer.__cause__=error
    details=requirement_failure_details('system_error',outer,http_status=429,error_code=metadata)
    assert details['reason_code']==expected
    assert 'private' not in json.dumps(details)


def test_actual_context_rejection_overrides_legacy_server_code():
    error=sdk_error(400,body={'code':'context_length_exceeded'})
    details=requirement_failure_details('system_error',error,http_status=503,error_code='model_server_error')
    assert details['reason_code']=='context_budget_exceeded' and not details['retryable']


def test_unknown_metadata_is_not_echoed_or_assigned_a_guessed_failure_category():
    details=requirement_failure_details('system_error',error_code=PRIVATE)
    assert details['reason_code']=='processing_error' and not details['retryable']
    assert 'private' not in json.dumps(details)
