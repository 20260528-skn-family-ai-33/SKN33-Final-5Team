"""Ground the native aggregate output and its consumer for a single, fully specified final artifact."""
import re
from decimal import Decimal

from agent_builder.conditions.condition_readiness import (
    _amounts, _BOUND_SUBJECT_END, _WRITTEN_BOUND, _WRITTEN_OPERATOR, explicit_policies, source_catalog,
)
from .requirement_task_identity import _artifact_position

_TABLE = re.compile(r'\s*(?:(?:연결한|연결된|첨부한|이|해당)\s*)?(?:표(?:를|에서|의)?\s*)?')
_SUM_SUBJECT = re.compile(r'(?:하고|하여|해서|한\s*(?:후|뒤)|한\s*다음)\s+sum' + _BOUND_SUBJECT_END)
_FINAL_EXPORT = re.compile(
    r'\s*(?:인\s*(?:최종)?)?\s*(?:결과물?|행|항목)만(?:을)?\s*(?:하나의\s*)?(?P<fmt>csv|json)'
    r'\s*(?:파일)?(?:으로|로)\s*(?:저장|내보내)(?:해)?\s*(?:줘|주세요|세요)?(?=[.!?,\s]|$)', re.I)
_POLICIES = re.compile(r'(?:[.!?,\s]|(?:빈\s*(?:값|셀|칸)|중복)(?:은|는)?\s*(?:오류|에러|유지|제외)(?:로\s*처리)?(?:해|하고)?)*')


def grouped_result_specs(state):
    """All clauses must describe this one chain, including affirmative policy clauses after export."""
    from .processing_guard import _GROUP_SUM
    users = [m['content'] for m in state.get('messages', []) if m['role'] == 'user']
    assets = state.get('resources', [])
    if (len(users) != 1 or len(assets) != 1 or assets[0].get('kind') != 'table'
            or any(state.get(k) for k in ('base_agent_id', 'required_node_refs', 'redesign_context',
                                           'tool_gap_choice', 'condition_enforced'))):
        return None
    text = users[0]
    sums = list(_GROUP_SUM.finditer(text))
    bounds = list(_WRITTEN_BOUND.finditer(text))
    if len(sums) != 1 or len(bounds) != 1:
        return None
    aggregate, bound = sums[0], bounds[0]
    group, measure = aggregate.group('group', 'measure')
    columns = set(assets[0].get('columns', []))
    if (not {group, measure} <= columns or 'sum' in columns or group == measure
            or not _TABLE.fullmatch(text[:aggregate.start()])
            or not _SUM_SUBJECT.fullmatch(text[aggregate.end():bound.start()])):
        return None
    tail = text[bound.end():]
    export = _FINAL_EXPORT.match(tail)
    amounts = _amounts(bound['amount'], 'sum')
    if not export or len(amounts) != 1 or not _POLICIES.fullmatch(tail[export.end():]):
        return None
    fmt = export['fmt'].lower()
    if _artifact_position(text, fmt) != 'final':
        return None
    literal = explicit_policies(state, source_catalog(state)[0])
    policies = {k: literal[k] for k in ('missing.policy','duplicate.policy') if k in literal}
    if any(p['status'] != 'specified' for p in policies.values()):
        return None
    expected = {
        'aggregate': dict(kind='aggregate', fields=[group], aggregation='sum', sum_field=measure),
        'filter': dict(kind='filter', match='all', filters=[dict(field='sum', value_type='number',
                       value=amounts.pop(), operator=_WRITTEN_OPERATOR[bound['operator']], compare_field=None)]),
        'export': dict(kind='export', format=fmt),
    }
    if policies:
        expected['policy'] = dict(kind='policy', missing_policy=policies.get('missing.policy', {}).get('value'),
                                  duplicate_policy=policies.get('duplicate.policy', {}).get('value'), duplicate_field=None)
    return expected


def reconcile_grouped_result(candidates, state, expected):
    tasks = {t['spec'].get('kind'): t for t in candidates}
    if (len(tasks) != len(candidates) or tasks.keys() - (expected.keys() | {'read','rename','select_fields'})
            or not expected.keys() <= tasks.keys()
            or any(tasks[k]['spec'] != spec for k, spec in expected.items() if k != 'filter')):
        return candidates
    selection = tasks['filter']['spec']
    if selection.get('match') != 'all' or len(selection.get('filters', [])) != 1:
        return candidates
    rule = selection['filters'][0]
    if not isinstance(rule, dict) or type(rule.get('value')) not in (float, int):
        return candidates
    alias = rule.get('field')
    native = expected['filter']['filters'][0]
    if not isinstance(alias, str) or {**rule, 'field':'sum', 'value':Decimal(str(rule['value']))} != native:
        return candidates
    asset = state['resources'][0]
    text = next(m['content'] for m in state['messages'] if m['role']=='user')
    if alias != 'sum' and (alias in asset['columns'] or alias in text):
        return candidates
    # The registered aggregate emits its group columns followed by "sum".
    # Only a rename from the nonexistent alias and a projection identical to
    # that ordered output are explained by the model's spurious alias.
    compatible = {
        'rename': dict(kind='rename', source=alias, target='sum'),
        'select_fields': dict(kind='select_fields', fields=[*expected['aggregate']['fields'], 'sum']),
        'read': dict(kind='read', file_ids=[asset['id']]),
    }
    if any(k in tasks and tasks[k]['spec'] != spec for k,spec in compatible.items()):
        return candidates
    tasks['filter'] = {**tasks['filter'], 'spec':{**selection, 'filters':[{**rule, 'field':'sum'}]}}
    order = ('read', 'aggregate', 'filter', 'policy', 'export')
    return [tasks[k] for k in order if k in tasks]


def validate_grouped_result(plan, state):
    expected = grouped_result_specs(state)
    if not expected or plan['unresolved']:
        return
    tasks = {t['spec']['kind']: t for t in plan['tasks']}
    if 'read' in tasks:
        expected['read'] = dict(kind='read', file_ids=[state['resources'][0]['id']])
    if len(tasks) != len(plan['tasks']) or tasks.keys() != expected.keys():
        raise ValueError('원문에 명시된 단일 집계·선별·결과 저장 업무를 확인하세요.')
    from .requirement_coverage import _same_filters
    for kind, spec in expected.items():
        actual = tasks[kind]['spec']
        if kind == 'filter':
            if _same_filters(actual['filters'], spec['filters'], actual['match'], spec['match']):
                continue
        elif actual == spec:
            continue
        raise ValueError('집계 결과의 업무 설정이 사용자 원문과 다릅니다: ' + kind)
    previous = []
    for kind in ('read','aggregate','filter','export'):
        if kind in tasks:
            if tasks[kind]['depends_on'] != previous:
                raise ValueError('합계 선별과 최종 저장은 해당 집계 결과에 연결되어야 합니다.')
            previous = [tasks[kind]['id']]
