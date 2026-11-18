"""Compare the complete row-selection expression on every exported data path.

Sequential filters are conjunctions of clauses, not independent witnesses.
Positive AND/OR clauses have a canonical form after redundant supersets are
removed. No truth-table expansion or sampling of live rows is used as proof.
"""
from .content_lineage import content_bindings
from .requirement_coverage import _arguments, _condition
from .workflow_paths import workflow_paths


def clauses(plan):
    atoms = frozenset(_condition(rule) for rule in plan.get('filters', []))
    if not atoms:
        return frozenset()
    mode = plan.get('match', 'all')
    if mode not in ('all', 'any'):
        raise ValueError('Unknown selection connective')
    return frozenset({atoms} if mode == 'any' else (frozenset({atom}) for atom in atoms))


def canonical(expression):
    # In a conjunction, X makes every X-or-Y clause redundant. This also
    # detects narrowing before/after an otherwise exact requested OR.
    return frozenset(clause for clause in expression
                     if not any(other < clause for other in expression))


def filter_path_issues(state, draft, groups):
    error = ['행 선별의 전체 데이터 경로가 요청한 AND/OR 조건과 일치하지 않습니다. 앞뒤 필터의 행 누락과 변환을 확인하세요.']
    expected = canonical(frozenset().union(*(clauses(group) for group in groups)))
    workflow = draft.get('workflow')
    if not workflow:
        return [] if canonical(clauses(draft.get('execution_plan') or {})) == expected else error
    nodes = {node['id']: node for node in workflow['nodes']}
    fields = {rule['field'] for group in groups for rule in group['filters']}
    tables = {asset['id'] for asset in state.get('resources', []) if asset.get('kind') == 'table'}
    slots = (state.get('condition_ledger') or {}).get('slots') or {}
    policies = {key: slot.get('value') for key, slot in slots.items() if slot.get('status') == 'specified'}

    def trace(identifier, output, active, seen):
        if identifier not in active or identifier in seen:
            return None
        node = nodes[identifier]
        tool, settings = node['tool'], node['settings']
        operation = settings.get('operation')
        if tool == 'read_table' and not operation and output == 'table':
            selected = set(settings.get('file_ids') or tables)
            return frozenset() if len(tables) == 1 and selected == tables else None
        local = frozenset()
        removes_duplicates = False
        if not operation:
            safe = tool in ('write_result', 'export_result')
            if tool == 'filter_sort' and output == 'table':
                plan = settings.get('execution_plan') or draft.get('execution_plan') or {}
                local = clauses(plan)
                # An extra node may not silently change the data policies.
                for key, default in (('missing_policy', 'ask'), ('duplicate_policy', 'keep')):
                    policy_key = key.replace('_', '.')
                    actual_policy = plan.get(key, default)
                    neutral_duplicate = key == 'duplicate_policy' and actual_policy == 'keep'
                    if policy_key in policies and actual_policy != policies[policy_key] and not neutral_duplicate:
                        return None
                if 'duplicate.field' in policies and plan.get('duplicate_policy') in ('first', 'last') and plan.get('duplicate_field') != policies['duplicate.field']:
                    return None
                removes_duplicates = plan.get('duplicate_policy') in ('first', 'last')
                safe = True
            elif tool in ('condition', 'ask_user', 'finish'):
                safe = output in ('table', 'records')
            elif tool == 'calculate_columns':
                safe = settings.get('table_operation') == 'compute' and settings.get('output_column') not in fields
        else:
            args = _arguments(node)
            safe = (tool == 'filter_sort' and operation == 'select_columns'
                    or tool == 'json_transform' and operation == 'lookup' and args.get('path') == ['rows']
                    or tool == 'export_result' and operation in ('write_csv', 'write_json', 'serialize'))
        if not safe:
            return None
        bindings = content_bindings(node, output)
        if len(bindings) != 1:
            return None
        parent = bindings[0]
        upstream = trace(parent['node_id'], parent['output'], active, seen | {identifier})
        if upstream is None:
            return None
        combined = upstream | local
        if removes_duplicates and canonical(upstream) != expected:
            return None  # The executor deduplicates BEFORE its local filters; only upstream selection can protect rows.
        return combined

    try:
        checked = 0
        for path, _ in workflow_paths(workflow):
            active = {node['id'] for node in path}
            endpoints = [(node, None) for node in path if node['tool'] in ('write_result', 'export_result')]
            if not endpoints and path:
                last = path[-1]
                if last['tool'] == 'filter_sort':
                    endpoints = [(last, 'table')]
                elif last['tool'] == 'finish':
                    endpoints = [(last, port) for port in ('table', 'records') if content_bindings(last, port)]
            for node, output in endpoints:
                checked += 1
                actual = trace(node['id'], output, active, set())
                if actual is None or canonical(actual) != expected:
                    return error
        return [] if checked else error
    except (ValueError, KeyError, TypeError):
        return error
