"""Closed, source-grounded table selection contracts, never model-derived repairs.

Recognize complete single-table requests for grouped OR/AND selection or scoped
empty removal. Unknown clauses, quoted examples and later revisions stay with
the general planner. The same contract constrains extraction and final data flow.
"""
import json
import re

from contracts.models import FilterCondition


def _rule(field, operator, value=None, value_type='text'):
    return FilterCondition(field=field, operator=operator, value=value,
                           compare_field=None, value_type=value_type).model_dump()


def compound_table_request(state):
    # Revisions require the general conversational owner. An exact retry of the
    # same complete request has the same contract and original source citation.
    users = [m['content'].strip() for m in state.get('messages', []) if m.get('role') == 'user']
    assets = state.get('resources', [])
    if not users or len(set(users)) != 1 or len(assets) != 1 or assets[0].get('kind') != 'table':
        return None
    asset = assets[0]
    columns = asset.get('columns', [])
    if (not asset.get('id') or not asset.get('name') or not columns or len(set(columns)) != len(columns)
            or any(state.get(k) for k in ('base_agent_id', 'required_node_refs', 'redesign_context', 'tool_gap_choice'))):
        return None
    names = '(?:' + '|'.join(re.escape(c) for c in sorted(columns, key=len, reverse=True)) + ')'
    source = re.escape(asset['name']) + r'에서\s+'
    finish = (r'(?:전체|모든)\s*열을\s*유지하고\s+(?P<sort>' + names + r')\s+'
              r'(?P<direction>오름차순|내림차순)으로\s*(?:정렬(?:해서|하고)\s+)?'
              r'(?P<path>[\w가-힣.-]+\.csv)(?:로|에)\s*저장해\s*줘[.]?')
    empty = (source + r'(?P<a>' + names + r')\s*(?:또는|혹은)\s*(?P<b>' + names + r')(?:이|가)\s*빈\s*행만\s*'
             r'(?:제거|삭제)해\s*줘\.\s*(?P<allowed>' + names + r')(?:이|가)\s*빈\s*것은\s*허용하고\s*'
             r'(?P<zero>' + names + r')\s*0도\s*정상\s*값으로\s*남겨\s*줘\.\s*' + finish)
    grouped = (source + r'\((?P<a>' + names + r')(?:이|가)\s+(?P<av>\w+?)이거나\s+'
               r'(?P<b>' + names + r')(?:이|가)\s+(?P<bv>\w+)\)인\s*조건과\s+'
               r'(?P<number>' + names + r')(?:이|가)\s+(?P<value>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*'
               r'(?P<operator>이상|초과|이하|미만)인\s*조건을\s*동시에\s*만족하는\s*행만\s*남겨\s*줘\.\s*' + finish)
    match = re.fullmatch(empty, users[0])
    if match:
        if match['a'] == match['b'] or match['allowed'] in (match['a'], match['b']):
            return None
        filters = [dict(kind='filter', match='all', filters=[_rule(match[k], 'not_empty') for k in ('a', 'b')])]
    else:
        match = re.fullmatch(grouped, users[0])
        if not match:
            return None
        from contracts.numeric_criteria import number_value
        from agent_builder.conditions.input_resolution import executable_number
        threshold = number_value(match['value'])
        if not executable_number(threshold):
            return None
        # Legacy missing_policy=keep skips unknown comparisons, and an empty
        # comparison list passes. Explicit nonempty guards keep that behavior
        # from admitting rows with both OR operands missing or no amount.
        filters = [dict(kind='filter', match='any', filters=[_rule(match[k], 'not_empty') for k in ('a', 'b')]),
                   dict(kind='filter', match='any', filters=[_rule(match[k], 'eq', match[k+'v']) for k in ('a', 'b')]),
                   dict(kind='filter', match='all', filters=[_rule(match['number'], 'not_empty'), _rule(match['number'],
                        {'이상':'gte', '초과':'gt', '이하':'lte', '미만':'lt'}[match['operator']],
                        float(threshold), 'number')])]
    from agent_builder.conditions.input_resolution import numeric_column
    numeric = numeric_column(state, match['sort']) or match['sort'] == match.groupdict().get('number', match.groupdict().get('zero'))
    sorting = [dict(field=match['sort'], direction='ascending' if match['direction']=='오름차순' else 'descending', value_type='number' if numeric else 'text')]
    return dict(filters=filters, sort=sorting, columns=columns, source=asset['id'], path=match['path'])


def request_specs(request):
    return [dict(kind='read', file_ids=[request['source']]), *request['filters'],
            dict(kind='sort', sort=request['sort']), dict(kind='select_fields', fields=request['columns']),
            dict(kind='export', format='csv')]


def grounded_compound_rule(state, rule):
    request = compound_table_request(state)
    return bool(request and any(rule == expected for spec in request['filters'] for expected in spec['filters']))


def _groups(specs):
    """CNF: successive filters are AND; retain each disjunction intact."""
    def key(rule):
        return json.dumps(FilterCondition.model_validate(rule).model_dump(), sort_keys=True, ensure_ascii=False)
    groups = set()
    for spec in specs:
        rules = frozenset(key(f) for f in spec['filters'])
        if spec.get('match', 'all') == 'all':
            groups.update(frozenset([r]) for r in rules)
        elif spec['match'] == 'any' and rules:
            groups.add(rules)
        else:
            raise ValueError('선별 조건 묶음을 확인할 수 없습니다.')
    return groups


def validate_compound_ledger(plan, state):
    request = compound_table_request(state)
    if request is None or plan['unresolved']:
        return
    actual = [t['spec'] for t in plan['tasks']]
    filters = [s for s in actual if s['kind'] == 'filter']
    if _groups(filters) != _groups(request['filters']):
        raise ValueError('열별 빈값 또는 OR/AND 조건 묶음이 사용자 원문과 다릅니다.')


def compound_draft_issues(state, draft):
    request = compound_table_request(state)
    if request is None:
        return []
    error = ['열별 빈값·OR/AND 조건, 원본 열, 정렬이 최종 CSV 경로에 보존되지 않았습니다.']
    if not draft.get('workflow'):
        return error
    from .workflow_paths import workflow_paths
    from .content_lineage import content_bindings
    from .requirement_coverage import _arguments
    nodes = {n['id']:n for n in draft['workflow']['nodes']}
    try:
        writers = 0
        for path, _ in workflow_paths(draft['workflow']):
            active = {n['id'] for n in path}
            for writer in path:
                if writer['tool'] not in ('write_result', 'export_result'):
                    continue
                writers += 1
                identifier, output, seen, filters, sort = writer['id'], None, set(), [], None
                while True:
                    if identifier in seen or identifier not in active:
                        return error
                    seen.add(identifier)
                    node = nodes[identifier]; tool = node['tool']; settings = node['settings']; op = settings.get('operation')
                    if tool == 'read_table' and not op:
                        if output != 'table' or settings['file_ids'] != [request['source']]:
                            return error
                        break
                    if tool == 'filter_sort' and not op and output == 'table':
                        plan = settings.get('execution_plan') or draft['execution_plan']
                        if plan['missing_policy'] != 'keep' or plan['duplicate_policy'] != 'keep':
                            return error
                        filters.append(plan)
                        if sort is None and plan['sort']:
                            sort = plan['sort']
                    elif tool == 'export_result' and op == 'write_csv':
                        args = _arguments(node)
                        if args.get('columns') != request['columns'] or args.get('path') != request['path']:
                            return error
                    elif tool == 'json_transform' and op == 'lookup' and output == 'records':
                        if _arguments(node).get('path') != ['rows']:
                            return error
                    elif tool == 'filter_sort' and op == 'select_columns' and output == 'records':
                        if _arguments(node).get('columns') != request['columns']:
                            return error
                    else:
                        return error
                    bindings = content_bindings(node, output)
                    if len(bindings) != 1:
                        return error
                    identifier, output = bindings[0]['node_id'], bindings[0]['output']
                if seen != active or _groups(filters) != _groups(request['filters']) or sort != request['sort']:
                    return error
        return [] if writers else error
    except (ValueError, KeyError, TypeError):
        return error
