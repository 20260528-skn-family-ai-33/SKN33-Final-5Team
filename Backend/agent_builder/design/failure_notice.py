"""Actionable design failures without leaking model bodies or internal fields."""


def requirement_failure_notice(kind, error):
    from openai import (APIConnectionError, APITimeoutError, AuthenticationError,
                        PermissionDeniedError, RateLimitError)
    prefix = '입력하신 조건은 보존했습니다. '
    if isinstance(error, (APITimeoutError, TimeoutError)):
        action = '모델 응답 시간이 초과됐습니다. 잠시 후 같은 조건으로 다시 시도해주세요. 반복되면 선택한 모델의 연결 상태를 확인해주세요.'
    elif isinstance(error, (AuthenticationError, PermissionDeniedError)):
        action = '선택한 모델의 연결 계정이나 사용 권한을 확인한 뒤 다시 시도해주세요.'
    elif isinstance(error, RateLimitError):
        action = '모델 사용 한도에 도달했습니다. 연결 계정의 사용 한도를 확인하거나 잠시 후 다시 시도해주세요.'
    elif isinstance(error, APIConnectionError):
        action = '모델에 연결하지 못했습니다. 선택한 모델의 연결 상태를 확인한 뒤 같은 조건으로 다시 시도해주세요.'
    elif kind == 'condition_state_error':
        action = '확정 조건을 다시 확인하지 못했습니다. 대화를 다시 열어 기존 답변을 확인한 뒤 재시도해주세요.'
    elif kind == 'model_output_error':
        action = ('모델 응답이 요청 조건과 맞지 않아 저장을 중단했습니다. 같은 조건으로 다시 시도할 수 있습니다. '
                  '반복되면 입력 자료 → 처리 조건 → 원하는 결과 순서로 요청을 나누어 적어주세요.')
    else:
        action = '업무 흐름을 구성하지 못했습니다. 선택한 모델의 연결 상태를 확인한 뒤 같은 조건으로 다시 시도해주세요.'
    return prefix + action
