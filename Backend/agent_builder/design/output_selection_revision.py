"""Apply a proven output-only change to the sealed table work."""
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
    from .export_intent import request_clauses
    from .output_selection_request import _request_turns, output_requirements

    messages = state.get('messages', [])
    last = next((i for i in range(len(messages)-1, -1, -1)
                 if messages[i].get('role') == 'user'), None)
    if last is None:
        return None
    old = state.get('requirement_ledger')
    if (not old or old.get('unresolved')
            or not state.get('requirement_required')
            or state.get('requirement_digest') != plan_digest(old)
            or state.get('requirement_context') != revision_context({**state, 'messages': messages[:last]})):
        return None
    assets = state.get('resources', [])
    specs = [t['spec'] for t in old['tasks']]
    kinds = [s['kind'] for s in specs]
    if (len(assets) != 1 or assets[0].get('kind') != 'table'
            or not set(kinds) <= {'read', 'compute', 'filter', 'sort', 'policy', 'aggregate',
                                  'rename', 'select_fields', 'export'}
            or kinds.count('read') != 1 or kinds.count('export') != 1 or kinds[-1] != 'export'):
        return None

    quote = messages[last]['content']
    source = sum(m.get('role') == 'user' for m in messages[:last])
    match = _NARROW.fullmatch(quote.strip())
    intent = output_requirements(state)
    actual_turn = dict(_request_turns(state)).get(source, '')
    clauses = [(source, clause.strip()) for _, clause in request_clauses(actual_turn) if clause.strip()]
    display_changed = any(clause in intent['display_clauses'] for clause in clauses)
    output_only = (intent['active_from'] == source and not intent['multiple'] and clauses
                   and all(clause in intent['accepted_clauses'] or clause in intent['display_clauses']
                           for clause in clauses))

    plan = deepcopy(old)
    if output_only and intent['cancelled']:
        writer = plan['tasks'].pop()
        if display_changed:
            if kinds.count('select_fields') > 1:
                return None
            if kinds.count('select_fields') == 1:
                selection = plan['tasks'][kinds.index('select_fields')]
            else:
                # Reuse the removed writer's lineage so the preview receives
                # the same final processed rows that previously fed the file.
                selection = writer
                plan['tasks'].append(selection)
            selection.update(source_message=source, quote=quote, description='최종 표시 열 변경',
                             spec={'kind': 'select_fields', 'fields': intent['fields']})
    elif output_only and not display_changed and len(intent['formats']) == 1:
        plan['tasks'][-1].update(source_message=source, quote=quote, description='결과 파일 형식 변경',
                                 spec={'kind': 'export', 'format': next(iter(intent['formats']))})
    elif (match and kinds.count('select_fields') == 1 and kinds[-2] == 'select_fields'
          and specs[-1]['format'] == 'csv' and match['column'] in specs[-2]['fields']):
        plan['tasks'][-2].update(source_message=source, quote=quote, description='최종 출력 열 변경',
                                 spec={'kind': 'select_fields', 'fields': [match['column']]})
    else:
        return None
    # Keep every other task and dependency intact; all current condition,
    # computation, numeric and provenance checks remain mandatory.
    try:
        from .grouped_totals_request import connect_grouped_totals
        plan['tasks'] = connect_grouped_totals(plan['tasks'], state)
        return validate_plan(plan, state)
    except (ValueError, KeyError, TypeError):
        return None
