"""Preserve sealed table work for a closed, output-only narrowing request."""
import re
from copy import deepcopy


_NARROW = re.compile(
    r'(?P<column>\w+?)(?:\s+(?:열|컬럼))?만(?:을|를)?\s*'
    r'저장\s*해\s*(?:줘|주세요)[.!]?')


def revision_context(state):
    from agent_builder.capabilities.capability_gate import input_fingerprint
    # Meaning is re-evaluated each turn; all source/model/catalog evidence must
    # still match the prefix that produced the sealed ledger.
    return input_fingerprint({**state, 'request_meaning_digest': None})


def narrow_output_revision(state):
    from .requirement_ledger import plan_digest, validate_plan
    messages = state.get('messages', [])
    last = next((i for i in range(len(messages)-1, -1, -1)
                 if messages[i].get('role') == 'user'), None)
    if last is None:
        return None
    match = _NARROW.fullmatch(messages[last]['content'].strip())
    old = state.get('requirement_ledger')
    if (not match or not old or old.get('unresolved')
            or not state.get('requirement_required')
            or state.get('requirement_digest') != plan_digest(old)
            or state.get('requirement_context') != revision_context({**state, 'messages': messages[:last]})):
        return None
    assets = state.get('resources', [])
    specs = [t['spec'] for t in old['tasks']]
    kinds = [s['kind'] for s in specs]
    if (len(assets) != 1 or assets[0].get('kind') != 'table'
            or not set(kinds) <= {'read', 'compute', 'filter', 'sort', 'select_fields', 'export'}
            or any(kinds.count(k) != 1 for k in ('read', 'select_fields', 'export'))
            or kinds[-2:] != ['select_fields', 'export']
            or specs[-1]['format'] != 'csv'
            or match['column'] not in specs[-2]['fields']):
        return None
    plan = deepcopy(old)
    selection = plan['tasks'][-2]
    selection.update(source_message=sum(m.get('role') == 'user' for m in messages[:last]),
                     quote=messages[last]['content'], description='최종 출력 열 변경',
                     spec={'kind': 'select_fields', 'fields': [match['column']]})
    # Keep every other task and dependency intact; all current condition,
    # computation, numeric and provenance checks remain mandatory.
    try:
        return validate_plan(plan, state)
    except (ValueError, KeyError, TypeError):
        return None
