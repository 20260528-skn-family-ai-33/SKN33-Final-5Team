"""Recover a previously ready draft from checkpoints after a legacy edit failure."""
from copy import deepcopy

MEANING_FIELDS = ('request_meaning', 'request_meaning_fingerprint', 'request_meaning_digest',
                  'request_meaning_review', 'request_meaning_model_calls')


def retained_design(state, graph, config):
    if state.get('draft') or not (state.get('requirement_failure') or state.get('system_failure') or {}).get('retryable'):
        return state
    def resources(value):
        return [(r['id'], str(r.get('version') or r.get('hash') or '1')) for r in value.get('resources', [])]
    for snapshot in graph.get_state_history(config, limit=40):
        previous = snapshot.values or {}
        draft = previous.get('draft')
        if (previous.get('status') != 'ready' or 'await_approval' not in snapshot.next
                or previous.get('issues') or not draft or not draft.get('workflow')
                or resources(previous) != resources(state)):
            continue
        context = ({key: deepcopy(previous.get(key)) for key in MEANING_FIELDS}
                   if not state.get('request_meaning') else {})
        return {**state, **context, 'draft': deepcopy(draft)}
    return state
