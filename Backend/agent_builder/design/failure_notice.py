"""Actionable design failures without leaking model bodies or internal fields."""


def requirement_failure_reason(error):
    import json
    from pydantic import ValidationError
    if isinstance(error, json.JSONDecodeError):
        return 'AI가 작업 목록을 읽을 수 없는 응답 형식으로 반환했습니다.'
    if isinstance(error, ValidationError):
        missing = any(item['type'] == 'missing' for item in error.errors(include_input=False))
        if not missing:
            for item in error.errors(include_input=False):
                cause=(item.get('ctx') or {}).get('error')
                if isinstance(cause, ValueError):
                    known=requirement_failure_reason(cause)
                    if known != 'AI가 작성한 작업 목록의 설정이나 작업 간 연결을 검증하지 못했습니다.':
                        return known
        return ('AI가 작업 목록에 필요한 필수 항목을 누락했습니다.' if missing else
                'AI가 작업 목록의 항목을 잘못된 형식으로 반환했습니다.')
    # Match only application-owned validation messages; never echo provider bodies or values.
    message = str(error)
    reasons = {
        '업무 목록 응답 형식을 확인하세요.': 'AI가 작업 목록을 잘못된 형식으로 반환했습니다.',
        '업무 ID·선행 연결·원문 인용은 앱 코드가 부여합니다.': 'AI가 작업 항목에 필요한 정보 대신 허용되지 않은 항목을 반환했습니다.',
        '요구사항의 사용자 발화를 확인하지 못했습니다.': 'AI가 작성한 작업의 사용자 요청 근거를 확인하지 못했습니다.',
        '요구사항 응답이 중간에 끝났습니다.': 'AI 응답이 작업 목록을 끝까지 작성하기 전에 중단됐습니다.',
        '요구사항 추출 응답이 중간에 끝났습니다.': 'AI 응답이 작업 목록을 끝까지 작성하기 전에 중단됐습니다.',
        '요구사항 응답 본문이 올바른 문자열이 아닙니다.': 'AI 응답에 작업 목록을 읽을 수 있는 본문이 없습니다.',
        '이미 확인한 조건이나 현재 필요하지 않은 예약 질문만 반환했습니다. 요청한 실제 업무 목록을 추출하세요.':
            'AI가 요청을 처리할 실제 작업 목록을 반환하지 않았습니다.',
        '요구사항의 사용자 원문 근거를 확인하지 못했습니다.': 'AI가 작성한 작업의 근거를 사용자 요청에서 확인하지 못했습니다.',
        '조회 개수가 사용자 원문의 수량과 다릅니다.': 'AI가 해석한 조회 건수가 요청한 건수와 일치하지 않습니다.',
        '고객에게 표시할 구체적인 질문을 구성하지 못했습니다.': 'AI가 필요한 확인 질문을 유효한 형식으로 만들지 못했습니다.',
        '요청한 업무나 확인할 조건이 하나 이상 필요합니다.': 'AI가 요청을 처리할 실제 작업 목록을 반환하지 않았습니다.',
        '요청한 업무 목록을 구성해야 합니다.': 'AI가 요청을 처리할 실제 작업 목록을 반환하지 않았습니다.',
        '요구사항 ID가 중복됩니다.': 'AI가 서로 다른 작업을 구분하는 정보를 중복 작성했습니다.',
        '요구사항의 선행 업무에 순환이 있습니다.': 'AI가 작업 순서를 서로 되돌아가는 연결로 작성해 실행 순서를 정할 수 없습니다.',
        '확정 조건과 대조되지 않은 질문이 요구사항에 남아 있습니다.': 'AI가 이미 확인한 조건과 맞지 않는 질문을 다시 작성했습니다.',
        '이미 확인한 조건을 다시 미정으로 질문할 수 없습니다. 기존 답변을 반영하세요.': 'AI가 이미 답변한 조건을 반영하지 않고 같은 내용을 다시 질문했습니다.',
        '선별 조건은 기준 값 또는 비교 열 중 하나만 가져야 합니다.': 'AI가 숫자 기준과 비교할 열을 동시에 지정해 선별 방법을 확정할 수 없습니다.',
        '선별 기준 숫자가 사용자 원문에서 확인되지 않습니다.': 'AI가 작성한 선별 기준값이 입력한 요청이나 답변에 근거하지 않습니다.',
        '복합 선별 조건의 원문·대상·결합 방식을 확인하지 못했습니다.': 'AI가 여러 선별 조건을 어느 자료에 어떻게 결합할지 검증하지 못했습니다.',
        '별도 선별 조건을 적용할 출력·수정 범위를 확인하지 못했습니다.': 'AI가 선별 조건을 적용할 결과와 수정 범위를 구분하지 못했습니다.',
        '요청한 선별·중복 제거 순서를 선행 업무에 반영해야 합니다.': 'AI가 선별과 중복 제거의 처리 순서를 요청과 다르게 작성했습니다.',
        '요구사항에 실제 연결 자료가 아닌 ID가 있습니다.': 'AI가 작업에 연결되지 않은 자료를 입력으로 지정했습니다.',
        '원문에서 요청한 사람 검토가 업무 목록에서 누락됐습니다.': 'AI가 요청한 사람 검토 단계를 작업 목록에서 빠뜨렸습니다.',
    }
    if message.startswith('검증된 업무 조건과 요구사항 설정이 다릅니다: '):
        return 'AI가 작성한 작업 설정이 이미 확인한 업무 조건과 일치하지 않습니다.'
    if message.startswith('요구사항 ID·선행 업무 관계가 잘못됐습니다: '):
        return 'AI가 실제로 존재하지 않거나 순서가 맞지 않는 선행 작업을 연결했습니다.'
    return reasons.get(message, 'AI가 작성한 작업 목록의 설정이나 작업 간 연결을 검증하지 못했습니다.')


def requirement_failure_details(kind, error=None, *, attempt=1, stage='requirements', http_status=None, error_code=None):
    """Public, allowlisted diagnosis; a processing failure is never a missing user answer.

    Two invalid model responses have already been repaired internally. Offering another
    identical request as the solution hides a contract defect. Only identified transient
    transport errors get one user retry; the diagnosis survives independently of it.
    """
    from infrastructure.llm.failure_classification import classify_model_failure
    code, canonical_kind = classify_model_failure(kind, error, http_status=http_status, error_code=error_code)
    transient = False
    if code == 'context_budget_exceeded':
        reason = 'AI 서버가 처리할 수 있는 입력 길이를 초과했습니다.'
        action = '관리자가 대화·자료의 크기와 AI 서버의 문맥 한도를 확인해야 합니다. 업무 조건을 임의로 바꾸지 않아도 됩니다.'
    elif code == 'model_timeout':
        reason = 'AI 응답 시간이 초과돼 처리 결과를 받지 못했습니다.'
        action = 'AI 서버의 응답 상태가 회복된 뒤 다시 시도해주세요.'
        transient = True
    elif code == 'model_auth_failed':
        reason = 'AI 서버의 연결 계정이나 접근 권한 확인에 실패했습니다.'
        action = '관리자가 AI 서버의 연결 계정과 사용 권한을 확인해야 합니다.'
    elif code == 'model_rate_limited':
        reason = 'AI 서버의 사용 한도 또는 동시 요청 제한에 도달했습니다.'
        action = '사용 한도나 동시 요청 제한이 해제된 뒤 다시 시도해주세요.'
        transient = True
    elif code == 'model_server_error':
        reason = 'AI 서버가 일시적인 서버 오류를 반환했습니다.'
        action = 'AI 서버의 장애가 해소된 뒤 다시 시도해주세요.'
        transient = True
    elif code == 'model_request_rejected':
        reason = 'AI 서버가 요청을 거부했습니다. 현재 기록만으로 구체적인 거부 원인을 확인하지 못했습니다.'
        action = '관리자가 AI 서버의 요청 규격과 입력 한도를 확인해야 합니다.'
    elif code == 'generation_step_limit':
        reason = '설계·검증 처리가 단계 수 제한 안에 완료되지 못했습니다.'
        action = '관리자가 설계·검증의 반복 경로를 확인해야 합니다.'
    elif code == 'model_connection_failed':
        reason = 'AI 서버에 연결하지 못했습니다.'
        action = 'AI 서버의 연결 상태가 회복된 뒤 다시 시도해주세요.'
        transient = True
    elif code == 'condition_state_invalid':
        reason = '이미 확인한 답변을 현재 요청의 처리 조건과 연결해 검증하지 못했습니다.'
        action = '확정된 답변을 처리하는 앱의 오류 확인이 필요합니다. 관리자에게 이 안내를 전달해주세요.'
    elif code == 'invalid_model_response':
        if stage == 'requirements':
            reason = requirement_failure_reason(error)
        elif stage == 'condition_check':
            reason = 'AI가 처리 조건과 확인 질문을 올바른 형식으로 구성하지 못했습니다.'
        else:
            reason = 'AI의 도구 지원 판단과 작업에 필요한 조건이 서로 맞지 않거나 응답 형식이 잘못됐습니다.'
        action = 'AI 응답을 구성·검증하는 처리의 오류 확인이 필요합니다. 관리자에게 이 안내를 전달해주세요.'
    else:
        reason = '처리 중 오류가 발생했으며 현재 기록만으로 구체적인 원인을 확인하지 못했습니다.'
        action = '관리자가 실패 기록을 확인해야 합니다. 필요한 조건을 추측해 다시 입력하지 않아도 됩니다.'
    result = dict(stage=stage, failure_kind=canonical_kind, reason_code=code, reason=reason,
                  action=action, retryable=transient and attempt < 2)
    if attempt > 1:
        result['attempt_notice'] = f'같은 조건의 {attempt}번째 시도도 실패했습니다. 반복 전송으로 해결되지 않고 있어 오류 확인이 필요합니다.'
        result['action'] = '관리자에게 실패 단계와 이 안내를 전달해 원인을 확인해주세요.'
    return result


def requirement_failure_notice(kind, error, *, retained_draft=False, attempt=1, stage='requirements', error_code=None):
    details = requirement_failure_details(kind, error, attempt=attempt, stage=stage, error_code=error_code)
    label={'requirements':'요청을 작업 목록으로 정리하는', 'capability_check':'사용할 도구의 지원 여부를 확인하는',
           'condition_check':'입력한 처리 조건을 확인하는'}.get(stage,'업무 흐름을 구성하는')
    notice = f"{label} 단계에서 중단됐습니다. {details['reason']} {details['action']}"
    if details.get('attempt_notice'):
        notice += ' ' + details['attempt_notice']
    notice += ' 입력하신 조건은 보존했습니다. 변경 사항을 적용하거나 저장하지 않았으며 저장을 중단했습니다.'
    if retained_draft:
        notice += ' 이전 워크플로우 초안은 유지했으며, 이번 변경은 아직 적용되지 않았습니다.'
    return notice
