"""Actionable design failures without leaking model bodies or internal fields."""


def requirement_failure_reason(error):
    import json
    from pydantic import ValidationError
    if isinstance(error, json.JSONDecodeError):
        return 'AI가 작업 목록을 읽을 수 없는 응답 형식으로 반환했습니다.'
    if isinstance(error, ValidationError):
        missing = any(item['type'] == 'missing' for item in error.errors(include_input=False))
        return ('AI가 작업 목록에 필요한 필수 항목을 누락했습니다.' if missing else
                'AI가 작업 목록의 항목을 잘못된 형식으로 반환했습니다.')
    # Match only application-owned validation messages; never echo provider bodies or values.
    message = str(error)
    reasons = {
        '업무 목록 응답 형식을 확인하세요.': 'AI가 작업 목록을 잘못된 형식으로 반환했습니다.',
        '업무 ID·선행 연결·원문 인용은 앱 코드가 부여합니다.': 'AI가 작업 항목에 필요한 정보 대신 허용되지 않은 항목을 반환했습니다.',
        '요구사항의 사용자 발화를 확인하지 못했습니다.': 'AI가 작성한 작업의 사용자 요청 근거를 확인하지 못했습니다.',
        '요구사항 응답이 중간에 끝났습니다.': 'AI 응답이 작업 목록을 끝까지 작성하기 전에 중단됐습니다.',
        '요구사항 응답 본문이 올바른 문자열이 아닙니다.': 'AI 응답에 작업 목록을 읽을 수 있는 본문이 없습니다.',
        '이미 확인한 조건이나 현재 필요하지 않은 예약 질문만 반환했습니다. 요청한 실제 업무 목록을 추출하세요.':
            'AI가 요청을 처리할 실제 작업 목록을 반환하지 않았습니다.',
        '요구사항의 사용자 원문 근거를 확인하지 못했습니다.': 'AI가 작성한 작업의 근거를 사용자 요청에서 확인하지 못했습니다.',
        '조회 개수가 사용자 원문의 수량과 다릅니다.': 'AI가 해석한 조회 건수가 요청한 건수와 일치하지 않습니다.',
        '고객에게 표시할 구체적인 질문을 구성하지 못했습니다.': 'AI가 필요한 확인 질문을 유효한 형식으로 만들지 못했습니다.',
    }
    return reasons.get(message, 'AI가 작성한 작업 목록의 설정이나 작업 간 연결을 검증하지 못했습니다.')


def requirement_failure_notice(kind, error, *, retained_draft=False, attempt=1, stage='requirements'):
    from openai import (APIConnectionError, APITimeoutError, AuthenticationError,
                        PermissionDeniedError, RateLimitError)
    prefix = '입력하신 조건은 보존했습니다. '
    label={'requirements':'요청을 작업 목록으로 정리하는', 'capability_check':'사용할 도구의 지원 여부를 확인하는',
           'condition_check':'입력한 처리 조건을 확인하는'}.get(stage,'업무 흐름을 구성하는')
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
        subject={'capability_check':'도구 지원 판단','condition_check':'처리 조건'}.get(stage,'작업 목록')
        action = (label + ' 단계에서 ' + requirement_failure_reason(error).replace('작업 목록',subject) +
                  ' 변경 적용과 저장을 중단했습니다. 같은 조건으로 다시 시도할 수 있습니다.')
    else:
        action = label+' 단계에서 처리를 완료하지 못했습니다. 선택한 모델의 연결 상태를 확인한 뒤 같은 조건으로 다시 시도해주세요.'
    if retained_draft:
        action += ' 이전 워크플로우 초안은 유지했으며, 이번 변경은 아직 적용되지 않았습니다.'
    if attempt > 1:
        action += f' 같은 조건의 {attempt}번째 시도도 실패했습니다. 재시도는 실행됐지만 이 단계의 검증을 통과하지 못했습니다.'
    return prefix + action
