"""Keep a proven aggregate contract across turns, independently of auto generation."""
import re
from pathlib import PurePosixPath


_ACK = re.compile(
    r'(?:네|예|응|yes|ok|알겠어|알겠습니다|확인했어|확인했습니다)'
    r'(?:[,.!]?\s+(?:알겠습니다|확인했습니다))?[.!。]?', re.I)
_END = r'(?:해\s*(?:줘|주세요)|하세요)[.!]?'
_SORT_CANCEL = re.compile(
    r'정렬(?:은|을|이)?\s*(?:(?:취소|생략)' + _END + r'|'
    r'(?:건너뛰어\s*(?:줘|주세요)|(?:할\s*)?필요(?:가|는)?\s*없(?:어|어요|습니다|다))[.!]?)')


def grouped_contract(state):
    from .grouped_totals_request import _REQUEST, group_sort_type
    from .output_selection_request import _request_turns
    from .export_intent import explicit_export_intent, request_clauses

    active = list(_request_turns(state))
    for source, request in reversed(active):
        if len(request) <= 2000 and (match := _REQUEST.fullmatch(request)):
            break
    else:
        return None
    values = match.groupdict()
    assets = [a for a in state.get('resources', []) if a.get('kind') == 'table' and a.get('name') == values['file']]
    if len(assets) != 1 or not assets[0].get('id'):
        return None
    asset = assets[0]
    status = values['subject'] + '상태'
    columns = set(asset.get('columns', []))
    if not {values['left'], values['right'], values['group'], status} <= columns:
        return None
    intent = explicit_export_intent(state)
    contract = dict(values, asset_id=asset['id'], status=status, source=source, first_active_source=active[0][0],
                    sort_type=group_sort_type(asset, values['group']),
                    formula=f"{values['left']} * {values['right']}", request=request.strip(),
                    revisions={}, revision_clauses={}, unknown=[],
                    formats=set(intent['formats']), export_source=max(source, intent['active_from']))
    group = re.escape(values['group'])
    for index, text in active:
        if index <= source or _ACK.fullmatch(text.strip()):
            continue
        for _, clause in request_clauses(text):
            clause = clause.strip()
            if not clause or _ACK.fullmatch(clause):
                continue
            direction = re.fullmatch(
                rf'(?:{group}\s+)?(오름차순|내림차순)(?:으로)?\s*정렬' + _END, clause)
            status_change = re.fullmatch(r'(?:' + re.escape(contract['value']) + r'\s*(?:대신|말고)\s*)?'
                r'(?P<value>[\w-]+)\s+' + re.escape(values['subject']) + r'만\s*(?:대상으로\s*)?' + _END, clause)
            formula = re.fullmatch(r'(?:계산식(?:은|을)|' + re.escape(values['measure']) + r'(?:은|을))\s*'
                r'(.+?)(?:으로|로)\s*(?:변경|계산)' + _END, clause)
            if direction:
                contract['direction'] = direction[1]
                role = 'sort'
            elif _SORT_CANCEL.fullmatch(clause):
                contract['direction'] = None
                role = 'sort'
            elif status_change:
                contract['value'] = status_change['value']
                role = 'filter'
            elif formula:
                from workflow_engine.table_formula import formula_columns
                try:
                    operands = formula_columns(formula[1])
                except ValueError:
                    operands = None
                if operands is None or not set(operands) <= columns:
                    contract['unknown'].append(clause)
                    continue
                contract['formula'] = formula[1]
                role = 'compute'
            elif (index, clause) in intent['accepted_clauses']:
                role = 'export'
            else:
                # Never discard a new instruction or silently unseal old work.
                contract['unknown'].append(clause)
                continue
            contract['revisions'][role] = index
            contract['revision_clauses'][role] = clause
    return contract


def grouped_evidence(contract, state):
    from .export_intent import request_clauses

    users = [m['content'] for m in state['messages'] if m['role'] == 'user']
    changes = {'filter': '선별 조건 변경', 'compute': '계산식 변경',
               'sort': '정렬 조건 변경', 'export': '결과물 저장 변경'}
    entries = [(contract['source'], contract['request'], '합계·행 개수 원문')]
    entries.extend((source, contract['revision_clauses'][role], changes[role])
                   for role, source in contract['revisions'].items())
    evidence = {}
    for index, parsed, description in sorted(entries, key=lambda entry: entry[0]):
        original = users[index]
        if len(original) <= 2000:
            quote = original
        elif parsed in original and len(parsed) <= 2000:
            quote = parsed
        else:
            quote = next((part.strip() for _, part in request_clauses(original)
                          if 0 < len(part.strip()) <= 2000 and part.strip() in parsed), None)
        if quote:
            evidence.setdefault(quote, []).append(description)
    return [dict(source_id='user', quote=quote, description=' · '.join(descriptions))
            for quote, descriptions in evidence.items()]


def contract_source(contract, role):
    if role.startswith('export_'):
        return contract['export_source']
    return contract['revisions'].get(role, contract['source'])


def contract_specs(contract):
    """Roles carry the data dependencies even when rename/sort/export is absent."""
    c = contract
    result = [
        ('read', dict(kind='read', file_ids=[c['asset_id']])),
        ('filter', dict(kind='filter', match='all', filters=[dict(field=c['status'], operator='eq', value=c['value'],
            compare_field=None, value_type='text')])),
        ('compute', dict(kind='compute', formula=c['formula'], output_column=c['measure'])),
        ('sum', dict(kind='aggregate', fields=[c['group']], aggregation='sum', sum_field=c['measure'])),
    ]
    if c['total'] != 'sum':
        result.append(('total_name', dict(kind='rename', source='sum', target=c['total'])))
    result.append(('count', dict(kind='aggregate', fields=[c['group']], aggregation='count', sum_field=None)))
    if c['count'] != 'count':
        result.append(('count_name', dict(kind='rename', source='count', target=c['count'])))
    if c['direction']:
        result.append(('sort', dict(kind='sort', sort=[dict(field=c['group'],
            direction='ascending' if c['direction'] == '오름차순' else 'descending', value_type=c['sort_type'])])))
    result.append(('select', dict(kind='select_fields', fields=[c['group'], c['total'], c['count']])))
    result.extend(('export_' + fmt, dict(kind='export', format=fmt)) for fmt in sorted(c['formats']))
    return result


def checked_specs(contract, tasks):
    if contract['unknown']:
        raise ValueError('후속 변경을 기존 합계·행 개수 계약과 함께 확인하지 못했습니다. 변경한 조건을 포함한 전체 요청이 필요합니다.')
    expected = contract_specs(contract)
    if len(tasks) != len(expected):
        raise ValueError('합계·행 개수의 필터·계산·집계·정렬·출력 작업이 원문과 다릅니다.')
    from .requirement_coverage import _formula
    def matches(required, actual):
        if required['kind'] == 'compute' and actual.get('kind') == 'compute':
            equal = actual['output_column'] == required['output_column'] and _formula(actual['formula']) == _formula(required['formula'])
        elif required['kind'] == 'sort' and actual.get('kind') == 'sort' and required['sort'][0]['value_type'] is None:
            equal = (len(actual['sort']) == 1 and actual['sort'][0]['field'] == required['sort'][0]['field']
                     and actual['sort'][0]['direction'] == required['sort'][0]['direction']
                     and actual['sort'][0]['value_type'] in ('text', 'number'))
        else:
            equal = actual == required
        return equal
    # The two independent aggregate branches may be listed in either order.
    # Match each full spec exactly once; task positions are not user intent.
    remaining = list(expected)
    ordered = []
    for task in tasks:
        candidates = [item for item in remaining if matches(item[1], task['spec'])]
        if len(candidates) != 1:
            raise ValueError('원문의 ' + task['spec']['kind'] + ' 조건이 누락되거나 변경됐습니다.')
        ordered.append(candidates[0])
        remaining.remove(candidates[0])
    return ordered


def connected_tasks(contract, tasks, state):
    from copy import deepcopy
    expected = checked_specs(contract, tasks)
    users = [m['content'] for m in state['messages'] if m['role'] == 'user']
    result = deepcopy(tasks)
    ids = {role:t['id'] for (role, _), t in zip(expected, tasks)}
    total = 'total_name' if 'total_name' in ids else 'sum'
    count = 'count_name' if 'count_name' in ids else 'count'
    parents = dict(read=[], filter=['read'], compute=['filter'], sum=['compute'], total_name=['sum'],
                   count=['compute'], count_name=['count'], sort=[total, count],
                   select=['sort'] if 'sort' in ids else [total, count])
    for (role, _), task in zip(expected, result):
        source = contract_source(contract, role)
        task.update(source_message=source, quote=users[source],
                    depends_on=[ids[p] for p in (['select'] if role.startswith('export_') else parents[role])])
    return result


def contract_filename(contract, fmt):
    path = PurePosixPath(contract['output'])
    if path.suffix.casefold() == '.' + fmt.casefold():
        return contract['output']
    return str(path.with_suffix('.' + fmt))
