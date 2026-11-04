"""Replay explicit ordering changes and audit native table execution settings."""
import re

_DIRECTION = re.compile(r'내림차순|오름차순|(?:큰|작은|높은|낮은|많은|적은)\s*순(?:서)?')
_SORT = re.compile(r'정렬(?=$|[\s,.!?]|해|하|은|을)')
_CANCEL = re.compile(r'정렬\s*(?:은|을)?\s*(?:취소|하지\s*(?:마|말|않)|안\s*해|없이)|입력\s*순서.*유지')


def sorting_contract(state):
    fields = {str(column) for resource in state.get('resources', []) for column in resource.get('columns', [])}
    slots = (state.get('condition_ledger') or {}).get('slots', {})
    fields.update(str(item['value']) for key, item in slots.items()
                  if key.endswith('.field') and item.get('status') == 'specified')
    orders = {}
    unresolved = False
    cancelled = False
    for message in state.get('messages', []):
        if message.get('role') != 'user':
            continue
        text = re.sub(r"""(["']).*?\1""", '', message['content'])
        for clause in re.split(r'[.!?;\n]+|(?<=말고)\s*', text):
            if not (_SORT.search(clause) or _DIRECTION.search(clause)):
                continue
            named = [field for field in sorted(fields, key=len, reverse=True)
                     if re.search(r'(?<![\w])' + re.escape(field)
                                  + r'(?=$|[^\w]|이|가|은|는|을|를|의|합계)', clause)]
            if _CANCEL.search(clause):
                if named:
                    for field in named:
                        orders.pop(field, None)
                else:
                    orders.clear()
                    unresolved = False
                cancelled = True
                continue
            direction = _DIRECTION.search(clause)
            if len(named) != 1 or direction is None:
                for field in named:
                    orders.pop(field, None)
                unresolved = True
                continue
            word = direction.group()
            orders[named[0]] = 'descending' if re.search(r'내림|큰|높은|많은', word) else 'ascending'
            cancelled = False
    return {'orders': orders, 'unresolved': unresolved, 'cancelled': cancelled}


def sorting_requested(state):
    contract = sorting_contract(state)
    return bool(contract['orders'] or contract['unresolved'])


def sort_request_issues(state, draft):
    if draft.get('questions') or draft.get('unsupported'):
        return []
    contract = sorting_contract(state)
    nodes = (draft.get('workflow') or {}).get('nodes', [])
    if not nodes:
        return []
    plans = [node['settings'].get('execution_plan') or draft.get('execution_plan', {})
             for node in nodes if node['tool'] == 'filter_sort' and not node['settings'].get('operation')]
    actual = [order for plan in plans for order in plan.get('sort', [])]
    from workflow_engine.catalog.canonical import parse_arguments
    for node in nodes:
        if node['tool'] == 'filter_sort' and node['settings'].get('operation') == 'sort':
            try:
                keys = parse_arguments(node['settings']).get('keys', [])
                actual.extend({'field': key['column'],
                               'direction': 'descending' if key.get('descending', False) else 'ascending'}
                              for key in keys)
            except (ValueError, KeyError, TypeError):
                return ['정렬 도구의 열·방향 인자를 확인해야 합니다.']
    if contract['orders']:
        expected = list(contract['orders'].items())
        actual_keys = [(order['field'], order['direction']) for order in actual]
        if actual_keys != expected:
            return ['요청한 정렬 조건의 열·방향·키 우선순위가 실행 설정과 다릅니다. 정렬을 반영하거나 확인해야 합니다.']
    elif contract['cancelled'] and actual:
        return ['취소한 정렬 조건이 실행 설정에 남아 있습니다.']
    elif contract['unresolved']:
        # Without a table or a confirmed condition contract this can be unrelated
        # historical text. A table request needs clarification before approval.
        if state.get('resources') or (state.get('condition_ledger') or {}).get('decision') == 'ready':
            return ['정렬할 열·방향을 확정하지 못했습니다. 사용자에게 확인해야 합니다.']
    return []
