"""Recover a previously ready draft from checkpoints after a legacy edit failure."""
from collections import OrderedDict
from copy import deepcopy
from threading import Lock

MEANING_FIELDS = ('request_meaning', 'request_meaning_fingerprint', 'request_meaning_digest',
                  'request_meaning_review', 'request_meaning_model_calls')

# Session polls re-read the same checkpoint many times. A checkpoint is immutable once
# written, so the approval snapshot found for it (or its absence) can be reused until a
# new checkpoint is written for the thread.
_CACHE_SIZE = 256
_found = OrderedDict()
_lock = Lock()


def _resources(value):
    return [(r['id'], str(r.get('version') or r.get('hash') or '1')) for r in value.get('resources', [])]


def _approved_snapshot(state, graph, config):
    for snapshot in graph.get_state_history(config, limit=40):
        previous = snapshot.values or {}
        draft = previous.get('draft')
        if (previous.get('status') != 'ready' or 'await_approval' not in snapshot.next
                or previous.get('issues') or not draft or not draft.get('workflow')
                or _resources(previous) != _resources(state)):
            continue
        return {key: previous.get(key) for key in ('draft', *MEANING_FIELDS)}
    return None


def retained_design(state, graph, config, checkpoint_id=None):
    if state.get('draft') or not (state.get('requirement_failure') or state.get('system_failure') or {}).get('retryable'):
        return state
    key = (config.get('configurable', {}).get('thread_id'), checkpoint_id) if checkpoint_id else None
    with _lock:
        cached = key in _found if key else False
        if cached:
            _found.move_to_end(key)
            approved = _found[key]
    if not cached:
        approved = _approved_snapshot(state, graph, config)
        if key:
            with _lock:
                _found[key] = approved
                while len(_found) > _CACHE_SIZE:
                    _found.popitem(last=False)
    if not approved:
        return state
    context = ({field: deepcopy(approved[field]) for field in MEANING_FIELDS}
               if not state.get('request_meaning') else {})
    return {**state, **context, 'draft': deepcopy(approved['draft'])}
