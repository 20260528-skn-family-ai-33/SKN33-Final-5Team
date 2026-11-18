"""A fully written single-table sum/count request, with parallel row lineage.

Consume the whole request, not a keyword subset. Anything outside this grammar
stays on the general extractor; no file contents or expected answers are used.
"""
import re

from workflow_engine.table_formula import formula_columns


_NAME = r'[\w-]+?'
_END = r'(?:해\s*(?:줘|주세요)|하세요)[.!]?'
_REQUEST = re.compile(
    rf'\s*(?P<file>[^\s/\\]+\.csv)에서\s+(?P<value>{_NAME})\s+(?P<subject>{_NAME})만\s+대상으로\s+'
    rf'(?P<left>{_NAME})\s*(?:곱하기|×|\*)\s*(?P<right>{_NAME})(?:을|를)\s+'
    rf'(?P<measure>{_NAME})(?:으로|로)\s*계산{_END}\s+'
    rf'(?P<group>{_NAME})별\s+(?P=measure)\s+합계(?:와|\s*및)\s+(?:(?P=subject)\s+)?행\s*(?:개수|수)를\s*구해서\s+'
    rf'열\s*이름을\s*(?P=group)\s*,\s*(?P<total>{_NAME})\s*,\s*(?P<count>{_NAME})(?:으로|로)\s*{_END}\s+'
    rf'(?P=group)\s+(?P<direction>오름차순|내림차순)(?:으로)?\s*정렬한\s*뒤\s+'
    rf'(?P<output>[^\s/\\]+\.(?:csv|json))(?:으로|로)\s*저장{_END}'
    rf'(?:\s+(?P=left)(?:이|가)\s*0인\s+(?P=value)\s+(?P=subject)도\s+(?P=count)(?:에는|에)\s*포함{_END})?\s*', re.I)


def group_sort_type(asset, field):
    """Use the complete upload profile, never a column name or a few samples."""
    profiles = asset.get('profile')
    profile = profiles.get(field) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        return None
    total, missing = asset.get('row_count'), profile.get('missing')
    if type(total) is not int or type(missing) is not int or not 0 <= missing < total:
        return None
    raw_types = profile.get('types')
    if not isinstance(raw_types, list) or any(not isinstance(t, str) for t in raw_types):
        return None
    types = set(raw_types)
    numeric = profile.get('numericStrings')
    if types == {'number'}:
        return 'number'
    if types == {'string'} and type(numeric) is int:
        if numeric == total - missing:
            return 'number'
        if numeric == 0:
            return 'text'
    return None


def grouped_totals_packet(state):
    from .grouped_totals_contract import contract_source, contract_specs, grouped_contract

    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    assets = state.get('resources', [])
    if (not users or len(users[-1]) > 2000 or len(assets) != 1 or assets[0].get('kind') != 'table'
            or any(state.get(k) for k in ('base_agent_id', 'required_node_refs', 'redesign_context'))
            or not _REQUEST.fullmatch(users[-1])):
        return None
    contract = grouped_contract(state)
    if contract is None or contract['source'] != len(users) - 1:
        return None
    if len(users) > 1 and contract['first_active_source'] == 0:
        return None
    if (not all(contract[key].isidentifier() for key in ('left', 'right'))
            or len({contract['group'], contract['measure'], contract['total'], contract['count'], 'sum', 'count'}) != 6
            or contract['measure'] in assets[0].get('columns', [])
            or contract['group'] in (contract['left'], contract['right']) or contract['sort_type'] is None):
        return None
    try:
        if formula_columns(contract['formula']) != {contract['left'], contract['right']}:
            return None
    except ValueError:
        return None
    return dict(tasks=[dict(source_message=contract_source(contract, role), description=spec['kind'], spec=spec)
                       for role, spec in contract_specs(contract)], unresolved=[])


def validate_grouped_totals_intent(plan, state):
    from .grouped_totals_contract import grouped_contract, checked_specs
    contract = grouped_contract(state)
    if contract is not None and not plan['unresolved']:
        checked_specs(contract, plan['tasks'])


def connect_grouped_totals(tasks, state):
    from .grouped_totals_contract import grouped_contract, connected_tasks
    contract = grouped_contract(state)
    return connected_tasks(contract, tasks, state) if contract is not None else tasks


def grouped_totals_filename(state):
    from .grouped_totals_contract import grouped_contract, contract_filename
    contract = grouped_contract(state)
    if contract is None or len(contract['formats']) != 1:
        return None
    return contract_filename(contract, next(iter(contract['formats'])))


def validate_grouped_totals(plan, state):
    from .grouped_totals_contract import grouped_contract, connected_tasks
    contract = grouped_contract(state)
    if contract is None:
        return
    if plan['unresolved']:
        raise ValueError('명시한 합계·행 개수 조건과 후속 변경을 확인한 뒤 설계해야 합니다.')
    wired = connected_tasks(contract, plan['tasks'], state)
    if any(expected['depends_on'] != actual['depends_on'] for expected, actual in zip(wired, plan['tasks'])):
        raise ValueError('합계와 행 개수는 같은 선별 원본에서 계산하고 최종 결과에 함께 연결해야 합니다.')
    if wired != plan['tasks']:
        raise ValueError('합계·행 개수 조건의 근거가 현재 요청 또는 후속 변경을 인용하지 않습니다.')
