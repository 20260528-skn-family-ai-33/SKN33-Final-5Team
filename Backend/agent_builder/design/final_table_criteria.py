"""Check policy overrides and ordering on the actual final content path."""
from workflow_engine.catalog.canonical import parse_arguments
from workflow_engine.catalog.registry import node_ports


def _lookup_preserves_order(node, args, nodes):
    value = args.get('value', {'$binding': 'value'})
    if not isinstance(value, dict) or set(value) != {'$binding'}:
        return False
    bound = [b for b in node['bindings'] if b['input'] == value['$binding']]
    if len(bound) != 1 or bound[0]['node_id'] not in nodes:
        return False
    source = node_ports(nodes[bound[0]['node_id']])[1].get(bound[0]['output'])
    target = node['settings'].get('output_port')
    if args.get('path') == ['rows']:
        return source == 'table' and target in (None, 'records')
    return args.get('path') == [] and source in ('table', 'records') and target in (None, source)


def _missing_fields(filters, sort, policy):
    fields = ({f['field'] for f in filters if f['operator'] not in ('is_empty', 'not_empty')}
              | {f['compare_field'] for f in filters if f.get('compare_field')}
              | {s['field'] for s in sort})
    # Native exclude without any conditions checks every column.
    return None if policy == 'exclude' and not filters and not sort else fields


def policy_path_conflicts(spec, nodes, requested_transform, draft, confirmed_specs):
    """Policies need a known path, not merely one matching native policy node.

    Explicit row filtering, aggregation and joins can intentionally change row
    counts. An opaque selection or projection cannot stand in for those tasks.
    """
    requested_policies = {}
    for policy in (s for s in confirmed_specs if s['kind'] == 'policy'):
        for key in ('missing_policy', 'duplicate_policy', 'duplicate_field'):
            if policy.get(key) is not None:
                requested_policies.setdefault(key, policy[key])
    missing = requested_policies.get('missing_policy', 'keep')
    conflicts = []
    lookup = {node['id']: node for node in nodes}
    from .requirement_coverage import _same_filters
    filters = [s for s in confirmed_specs if s['kind'] == 'filter']
    orders = [s['sort'] for s in confirmed_specs if s['kind'] == 'sort']
    expected_filters = filters[0]['filters'] if filters else []
    expected_sort = orders[0] if orders else []
    expected_fields = _missing_fields(expected_filters, expected_sort, missing)
    names = {field: field for field in (expected_fields or set())}
    duplicate_field = requested_policies.get('duplicate_field')
    if duplicate_field:
        names[duplicate_field] = duplicate_field
    for spec_filter in filters:
        for condition in spec_filter['filters']:
            names[condition['field']] = condition['field']
            if condition.get('compare_field'):
                names[condition['compare_field']] = condition['compare_field']
    for node in nodes:
        settings = node['settings']
        tool, operation = node['tool'], settings.get('operation')
        if tool in ('aggregate_table', 'join_tables'):
            if not requested_transform(node):
                conflicts.append(node['id'] + '의 행 변환을 요청한 업무로 확인하지 못했습니다.')
            continue
        if not operation:
            if tool == 'filter_sort':
                plan = settings.get('execution_plan') or draft['execution_plan']
                for key in ('missing_policy', 'duplicate_policy'):
                    if plan[key] != requested_policies.get(key, 'keep'):
                        conflicts.append(node['id'] + '의 ' + key + ' 효과를 요청한 정책으로 확인하지 못했습니다.')
                if (plan['duplicate_policy'] in ('first', 'last', 'error')
                        and plan['duplicate_field'] != names.get(duplicate_field, duplicate_field)):
                    conflicts.append(node['id'] + '의 중복 기준 열이 요청한 정책과 다릅니다.')
                def renamed_filters(conditions):
                    return [{**f, 'field': names.get(f['field'], f['field']),
                             'compare_field': names.get(f.get('compare_field'), f.get('compare_field'))} for f in conditions]
                if plan['filters'] and not any(_same_filters(plan['filters'], renamed_filters(f['filters']), plan['match'], f['match']) for f in filters):
                    conflicts.append(node['id'] + '의 행 선별을 요청한 조건으로 확인하지 못했습니다.')
                current_fields = None if expected_fields is None else {names.get(field, field) for field in expected_fields}
                if missing in ('exclude', 'error') and _missing_fields(plan['filters'], plan['sort'], missing) != current_fields:
                    # Native checks duplicates, then missing values, then filters.
                    # Splitting the requested fields across row-removing nodes
                    # can hide an error or remove rows the full plan preserves.
                    conflicts.append(node['id'] + '의 빈값 검사 열이 요청 전체 조건과 다릅니다.')
            if tool == 'calculate_columns':
                if settings.get('table_operation') == 'rename':
                    names, _ = _renamed_sort_fields(names, {settings.get('rename_from'): settings.get('rename_to')})
                elif settings.get('table_operation') == 'compute' and settings.get('output_column') in names:
                    names[settings['output_column']] = settings['output_column']
            if tool not in ('read_table', 'read_document', 'extract_information', 'filter_sort',
                            'calculate_columns', 'write_result', 'export_result', 'ask_user', 'finish'):
                conflicts.append(node['id'] + '에서 요청한 정책의 보존을 확인하지 못했습니다.')
            continue
        args = parse_arguments(settings)
        known = False
        if tool == 'filter_sort':
            known = operation in ('handle_missing', 'deduplicate', 'filter', 'sort', 'select_columns')
            if operation == 'handle_missing':
                action = args.get('action')
                if action not in ('keep', 'drop_rows', 'fill', 'error'):
                    known = False
                requested_fill = action == 'fill' and requested_transform(node)
                # The action name alone proves neither the inspected columns
                # nor native missing-value semantics. Only a no-op or an
                # independently requested fill has a preservation proof here.
                if action != 'keep' and not requested_fill:
                    conflicts.append(node['id'] + '의 빈값 변환을 요청한 정책·업무로 확인하지 못했습니다.')
            if operation == 'filter':
                # Raw predicates can remove the very row on which a later
                # missing/duplicate error or first/last policy should operate.
                conflicts.append(node['id'] + '의 행 선별에서 요청한 정책의 보존을 확인하지 못했습니다.')
            if operation == 'deduplicate':
                # Canonical JSON-key equality differs from native str(value)
                # equality for nested values. Even the same field/first rule
                # can remove a distinct native group, or hide a missing error.
                conflicts.append(node['id'] + '의 중복 제거에서 요청한 정책의 보존을 확인하지 못했습니다.')
        elif tool == 'calculate_columns':
            known = operation in ('compute', 'calculate_records', 'rename_columns', 'parse_dates')
            if operation == 'rename_columns':
                mapping = args.get('mapping')
                if isinstance(mapping, dict) and all(isinstance(k, str) and isinstance(v, str) for k,v in mapping.items()) and '$binding' not in mapping:
                    names, _ = _renamed_sort_fields(names, mapping)
                else:
                    known = False
            elif operation in ('compute', 'calculate_records') and isinstance(args.get('output_column'), str) and args['output_column'] in names:
                names[args['output_column']] = args['output_column']
        elif tool == 'json_transform' and operation == 'lookup':
            known = _lookup_preserves_order(node, args, lookup)
        elif tool in ('read_table', 'read_document'):
            known = True  # These establish rows rather than transform earlier rows.
        elif tool == 'export_result':
            known = operation in ('write_json', 'write_csv', 'serialize')
        elif tool == 'create_spreadsheet':
            known = operation == 'create' and requested_transform(node)
        if not known:
            conflicts.append(node['id'] + '에서 요청한 정책의 보존을 확인하지 못했습니다.')
    return conflicts


def _renamed_sort_fields(names, mapping):
    renamed = {original: mapping.get(current, current) for original, current in names.items()}
    reused = {original for original in names if original in mapping.values() and renamed[original] != original}
    for original in reused:
        renamed[original] = original
    return renamed, bool(reused)


def final_sort_matches(expected, draft, path, active):
    """Keep a sort proof only across operations that preserve its keys and order.

    A later native sort replaces the earlier proof. Canonical sort uses raw
    Python comparisons, unlike the native numeric/text/missing-value contract,
    so even equal-looking keys cannot be assumed equivalent. A later native sort
    can establish the requested order again after an otherwise unknown transform.
    """
    if not active <= {node['id'] for node in path}:
        return False
    guaranteed = False
    nodes = {node['id']: node for node in path}
    names = {item['field']: item['field'] for item in expected}
    for node in path:
        if node['id'] not in active:
            continue
        tool, settings = node['tool'], node['settings']
        operation = settings.get('operation')
        fields = set(names.values())
        if not operation:
            if tool == 'filter_sort':
                order = (settings.get('execution_plan') or draft['execution_plan'])['sort']
                if order:
                    if order == expected:
                        # A requested name can be reused after rename/compute.
                        # The current literal key takes precedence over its old alias.
                        guaranteed = True
                        names = {item['field']: item['field'] for item in expected}
                    else:
                        guaranteed = order == [{**item, 'field': names[item['field']]} for item in expected]
            elif tool == 'calculate_columns':
                kind = settings.get('table_operation')
                if kind == 'rename':
                    names, reused = _renamed_sort_fields(names, {settings.get('rename_from'): settings.get('rename_to')})
                    if reused:
                        guaranteed = False
                elif kind == 'parse_dates' and fields & set(settings.get('fields') or []):
                    guaranteed = False
                elif kind == 'compute' and settings.get('output_column') in names:
                    # Native compute cannot overwrite an existing key, but can
                    # reintroduce the requested name after that key was renamed.
                    guaranteed = False
                    names[settings['output_column']] = settings['output_column']
                elif kind not in ('compute', 'parse_dates'):
                    guaranteed = False
            elif tool not in ('write_result', 'export_result', 'ask_user', 'finish'):
                guaranteed = False
            continue
        args = parse_arguments(settings)
        if tool == 'filter_sort':
            if operation == 'sort':
                guaranteed = False
            elif operation == 'handle_missing':
                action, columns = args.get('action'), args.get('columns')
                if action == 'fill':
                    if not isinstance(columns, list) or any(not isinstance(c, str) for c in columns) or fields & set(columns):
                        guaranteed = False
                elif action not in ('keep', 'drop_rows', 'error'):
                    guaranteed = False
            elif operation not in ('filter', 'select_columns', 'deduplicate'):
                guaranteed = False
        elif tool == 'calculate_columns':
            if operation == 'rename_columns':
                mapping = args.get('mapping') or {}
                if (not isinstance(mapping, dict) or set(mapping) == {'$binding'}
                        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items())):
                    guaranteed = False
                else:
                    names, reused = _renamed_sort_fields(names, mapping)
                    if reused:
                        guaranteed = False
            elif operation == 'parse_dates':
                columns = args.get('columns')
                if not isinstance(columns, list) or any(not isinstance(c, str) for c in columns) or fields & set(columns):
                    guaranteed = False
            elif operation in ('compute', 'calculate_records'):
                if not isinstance(args.get('output_column'), str) or args['output_column'] in fields | set(names):
                    guaranteed = False
                if isinstance(args.get('output_column'), str) and args['output_column'] in names:
                    names[args['output_column']] = args['output_column']
            else:
                guaranteed = False
        elif tool == 'json_transform' and operation == 'lookup':
            # Only actual row extraction or a typed identity preserves order.
            # A table envelope or a type declaration alone is not evidence.
            if not _lookup_preserves_order(node, args, nodes):
                guaranteed = False
        elif tool == 'export_result' and operation in ('write_json', 'write_csv', 'serialize'):
            pass
        else:
            guaranteed = False
    return guaranteed
