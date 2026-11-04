"""Reject a known degenerate repair, without treating suggested IDs as a whitelist.

This checks only the loss of all processing. It cannot prove that a remaining
processing node implements every requested operation, condition or output.
"""

IO_ONLY = frozenset({'read_table', 'read_document', 'write_result', 'export_result', 'finish'})


def processing_issues(state, draft):
    from agent_builder.capabilities.capability_gate import capability_enforced, blocking_issue
    from workflow_engine.catalog.registry import TOOLS

    workflow = draft.get('workflow')
    if not workflow or draft.get('questions') or draft.get('unsupported'):
        return []
    if not capability_enforced(state) or blocking_issue(state):
        return []
    assessment = state.get('capability_assessment') or {}
    processing = set(assessment.get('tool_ids', [])) - IO_ONLY
    nodes = workflow.get('nodes', [])
    if not processing or not nodes:
        return []
    # An operation may have a different contract from the native tool sharing
    # its ID. Leave those nodes to operation validation and semantic review.
    if any(n['tool'] not in IO_ONLY or n.get('settings', {}).get('operation') for n in nodes):
        return []
    names = {t['id']: t['name'] for t in TOOLS}
    expected = '·'.join(names.get(t, t) for t in sorted(processing))
    return [f'지원 판단에 {expected} 처리가 포함됐지만 초안에는 읽기·저장 단계만 있습니다. '
            '원래 요청을 대조하여 실제 처리 단계를 구성하거나 지원 판단을 다시 검토하세요. '
            '오류를 없애기 위해 요청한 업무를 삭제해서는 안 됩니다.']
