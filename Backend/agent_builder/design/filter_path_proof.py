"""Compare the complete row-selection expression on every exported data path.

Sequential filters are conjunctions of clauses, not independent witnesses.
Positive AND/OR clauses have a canonical form after redundant supersets are
removed. No truth-table expansion or sampling of live rows is used as proof.
"""
from .condition_requirements import ordered_row_steps
from .content_lineage import content_bindings
from .requirement_coverage import _arguments, _condition
from .workflow_paths import workflow_paths


_UNARY_OPERATORS = {'is_empty', 'not_empty'}


def _missing_operands(rule):
    if rule['operator'] in _UNARY_OPERATORS:
        return []
    fields = [rule['field']]
    if rule.get('compare_field'):
        fields.append(rule['compare_field'])
    return fields


def clauses(plan, *, missing_policy):
    rules = plan.get('filters', [])
    mode = plan.get('match', 'all')
    if mode not in ('all', 'any'):
        raise ValueError('Unknown selection connective')
    groups = [rules] if rules and mode == 'any' else [[rule] for rule in rules]
    expression = set()
    for group in groups:
        matches = frozenset(('matches', _condition(rule)) for rule in group)
        if missing_policy == 'keep':
            # An OR also passes when every comparison was skipped for missing operands.
            # Distribute that conjunction across the OR before removing redundant clauses.
            for rule in group:
                expression.add(matches | {('missing', field) for field in _missing_operands(rule)})
        else:
            expression.add(matches)
    return frozenset(expression)


def canonical(expression):
    # In a conjunction, X makes every X-or-Y clause redundant. This also
    # detects narrowing before/after an otherwise exact requested OR.
    return frozenset(clause for clause in expression
                     if not any(other < clause for other in expression))


def filter_path_issues(state, draft, groups):
    error = ['행 선별의 전체 데이터 경로가 요청한 AND/OR 조건과 일치하지 않습니다. 앞뒤 필터의 행 누락과 변환을 확인하세요.']
    fields = {rule['field'] for group in groups for rule in group['filters']}
    tables = {asset['id'] for asset in state.get('resources', []) if asset.get('kind') == 'table'}
    slots = (state.get('condition_ledger') or {}).get('slots') or {}
    policies = {key: slot.get('value') for key, slot in slots.items() if slot.get('status') == 'specified'}
    default_plan = draft.get('execution_plan') or {}
    missing_policies = (policies['missing.policy'],) if 'missing.policy' in policies else ('ask', 'keep')
    expected = {canonical(frozenset().union(*(clauses(group, missing_policy=policy) for group in groups)))
                for policy in missing_policies}
    workflow = draft.get('workflow')
    if not workflow:
        actual = clauses(default_plan, missing_policy=default_plan.get('missing_policy', 'ask'))
        return [] if canonical(actual) in expected else error
    nodes = {node['id']: node for node in workflow['nodes']}
    dedup_inputs = {frozenset()} if ordered_row_steps(state) == ('policy', 'filter') else expected

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
                plan = settings.get('execution_plan') or default_plan
                local = clauses(plan, missing_policy=plan.get('missing_policy', 'ask'))
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
        if removes_duplicates and canonical(upstream) not in dedup_inputs:
            return None  # Native deduplication runs before the local predicates.
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
                if actual is None or canonical(actual) not in expected:
                    return error
        return [] if checked else error
    except (ValueError, KeyError, TypeError):
        return error
