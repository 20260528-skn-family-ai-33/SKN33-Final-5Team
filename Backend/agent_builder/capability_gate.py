"""Persisted capability gate shared by planning, confirmation and saving."""
import hashlib
import json
from .registry import TOOLS
from .capability_judgement import APP_CAPABILITIES


def capability_enforced(state):
    # Legacy enforce checkpoints contain an assessment but no explicit flag.
    return bool(state.get('capability_enforced') or state.get('capability_assessment'))


def input_fingerprint(state):
    payload={'users':[m for m in state.get('messages',[]) if m.get('role')=='user'],
             'resources':state.get('resources',[]),'source_health':state.get('source_health',{}),
             'tools':TOOLS,'app_capabilities':APP_CAPABILITIES}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def blocking_issue(state):
    if not capability_enforced(state):return None
    from .condition_readiness import input_issues
    if input_issues(state):return '입력 자료·조회 상태의 오류를 해결한 뒤 지원 여부를 다시 확인해주세요.'
    result=state.get('capability_assessment') or {}
    if result.get('decision')!='existing_tools':
        return '지원 판단의 추가 질문·검토 또는 미지원 상태가 해결되지 않았습니다.'
    if result.get('missing_conditions') or result.get('missing_actions'):
        return '지원 판단에 미해결 조건이나 부족 기능이 남아 있습니다.'
    if state.get('capability_fingerprint')!=input_fingerprint(state):
        return '요청·자료·도구 목록이 변경됐거나 이전 판정의 유효성을 확인하지 못했습니다. 지원 여부를 다시 확인해주세요.'
    return None


def require_ready(state):
    issue=blocking_issue(state)
    if issue:raise ValueError(issue)


def require_save_ready(state):
    require_ready(state)
    condition=state.get('condition_required') or state.get('condition_enforced')
    verification=state.get('verification_required') or state.get('verification_enforced')
    if condition:
        from .condition_readiness import plan_issues, literal_condition_issues
        ledger=state.get('condition_ledger') or {}
        if ledger.get('decision')!='ready' or literal_condition_issues(state) or plan_issues(ledger,state['draft']):
            raise ValueError('확정 업무 조건이 반영된 설정만 저장할 수 있습니다.')
    if verification and state.get('verification_route')!='continue_review':
        raise ValueError('검증 보류 상태의 초안은 저장할 수 없습니다.')
    if (capability_enforced(state) or condition or verification) and not state.get('confirmed'):
        raise ValueError('사용자 확인을 받은 설정만 저장할 수 있습니다.')


def generation_gate(state):
    return {'capability_generation_route':'recheck' if blocking_issue(state) else 'generate'}
