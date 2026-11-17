"""Prove mandatory literal predicates on each exported row-data path."""


def guarantees_filter(plan, rule):
    from .requirement_coverage import _condition
    expected = _condition(rule)
    actual = [_condition(f) for f in plan.get('filters', [])]
    if plan.get('match', 'all') == 'all':
        return expected in actual
    # An OR only guarantees this predicate when every alternative is that
    # predicate. Single-condition any/all are equivalent.
    return bool(actual) and plan.get('match') == 'any' and all(f == expected for f in actual)


def contains_alternative(plan, rule):
    from .requirement_coverage import _condition
    actual = {_condition(f) for f in plan.get('filters', [])}
    return plan.get('match') == 'any' and len(actual) > 1 and _condition(rule) in actual


def numeric_filter_issues(state, draft):
    from agent_builder.conditions.condition_readiness import compound_filter_state
    found = compound_filter_state(state)
    rules = found['rules']
    if not rules or found['pending'] not in (None, 'match'):
        # While the user is revising the conditions they are not a requirement yet.
        return _filter_issues(state, draft, None)
    if found['mode']:
        return _filter_issues(state, draft, (found['mode'], rules))
    # AND or OR is still being asked: a draft must at least apply every condition one way.
    strict = _filter_issues(state, draft, ('all', rules))
    return strict and _filter_issues(state, draft, ('any', rules)) and strict


def _filter_issues(state, draft, compound):
    from .output_selection_request import literal_numeric_filters, literal_or_filters
    required = [(r, guarantees_filter) for r in literal_numeric_filters(state)]
    required += [(r, contains_alternative) for r in literal_or_filters(state)]
    if compound:
        match, rules = compound
        if match == 'all' or len(rules) < 2:
            required.extend((rule, guarantees_filter) for rule in rules)
        else:
            from .requirement_coverage import _condition

            def matches_group(plan, group):
                return (plan.get('match') == 'any'
                        and {_condition(item) for item in plan.get('filters', [])}
                        == {_condition(item) for item in group})

            required.append((tuple(rules), matches_group))

    if not required:
        return []
    error = ['명시한 숫자 선별 조건이 최종 저장 경로의 모든 행에 적용되지 않습니다. AND/OR 조건과 데이터 연결을 확인하세요.']
    if compound:
        from .requirement_coverage import _condition
        requested = {_condition(rule) for rule in compound[1]}
        plans = [n['settings'].get('execution_plan') or draft.get('execution_plan', {})
                 for n in (draft.get('workflow') or {}).get('nodes', [])
                 if n['tool'] == 'filter_sort' and not n['settings'].get('operation')]
        if not draft.get('workflow'):
            plans = [draft.get('execution_plan') or {}]
        if any(_condition(rule) not in requested for plan in plans for rule in plan.get('filters', [])):
            return error
    if not draft.get('workflow'):
        return [] if all(check(draft.get('execution_plan') or {}, r) for r, check in required) else error
    from .workflow_paths import workflow_paths
    from .content_lineage import content_bindings
    from .requirement_coverage import _arguments
    nodes = {n['id']: n for n in draft['workflow']['nodes']}

    def proven(identifier, output, rule, check, active, seen):
        if identifier not in active or identifier in seen:
            return False
        n = nodes[identifier]; settings = n['settings']; operation = settings.get('operation')
        tool = n['tool']
        if tool == 'filter_sort' and not operation and output == 'table':
            plan = settings.get('execution_plan') or draft['execution_plan']
            if check(plan, rule):
                return True
        # Only row-preserving adapters can carry a proof forward. A new read,
        # aggregate, join, literal payload, or unknown operation is not proof.
        if not operation:
            safe = tool in ('filter_sort', 'write_result', 'export_result')
            if tool in ('condition', 'ask_user', 'finish'):
                safe = output in ('table', 'records')
            if tool == 'calculate_columns':
                safe = (settings.get('table_operation') == 'compute'
                        and settings.get('output_column') not in
                        {item['field'] for item in (rule if isinstance(rule, tuple) else (rule,))})
        else:
            args = _arguments(n)
            safe = (tool == 'filter_sort' and operation == 'select_columns'
                    or tool == 'json_transform' and operation == 'lookup' and args.get('path') == ['rows']
                    or tool == 'export_result' and operation in ('write_csv', 'write_json', 'serialize'))
        if not safe:
            return False
        bindings = content_bindings(n, output)
        return len(bindings) == 1 and proven(bindings[0]['node_id'], bindings[0]['output'], rule, check,
                                             active, seen | {identifier})

    try:
        checked_outputs = 0
        for path, _ in workflow_paths(draft['workflow']):
            active = {n['id'] for n in path}
            endpoints = [(node, None) for node in path if node['tool'] in ('write_result', 'export_result')]
            if compound and not endpoints and path:
                last = path[-1]
                if last['tool'] == 'filter_sort':
                    endpoints = [(last, 'table')]
                elif last['tool'] == 'finish':
                    endpoints = [(last, port) for port in ('table', 'records') if content_bindings(last, port)]
            for node, output in endpoints:
                checked_outputs += 1
                if any(not proven(node['id'], output, rule, check, active, set()) for rule, check in required):
                    return error
        return [] if checked_outputs else error
    except (ValueError, KeyError, TypeError):
        return error
