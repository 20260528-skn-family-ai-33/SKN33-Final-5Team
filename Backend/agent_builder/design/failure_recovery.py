"""Use the same recovery view for rendering old checkpoints and retry dispatch."""

PROJECTED_FAILURE_KINDS = frozenset({'execution_mode_questions', 'question_language_error'})


def pending_resource_recovery(state):
    """A file attachment may withdraw a verdict without consuming its request."""
    if (state.get('status') != 'needs_clarification' or state.get('pending_questions')
            or (state.get('draft') or {}).get('questions')):
        return None
    return next((failure for key in ('system_failure', 'requirement_failure')
                 if isinstance(failure := state.get(key), dict)
                 and failure.get('recovery_pending') is True and failure.get('retryable') is True), None)


def current_failure(state):
    if state.get('status') == 'validation_failed':
        return state.get('system_failure') or state.get('requirement_failure')
    return pending_resource_recovery(state)


def project_recovery_questions(state):
    from .execution_mode import project_execution_questions
    from .question_language import normalize_question_language
    # These markers describe a read-time projection, not a persisted processing
    # failure. Recompute them so a later, valid question cannot inherit one.
    failure = state.get('requirement_failure') or {}
    if failure.get('kind') in PROJECTED_FAILURE_KINDS:
        state = {**state, 'requirement_failure': None}
    return normalize_question_language(project_execution_questions(state), report_failure=not current_failure(state))


def projected_recovery_failure(state):
    failure = project_recovery_questions(state).get('requirement_failure') or {}
    return failure if failure.get('kind') in PROJECTED_FAILURE_KINDS else None


def normalize_active_failures(state):
    """Diagnose old metadata without exposing exception text or reviving history."""
    from .failure_notice import requirement_failure_details
    result = dict(state)
    for key in ('system_failure', 'requirement_failure'):
        failure = state.get(key)
        if not isinstance(failure, dict) or not failure:
            continue
        if (state.get('status') != 'validation_failed' and failure is not pending_resource_recovery(state)
                or failure.get('kind') in PROJECTED_FAILURE_KINDS):
            continue
        failure = dict(failure)
        attempt = failure.get('attempt', 1)
        if type(attempt) is not int or attempt < 1:
            attempt = 1
        details = requirement_failure_details(
            failure.get('failure_kind', 'system_error'), failure.get('error_type'),
            attempt=attempt, stage=failure.get('stage') or 'requirements',
            http_status=failure.get('http_status'),
            error_code=failure.get('error_code') or failure.get('reason_code'))
        if (details['reason_code'] == 'processing_error' and key == 'requirement_failure'
                and failure.get('stage', 'requirements') == 'requirements'):
            # Older checkpoints store the diagnosis only on the failed trace.
            # Use it before filling generic prose, which presentation treats as
            # an already diagnosed failure. Explicit modern metadata wins.
            latest = next((step for step in reversed(state.get('trace', []))
                           if step.get('node') == 'requirements'
                           and step.get('label') != '단계 소요 시간'), {})
            if latest.get('status') == 'failed':
                details = requirement_failure_details(
                    latest.get('failure_kind', 'system_error'), latest.get('error_type'),
                    attempt=attempt, stage='requirements', http_status=latest.get('http_status'),
                    error_code=latest.get('error_code') or latest.get('reason_code'))
        # Old explicit retry permissions remain usable when the old record
        # cannot be classified. A known diagnosis follows today's policy,
        # independently of whether explanatory text was already stored.
        known = details['reason_code'] != 'processing_error'
        retryable = (details['retryable'] and failure.get('retryable') is not False if known
                     else failure.get('retryable') is True)
        failure = {**failure, **(details if known else {
                       name: value for name, value in details.items() if name not in failure}),
                   'reason': failure.get('reason') or details['reason'],
                   'action': failure.get('action') or details['action'],
                   'retryable': retryable and attempt < 2}
        if attempt > 1:
            # Migrate the exact prefix emitted by older versions. The repeat
            # notice is structured independently of the user's next action.
            prefix = f'같은 조건의 {attempt}번째 시도도 실패했습니다. '
            action = failure.get('action')
            if isinstance(action, str) and action.startswith(prefix):
                failure['action'] = action[len(prefix):]
            failure.setdefault('attempt_notice', requirement_failure_details(
                'system_error', attempt=attempt)['attempt_notice'])
            failure['retryable'] = False
        result[key] = failure
    return result
