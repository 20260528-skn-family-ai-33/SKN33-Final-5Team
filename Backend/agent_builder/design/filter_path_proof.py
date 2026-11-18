"""Compare row selection on every path to a downloaded file or displayed table.

Sequential filters are conjunctions of clauses, not independent witnesses.
Positive AND/OR clauses have a canonical form after redundant supersets are
removed. No truth-table expansion or sampling of live rows is used as proof.
"""
import re

from contracts.table_selection import comparison_operand_fields, missing_check_fields
from agent_builder.capabilities.capability_judgement import active_request_messages
from agent_builder.conditions.filter_edits import in_metadata_text, in_quoted_text
from .condition_requirements import ordered_row_steps
from .content_lineage import content_bindings
from .requirement_coverage import _arguments, _condition
from .workflow_paths import workflow_paths
from workflow_engine.execution_path import artifact_ids_on_path, artifact_origin, result_rows_endpoint


def _workbook_requested(state):
    from .explicit_table_criteria import explicit_criteria
    criteria = explicit_criteria(state) or {}
    if criteria.get('output_format'):
        return criteria['output_format'] == 'xlsx'
    for message in reversed(active_request_messages(state)):
        if message.get('role') != 'user':
            continue
        formats = {value.casefold() for value in re.findall(
            r'(?<![A-Za-z0-9_])(xlsx|excel|엑셀|csv|json)\s*(?:파일)?\s*(?:으로|로|을|를)?\s*(?:저장|내보내|만들|생성|출력)',
            message['content'], re.I)}
        if formats:
            return formats <= {'xlsx', 'excel', '엑셀'}
    return False


def _workbook_source(node):
    from workflow_engine.executors.canonical_operation import validate_settings
    spec, args = validate_settings(node)
    sheets = args.get('sheets')
    if spec['id'] != 'create' or not isinstance(sheets, list) or len(sheets) != 1:
        return None
    table = sheets[0].get('table')
    if not isinstance(table, dict) or set(table) != {'$binding'}:
        return None
    return next((binding for binding in node['bindings']
                 if binding['input'] == table['$binding'] and binding['output'] == 'table'), None)


def filter_result_endpoints(state, workflow, path):
    """Check explicit writers, the actual downloadable artifact, and the displayed rows."""
    by_id = {node['id']: node for node in path}
    writers = [node for node in path if node['tool'] in ('write_result', 'export_result')]
    endpoints = [(node, None) for node in writers]
    artifacts = artifact_ids_on_path(path)
    if artifacts:
        origin = by_id.get(artifact_origin(workflow, artifacts[-1]))
        if origin is None:
            raise ValueError('Cannot establish the downloaded artifact source')
        if origin not in writers:
            source = _workbook_source(origin) if origin['tool'] == 'create_spreadsheet' and _workbook_requested(state) else None
            if source is None:
                raise ValueError('Cannot prove row selection on the downloaded artifact')
            endpoints.append((by_id[source['node_id']], source['output']))
    if preview := result_rows_endpoint(path):
        endpoints.append(preview)
    return endpoints, bool(artifacts)


def _presence_clauses(fields):
    return frozenset(frozenset({('present', field)}) for field in fields)


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
                expression.add(matches | {('missing', field) for field in comparison_operand_fields(rule)})
        else:
            expression.add(matches)
    if missing_policy == 'exclude':
        checked = missing_check_fields(rules, plan.get('sort', []), missing_policy)
        if checked is None:
            # A policy-only stage also inspects any extra columns supplied by a replacement run file.
            raise ValueError('Column scope depends on the runtime table schema')
        expression.update(_presence_clauses(checked))
    return frozenset(expression)


def canonical(expression):
    # In a conjunction, X makes every X-or-Y clause redundant. This also
    # detects narrowing before/after an otherwise exact requested OR.
    return frozenset(clause for clause in expression
                     if not any(other < clause for other in expression))


def _first_error_checks(groups):
    """The input row scope at the first missing-value check for each operand."""
    upstream = frozenset()
    checks = {}
    for group in groups:
        for field in missing_check_fields(group['filters'], [], 'error'):
            checks.setdefault(field, upstream)
        upstream = canonical(upstream | clauses(group, missing_policy='error'))
    return checks


_SORT_DIRECTION = r'(?:오름차순|내림차순|ascending|descending)'
_SORT_NEGATED = re.compile(
    r'\s*(?:은|는|을|를)?\s*(?:(?:하)?지\s*(?:는|도)?\s*(?:말|마|않)|없이|말고|취소|안\s*(?:해|함)|할\s*필요(?:가|는)?\s*없)')


def _requested_sort_fields(state, names):
    from .explicit_table_criteria import explicit_criteria, expected_specs

    explicit = explicit_criteria(state)
    if explicit is not None and 'sort' in explicit:
        return {item['field'] for spec in expected_specs(explicit) if spec['kind'] == 'sort' for item in spec['sort']}
    if not names:
        return set()
    label = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    opening = r'(?<![\w])["\x27“‘「`]?'
    closing = r'["\x27”’」`]?(?![A-Za-z0-9_])'
    label = '(?-i:' + label + ')'
    target = opening + label + closing
    targets = r'(?P<columns>' + target + r'(?:\s*(?:[,·]|및|와|과|그리고)\s*' + target + r')*)'
    suffix = r'\s*(?:(?:열|컬럼)\s*)?(?:을|를|은|는|도|으로|로)?\s*(?:(?:기준|순서|순)(?:으로|로)?\s*)?'
    forward = re.compile(targets + suffix + r'(?P<action>' + _SORT_DIRECTION + r'(?:으로|로)?\s*(?:정렬)?|정렬)', re.I)
    reverse = re.compile(r'(?P<action>정렬\s*기준(?:은|을|으로)?|\bsort\s+by)\s*[:：]?\s*' + targets, re.I)
    names_in_subject = re.compile(opening + '(?P<field>' + label + ')' + closing)
    filenames = [asset['name'] for asset in state.get('resources', [])
                 if re.search(r'\.\w+$', asset.get('name') or '')]
    filename = re.compile('|'.join(map(re.escape, sorted(filenames, key=len, reverse=True))), re.I) if filenames else None
    requested = set()
    for message in active_request_messages(state):
        if message.get('role') != 'user':
            continue
        text = message['content']
        if filename:
            text = filename.sub(lambda match: ' ' * len(match[0]), text)
        verbs = [match for match in re.finditer(r'정렬', text)
                 if not in_quoted_text(text, match.start()) and not in_metadata_text(text, match.start())]
        if any(_SORT_NEGATED.match(text[verb.end():]) for verb in verbs):
            requested.clear()
        current = {column['field'] for pattern in (forward, reverse) for match in pattern.finditer(text)
                   if not in_quoted_text(text, match.start('action'))
                   and not in_metadata_text(text, match.start()) and not _SORT_NEGATED.match(text[match.end():])
                   for column in names_in_subject.finditer(match['columns'])}
        if current:
            requested = requested | current if re.search(r'추가|또한|정렬도', text) else current
    return requested


def _requested_table(state, tables):
    if len(tables) == 1:
        return tables[0]
    from agent_builder.conditions.condition_readiness import _QUOTED_TEXT

    names = {table['name'].casefold() for table in tables if table.get('name')}
    requests = [_QUOTED_TEXT.sub(lambda match: match[0] if match[0][1:-1].casefold() in names
                                 else ' ' * len(match[0]), message['content'])
                for message in active_request_messages(state) if message.get('role') == 'user']
    denied = re.compile(r'\s*(?:말고|빼고|제외|안\s*(?:읽|가져오|사용|쓰)'
                        r'|(?:읽|가져오|사용하|쓰|처리하|선별하|필터링하)지\s*(?:말|마|않))')
    selected = []
    for table in tables:
        name = table.get('name')
        if not name:
            continue
        source = re.compile(r'(?<![\w.\-/\\])["\x27“‘「`]?' + re.escape(name)
                            + r'["\x27”’」`]?\s*(?:파일\s*)?에서(?![가-힣])', re.IGNORECASE)
        mentions = [not denied.match(text, match.end()) for text in requests
                    for match in source.finditer(text) if not in_metadata_text(text, match.start())]
        if mentions and mentions[-1]:
            selected.append(table)
    return selected[0] if len(selected) == 1 else None


def _duplicate_policy_matches(plan, policies):
    actual = plan.get('duplicate_policy', 'keep')
    return (actual == 'keep' or
            (actual == policies.get('duplicate.policy') and 'duplicate.field' in policies
             and plan.get('duplicate_field') == policies['duplicate.field']))


def filter_path_issues(state, draft, groups):
    error = ['행 선별의 전체 데이터 경로가 요청한 AND/OR 조건과 일치하지 않습니다. 앞뒤 필터의 행 누락과 변환을 확인하세요.']
    filters = [rule for group in groups for rule in group['filters']]
    fields = {rule['field'] for rule in filters} | {rule['compare_field'] for rule in filters if rule.get('compare_field')}
    slots = (state.get('condition_ledger') or {}).get('slots') or {}
    policies = {key: slot.get('value') for key, slot in slots.items() if slot.get('status') == 'specified'}
    default_plan = draft.get('execution_plan') or {}
    workflow = draft.get('workflow')
    tables = [asset for asset in state.get('resources', []) if asset.get('kind') == 'table']
    source_table = _requested_table(state, tables)
    if workflow and not source_table:
        return error
    source_columns = frozenset((source_table or {}).get('columns', []))
    nodes = {node['id']: node for node in (workflow or {}).get('nodes', [])}
    missing_policies = (policies['missing.policy'],) if 'missing.policy' in policies else ('ask', 'keep', 'exclude')
    selects_representative = policies.get('duplicate.policy') in ('first', 'last')
    sort_fields = set()
    if ('exclude' in missing_policies or selects_representative) and policies.get('sort.policy') != 'none':
        computed = {node['settings']['output_column'] for node in nodes.values() if node['tool'] == 'calculate_columns'
                    and node['settings'].get('table_operation') == 'compute' and node['settings'].get('output_column')}
        try:
            sort_fields = _requested_sort_fields(state, source_columns | computed)
        except (ValueError, TypeError, KeyError):
            return error
    selection = {policy: canonical(frozenset().union(*(clauses(group, missing_policy=policy) for group in groups)))
                 for policy in missing_policies}
    expected = {expression | _presence_clauses(sort_fields) if policy == 'exclude' else expression
                for policy, expression in selection.items()}
    sort_only = _presence_clauses(sort_fields - missing_check_fields(filters, [], 'exclude', columns=()))
    dedup_first = ordered_row_steps(state) == ('policy', 'filter')
    dedup_inputs = {frozenset()} if dedup_first else set(selection.values())
    error_checks = _first_error_checks(groups) if policies.get('missing.policy') == 'error' else {}
    error_after_dedup = dedup_first and selects_representative
    if not workflow:
        actual_policy = default_plan.get('missing_policy', 'ask')
        try:
            actual = clauses(default_plan, missing_policy=actual_policy)
        except (ValueError, TypeError, KeyError):
            return error
        if error_checks:
            checked = missing_check_fields(default_plan.get('filters', []), default_plan.get('sort', []), 'error')
            if any(error_checks.values()) or not error_checks.keys() <= checked:
                return error
        return [] if (actual_policy in missing_policies or 'missing.policy' not in policies) and (
            canonical(actual) in expected and _duplicate_policy_matches(default_plan, policies)) else error

    def trace(identifier, output, active, seen):
        if identifier not in active or identifier in seen:
            return None
        node = nodes[identifier]
        tool, settings = node['tool'], node['settings']
        operation = settings.get('operation')
        if tool == 'read_table' and not operation and output == 'table':
            selected = set(settings.get('file_ids') or [asset['id'] for asset in tables])
            return (frozenset(), frozenset(), False, False) if selected == {source_table['id']} else None
        plan = None
        removes_duplicates = False
        if not operation:
            safe = tool in ('write_result', 'export_result')
            if tool == 'filter_sort' and output == 'table':
                plan = settings.get('execution_plan') or default_plan
                # An extra node may not silently change the data policies.
                if ('missing.policy' in policies and plan.get('missing_policy', 'ask') != policies['missing.policy']
                        or not _duplicate_policy_matches(plan, policies)):
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
        selected, checked, deduplicated, unrequested_sort = upstream
        if removes_duplicates and (unrequested_sort or canonical(selected - sort_only) not in dedup_inputs):
            return None  # Native deduplication runs before the local predicates.
        deduplicated = deduplicated or removes_duplicates
        if plan is not None:
            if error_checks:
                fields_checked = missing_check_fields(plan.get('filters', []), plan.get('sort', []), 'error')
                first_checks = (fields_checked & error_checks.keys()) - checked
                scope = canonical(selected)
                if any(error_checks[field] != scope or deduplicated != error_after_dedup for field in first_checks):
                    return None
                checked |= first_checks
            selected |= clauses(plan, missing_policy=plan.get('missing_policy', 'ask'))
            # A local sort runs after deduplication. Only downstream representative selection sees it.
            unrequested_sort |= any(item['field'] not in sort_fields for item in plan.get('sort', []))
        return selected, checked, deduplicated, unrequested_sort

    try:
        checked = 0
        for path, _ in workflow_paths(workflow):
            active = {node['id'] for node in path}
            endpoints, _ = filter_result_endpoints(state, workflow, path)
            for node, output in endpoints:
                checked += 1
                actual = trace(node['id'], output, active, set())
                if actual is None or canonical(actual[0]) not in expected or not error_checks.keys() <= actual[1]:
                    return error
        return [] if checked else error
    except (ValueError, KeyError, TypeError):
        return error
