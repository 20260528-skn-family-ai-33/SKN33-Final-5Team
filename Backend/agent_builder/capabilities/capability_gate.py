"""Persisted capability gate shared by planning, confirmation and saving."""
import hashlib
import json
import os
from workflow_engine.catalog.registry import TOOLS
from agent_builder.capabilities.capability_judgement import APP_CAPABILITIES, delivery_conflict


def capability_enforced(state):
    # Legacy enforce checkpoints contain an assessment but no explicit flag.
    return bool(state.get('capability_enforced') or state.get('capability_assessment'))


def _delivery_issue(state, tools):
    directive = delivery_conflict(state, tools)
    if not directive:
        return None
    if directive['intent'] == 'cancel_delivery':
        return '사용자가 취소한 발송 단계가 남아 있습니다.'
    return '사용자가 유지한 발송 단계가 빠졌습니다.'


def draft_delivery_issue(state, draft):
    if not capability_enforced(state):
        return None
    nodes = (draft.get('workflow') or {}).get('nodes', [])
    return _delivery_issue(state, {node['tool'] for node in nodes})


CAPABILITY_POLICY = 'a4b.capability.v2.turn-evidence'


def _selected_model(state, model):
    if model and state.get('credential_id') == 'local-model' and os.getenv('A4B_MAIN_PROVIDER') == 'ollama':
        from infrastructure.llm.model_aliases import resolve_ollama_model
        return resolve_ollama_model(model)
    return model


def turn_evidence(state):
    """Use only evidence attached to user turns in this transcript or prefix."""
    messages = state.get('messages', [])
    turns = {i for i, message in enumerate(messages) if message.get('role') == 'user'}
    clock = state.get('temporal_context') or {}
    channels = ('capability_answers', 'condition_answers', 'requirement_answers')
    return {
        'model': _selected_model(state, state.get('model')), 'user_timezone': state.get('user_timezone'),
        'anchors': clock.get('user_message_anchors', [])[:len(turns)],
        'answers': {channel: [event for event in state.get(channel, []) if event.get('turn') in turns]
                    for channel in channels},
    }


def input_fingerprint(state):
    payload={'users':[m for m in state.get('messages',[]) if m.get('role')=='user'],
             'request_meaning_digest':state.get('request_meaning_digest'),
             'turn_evidence':turn_evidence(state),'policy':CAPABILITY_POLICY,
             'capability_model':_selected_model(state, os.getenv('A4B_CAPABILITY_MODEL') or state.get('model')),
             'resources':state.get('resources',[]),'source_health':state.get('source_health',{}),
             'tools':TOOLS,'app_capabilities':APP_CAPABILITIES}
    if state.get('execution_mode'):payload['execution_mode']=state['execution_mode']
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def blocking_issue(state):
    if not capability_enforced(state):return None
    from agent_builder.conditions.condition_readiness import input_issues
    if input_issues(state):return '입력 자료·조회 상태의 오류를 해결한 뒤 지원 여부를 다시 확인해주세요.'
    result=state.get('capability_assessment') or {}
    if result.get('decision')!='existing_tools':
        return '지원 판단의 추가 질문·검토 또는 미지원 상태가 해결되지 않았습니다.'
    if result.get('missing_conditions') or result.get('missing_actions'):
        return '지원 판단에 미해결 조건이나 부족 기능이 남아 있습니다.'
    if state.get('capability_fingerprint')!=input_fingerprint(state):
        return '요청·자료·도구 목록이 변경됐거나 이전 판정의 유효성을 확인하지 못했습니다. 지원 여부를 다시 확인해주세요.'
    return _delivery_issue(state, result.get('tool_ids', []))


def require_ready(state):
    issue=blocking_issue(state)
    if issue:raise ValueError(issue)
    if state.get('requirement_required'):
        from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest, validate_plan
        ledger = state.get('requirement_ledger')
        if (not ledger or not ledger.get('tasks') or ledger.get('unresolved')
                or state.get('requirement_fingerprint') != ledger_fingerprint(state)
                or state.get('requirement_digest') != plan_digest(ledger)):
            raise ValueError('필수 업무 조건을 확인한 뒤 워크플로우를 생성할 수 있습니다.')
        validate_plan(ledger, state)


def require_save_ready(state):
    require_ready(state)
    from agent_builder.conditions.clarification_core import open_required
    if open_required(state):
        raise ValueError('답하지 않은 확인 질문이 남아 있어 저장할 수 없습니다.')
    issue = draft_delivery_issue(state, state.get('draft') or {})
    if issue:
        raise ValueError(issue)
    from agent_builder.design.requirement_coverage import requirement_issues
    missing_requirements=requirement_issues(state,state.get('draft') or {})
    if missing_requirements:raise ValueError(' / '.join(missing_requirements))
    from agent_builder.design.processing_guard import processing_issues
    missing_processing=processing_issues(state,state.get('draft') or {})
    if missing_processing:raise ValueError(missing_processing[0])
    from agent_builder.design.name_policy import invalid_workflow_name
    if invalid_workflow_name((state.get('draft') or {}).get('name')):
        raise ValueError('도구 식별자만으로는 저장할 수 없습니다. 업무 이름을 확인해주세요.')
    condition=state.get('condition_required') or state.get('condition_enforced')
    verification=state.get('verification_required') or state.get('verification_enforced')
    if condition:
        from agent_builder.conditions.condition_readiness import plan_issues, literal_condition_issues
        from agent_builder.design.planner_constraints import table_tool_issues
        ledger=state.get('condition_ledger') or {}
        if (ledger.get('decision') != 'ready' or literal_condition_issues(state)
                or plan_issues(ledger, state['draft'], state=state) or table_tool_issues(state)):
            raise ValueError('확정 업무 조건이 반영된 설정만 저장할 수 있습니다.')
    if verification and state.get('verification_route')!='continue_review':
        raise ValueError('검증 보류 상태의 초안은 저장할 수 없습니다.')
    if (capability_enforced(state) or condition or verification) and not state.get('confirmed'):
        raise ValueError('사용자 확인을 받은 설정만 저장할 수 있습니다.')


def generation_gate(state):
    return {'capability_generation_route':'recheck' if blocking_issue(state) else 'generate'}
