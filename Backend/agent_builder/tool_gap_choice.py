"""Explicit user intent, persisted with session CAS; no LLM or execution side effects."""
from .tool_gap import describe_gap
from .storage import uid, now

PROTOCOL = 'a4b.tool-gap-choice.v1'
CHOICES = ('create_tool', 'redesign_workflow', 'defer')


def current_selection(state, session):
    gap = describe_gap(state, session['id'])
    if gap is None:
        return None
    events = session.get('tool_gap_choice_events', [])
    event = next((e for e in reversed(events) if e['gap_id'] == gap['gap_id']), None)
    # Changes outside this interaction invalidate intent, even if the same text later returns.
    if event and event['result_revision'] != session['revision'] and not (
        session.get('tool_gap_interaction_revision')==session['revision'] and
        session.get('tool_gap_interaction_decision_id')==event['id']):
        event = None
    return gap, event


def present_choice(state, session):
    current = current_selection(state, session)
    if current is None:
        return None
    gap, event = current
    choice = event['choice'] if event else None
    return {'protocol': PROTOCOL, 'gap_id': gap['gap_id'], 'revision': session['revision'],
            'state_fingerprint': gap['state_fingerprint'], 'capability_status': gap['capability_status'],
            'status': {'create_tool': 'creation_requested', 'redesign_workflow': 'redesign_requested',
                       'defer': 'deferred'}.get(choice, 'awaiting_choice'),
            'prompt': '현재 도구로는 요청한 기능을 처리하기 어려울 수 있습니다. 새 도구 제작과 검증에 시간이 더 걸릴 수 있습니다. 새 도구 제작을 진행할까요, 아니면 기존 도구로 워크플로우를 다시 설계할까요?',
            'missing_actions': gap['reported_missing_actions'],
            'condition_review': gap.get('condition_review'),
            'options': [{'id': 'create_tool', 'label': '새 도구 제작'},
                        {'id': 'redesign_workflow', 'label': '워크플로우 재설계'},
                        {'id': 'defer', 'label': '나중에 결정'}],
            'selection': event,
            'next_action': {'create_tool': 'review_spec_then_generate',
                            'redesign_workflow': 'propose_existing_tool_alternative',
                            'defer': 'wait_for_user'}.get(choice, 'wait_for_user'),
            'requirement_changes_approved': False, 'workflow_execution_allowed': False,
            'automatic_registration': False}


def choose(storage, state, session, data):
    revision = data.get('revision')
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError('현재 대화 버전을 확인하세요.')
    choice = data.get('choice')
    if not isinstance(choice, str) or choice not in CHOICES:
        raise ValueError('새 도구 제작·워크플로우 재설계·나중에 결정 중 선택하세요.')
    request_id = data.get('request_id')
    import uuid
    try:
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError('선택 요청의 고유 ID를 확인하세요.')
    current = current_selection(state, session)
    if current is None:
        raise ValueError('현재 대화에 선택할 도구 부족 요청이 없습니다.')
    gap, _ = current
    if data.get('gap_id') != gap['gap_id'] or data.get('state_fingerprint') != gap['state_fingerprint']:
        raise ValueError('요청·자료·도구가 변경됐습니다. 현재 선택지를 다시 확인하세요.')
    events = session.get('tool_gap_choice_events', [])
    duplicate = next((e for e in events if e['request_id'] == request_id), None)
    if duplicate:
        if (duplicate['choice'] != choice or duplicate['gap_id'] != gap['gap_id'] or
                duplicate['input_revision'] != revision or duplicate['result_revision'] != session['revision']):
            raise ValueError('이미 처리됐거나 변경된 선택 요청입니다. 현재 대화를 다시 확인하세요.')
        return session  # Exact retry of latest choice; never start another job.
    if session['revision'] != revision:
        raise ValueError('대화 버전이 변경됐습니다. 현재 선택지를 다시 확인하세요.')
    event = {'id': uid(), 'request_id': request_id, 'gap_id': gap['gap_id'],
             'state_fingerprint': gap['state_fingerprint'], 'choice': choice,
             'input_revision': revision, 'result_revision': revision + 1, 'created_at': now()}
    updated = {**session, 'tool_gap_choice_events': events + [event]}
    return storage.update_session(updated, revision)


def creation_intent(state, session):
    value = present_choice(state, session)
    if value is None or value['status'] != 'creation_requested':
        return None
    return {'protocol': PROTOCOL, 'decision_id': value['selection']['id'],
            'choice': 'create_tool', 'gap_id': value['gap_id'],
            'state_fingerprint': value['state_fingerprint'], 'revision': value['revision'],
            'scope': 'code_generation_and_lab_tests_only', 'workflow_execution_allowed': False}


def redesign_context(state, session):
    value = present_choice(state, session)
    if value is None or value['status'] != 'redesign_requested':
        return None
    from copy import deepcopy
    return {'original_user_messages': deepcopy([m for m in state.get('messages', []) if m.get('role') == 'user']),
            'condition_ledger': deepcopy(state.get('condition_ledger')),
            'condition_review': deepcopy(value.get('condition_review')),
            'draft': deepcopy(state.get('draft')), 'missing_actions': value['missing_actions'],
            'required_next_step': '기존 도구 대안과 변경점을 제시한 뒤 사용자 확인', 'requirement_changes_approved': False}
