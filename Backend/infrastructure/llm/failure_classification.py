"""Classify model failures without exposing provider bodies, URLs or input values."""
import json
import re


_OUTPUT_ERRORS = {
    'ValueError', 'TypeError', 'KeyError', 'ValidationError', 'JSONDecodeError',
    'ConditionContractError', 'AssessmentConflictError', 'StructuredOutputError',
    'StructuredOutputValidationError', 'MultipleStructuredOutputsError', 'DraftResponseError',
}
_TIMEOUTS = {'APITimeoutError', 'TimeoutError', 'ReadTimeout', 'ConnectTimeout', 'PoolTimeout'}
_CONNECTIONS = {'APIConnectionError', 'ConnectionError', 'ConnectError', 'URLError'}
_CONTEXT_CODES = {'context_length_exceeded', 'context_window_exceeded', 'context_budget_exceeded',
                  'max_context_length_exceeded', 'prompt_too_long', 'input_too_long'}


class ModelOutputLimitError(ValueError):
    """A finished transport response whose generation exhausted its output budget."""
    def __init__(self):
        super().__init__('로컬 모델 응답이 출력 길이 제한으로 중단되었습니다. 잘린 응답은 처리하지 않습니다.')


def _http_status(value):
    return value if type(value) is int and 100 <= value <= 599 else None


def context_limit_code(value):
    """Inspect only bounded error fields; retain a fixed code, never their text."""
    if isinstance(value, dict):
        return next((code for key in ('code', 'type', 'message', 'error')
                     if key in value and (code := context_limit_code(value[key]))), None)
    if not isinstance(value, str):
        return None
    text = value[:8192].casefold()
    if text in _CONTEXT_CODES or re.search(
            r'context[ _-]*(?:length|window|budget).{0,80}(?:exceed|too (?:long|large)|limit)'
            r'|(?:exceed|too (?:long|large)).{0,80}(?:context[ _-]*(?:length|window)|maximum context)'
            r'|prompt (?:is )?too long|input (?:is )?too long', text):
        return 'context_budget_exceeded'
    return None


class ModelTransportError(RuntimeError):
    """Safe native-transport envelope; the original exception remains a cause."""
    def __init__(self, *, http_status=None, error_code=None):
        self.status_code = _http_status(http_status)
        self.error_code = context_limit_code(error_code)
        super().__init__('모델 서버 요청을 완료하지 못했습니다.')

    @classmethod
    def from_http_error(cls, error):
        code = context_limit_code(getattr(error, 'reason', None))
        if not code:
            try:
                body = error.read(8192).decode('utf-8', errors='replace')
                try:
                    body = json.loads(body)
                except (ValueError, TypeError):
                    pass
                code = context_limit_code(body)
            except (OSError, AttributeError, TypeError, ValueError):
                pass
        status=getattr(error, 'code', None)
        # The bounded body was just read from this response; its context code is
        # actual transport evidence, not potentially stale checkpoint metadata.
        category,_=classify_model_failure('model_error',http_status=status,error_code=code)
        return _SAFE_ERRORS.get(category,cls)(http_status=status,error_code=code)

    @classmethod
    def from_exception(cls,error):
        category,_=classify_model_failure('model_error',error)
        return _SAFE_ERRORS.get(category,cls)()


# Some gates persist only error_type, not exception objects. Typed safe envelopes
# retain the category across that boundary without storing an HTTP body or URL.
class ModelServerError(ModelTransportError):pass
class ModelContextLimitError(ModelTransportError):pass
class ModelRequestError(ModelTransportError):pass
class ModelAuthenticationError(ModelTransportError):pass
class ModelRateLimitError(ModelTransportError):pass
class ModelTimeoutError(ModelTransportError):pass
class ModelConnectionError(ModelTransportError):pass


_SAFE_ERRORS = {
    'model_server_error':ModelServerError,'context_budget_exceeded':ModelContextLimitError,
    'model_request_rejected':ModelRequestError,'model_auth_failed':ModelAuthenticationError,
    'model_rate_limited':ModelRateLimitError,'model_timeout':ModelTimeoutError,
    'model_connection_failed':ModelConnectionError,
}
_SAFE_CODES = {error_type.__name__:code for code,error_type in _SAFE_ERRORS.items()}
_CANONICAL_KINDS = {**{code:'model_error' for code in _SAFE_ERRORS},
    'context_budget_exceeded':'model_input_error','model_request_rejected':'model_input_error',
    'invalid_model_response':'model_output_error','condition_state_invalid':'condition_state_error',
    'model_output_limit':'model_output_error',
    'generation_step_limit':'model_error'}


def _transport_category(names,status,context):
    safe_codes={_SAFE_CODES[name] for name in names if name in _SAFE_CODES}
    if context or 'context_budget_exceeded' in safe_codes:
        return 'context_budget_exceeded'
    # An actual response status takes precedence over a legacy/type label.
    if status is not None:
        if status==408:return 'model_timeout'
        if status in (401,403):return 'model_auth_failed'
        if status==429:return 'model_rate_limited'
        if status>=500:return 'model_server_error'
        if status>=400:return 'model_request_rejected'
    if names & _TIMEOUTS or 'model_timeout' in safe_codes:return 'model_timeout'
    if names & {'AuthenticationError','PermissionDeniedError'} or 'model_auth_failed' in safe_codes:return 'model_auth_failed'
    if 'RateLimitError' in names or 'model_rate_limited' in safe_codes:return 'model_rate_limited'
    if 'InternalServerError' in names or 'model_server_error' in safe_codes:return 'model_server_error'
    if names & {'BadRequestError','NotFoundError','UnprocessableEntityError'} or 'model_request_rejected' in safe_codes:return 'model_request_rejected'
    if names & _CONNECTIONS or 'model_connection_failed' in safe_codes:return 'model_connection_failed'
    if 'GraphRecursionError' in names:return 'generation_step_limit'
    return None


def classify_model_failure(kind, error=None, *, http_status=None, error_code=None):
    """One policy for modern exceptions, their causes and legacy class-name metadata."""
    causes = []
    current = error
    while isinstance(current, BaseException) and len(causes) < 8 and all(current is not e for e in causes):
        causes.append(current)
        current = current.__cause__ or current.__context__ or getattr(current, 'reason', None)
    names = {base.__name__ for cause in causes for base in type(cause).__mro__}
    if isinstance(error, str):
        names.add(error.rsplit('.', 1)[-1])
    status = None
    context = None
    for cause in causes:
        status = status or _http_status(getattr(cause, 'status_code', None)) or _http_status(getattr(cause, 'code', None))
        status = status or _http_status(getattr(getattr(cause, 'response', None), 'status_code', None))
        context = context or context_limit_code(getattr(cause, 'error_code', None))
        context = context or context_limit_code(getattr(cause, 'code', None)) or context_limit_code(getattr(cause, 'body', None))
    code = _transport_category(names,status,context) if causes else None
    if code:
        return code,_CANONICAL_KINDS[code]
    if causes and 'ModelOutputLimitError' in names:
        return 'model_output_limit', 'model_output_error'
    # Canonical, fixed metadata is a fallback when no actual transport diagnosis
    # survives. It must never override an exception's response status or cause.
    code = _transport_category(names if not causes else set(),_http_status(http_status),context_limit_code(error_code))
    if code:
        return code,_CANONICAL_KINDS[code]
    if 'ModelOutputLimitError' in names:
        return 'model_output_limit', 'model_output_error'
    if isinstance(error_code,str) and error_code in _CANONICAL_KINDS:
        return error_code,_CANONICAL_KINDS[error_code]
    if kind == 'condition_state_error':
        return 'condition_state_invalid', 'condition_state_error'
    if kind == 'model_output_error' or names & _OUTPUT_ERRORS:
        return 'invalid_model_response', 'model_output_error'
    return 'processing_error', kind if kind in ('model_error', 'system_error') else 'system_error'
