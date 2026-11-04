"""Keep a requested sort from disappearing in native table plans."""
import re

_SORT = re.compile(r'정렬|오름차순|내림차순|(?:큰|작은|높은|낮은|많은|적은)\s*순(?:서)?')
_NO_SORT = re.compile(r'정렬\s*(?:은|을)?\s*(?:하지\s*(?:마|말|않)|안\s*해|없이)|입력\s*순서.*유지')


def sorting_requested(state):
    requested = False
    for message in state.get('messages', []):
        if message.get('role') != 'user':
            continue
        text = re.sub(r"""(["']).*?\1""", '', message['content'])
        for clause in re.split(r'[.!?;\n]+', text):
            if _NO_SORT.search(clause):
                requested = False
                continue
            if _SORT.search(clause):
                requested = True
    return requested


def sort_request_issues(state, draft):
    if (state.get('condition_ledger') or {}).get('decision') != 'ready':
        return []
    if draft.get('questions') or draft.get('unsupported') or not sorting_requested(state):
        return []
    nodes = (draft.get('workflow') or {}).get('nodes', [])
    # Canonical operations and saved workflows have their own input contracts.
    if not nodes:
        return []
    if any(node['settings'].get('operation') or node['tool'] == 'saved_workflow' for node in nodes):
        return []
    plans = [node['settings'].get('execution_plan') or draft.get('execution_plan', {})
             for node in nodes if node['tool'] == 'filter_sort']
    if not any(plan.get('sort') for plan in plans):
        return ['요청한 정렬 조건이 실행 설정에 없습니다. 정렬 열·방향을 반영하거나 확인해야 합니다.']
    return []
