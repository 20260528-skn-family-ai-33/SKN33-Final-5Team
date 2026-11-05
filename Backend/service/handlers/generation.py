"""Short control calls used even when a design worker cannot start or is killed."""
from infrastructure.persistence.generation_history import GenerationHistory, history

INTERRUPT_REASONS = {
    'worker_timeout': '생성 작업의 제한 시간이 초과되어 작업자를 종료했습니다.',
    'worker_busy': '처리 중인 작업이 많아 생성 작업을 시작하지 못했습니다.',
    'worker_start_failed': '생성 작업자를 시작하지 못했습니다.',
    'worker_response_invalid': '생성 작업자의 응답을 해석하지 못했습니다.',
    'request_rejected': '모델 연결 계정 또는 생성 요청의 준비 상태를 확인하세요.',
    'worker_failed': '생성 작업자가 정상적으로 완료되지 않았습니다.',
}


def begin(c):
    action = c.data['request_action']
    if action not in ('message', 'confirm'):
        raise ValueError('생성 요청 종류를 확인하세요.')
    audit = GenerationHistory.start(c.storage, {**c.data, 'action': action})
    return {'generation_request_id': audit.id}


def interrupt(c):
    c.storage.session(c.data['id'])
    audit = GenerationHistory(c.storage, c.data['generation_request_id'])
    record = audit.read()
    if record['session_id'] != c.data['id']:
        raise ValueError('이 대화의 생성 요청이 아닙니다.')
    code = c.data.get('reason_code', 'worker_failed')
    if code not in INTERRUPT_REASONS:
        code = 'worker_failed'
    return audit.change(status='failed', reason_code=code, reason=INTERRUPT_REASONS[code], error_type=None)


def listing(c):
    return {'requests': history(c.storage, c.data['id'])}
