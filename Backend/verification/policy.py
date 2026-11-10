"""The current server policy takes precedence over persisted reviewer flags."""
import os


def verifier_mode(mode=None):
    setting = mode or os.getenv('A4B_VERIFIER_MODE', 'off')
    if setting not in ('off', 'shadow', 'defer', 'enforce'):
        raise ValueError('A4B_VERIFIER_MODE는 off/shadow/defer/enforce이어야 합니다.')
    return setting


def disabled_review_update(mode=None, state=None):
    # Keep historical verdicts and messages. Recompute active errors rather than
    # dropping all issues (which could hide a real native validation failure).
    if verifier_mode(mode) == 'off':
        update = dict(verification_route=None, verification_enforced=False,
                      verification_required=False)
        # Also recover checkpoints already visited by the former off policy,
        # which cleared flags but left the review's active error marker behind.
        previous_route = (state.get('verification') or {}).get('route') if state else None
        stale_error = bool(previous_route and
                           '검증 경로: ' + previous_route in state.get('issues', []))
        if state and state.get('draft') and (
                state.get('verification_required') or state.get('verification_enforced')
                or state.get('verification_route') is not None or stale_error):
            from agent_builder.design.nodes.validation import validate_node
            update.update(validate_node({**state, **update}))
        return update
    return {}
