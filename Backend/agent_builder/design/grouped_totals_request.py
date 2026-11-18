"""A fully written single-table sum/count request, with parallel row lineage.

Consume the whole request, not a keyword subset. Anything outside this grammar
stays on the general extractor; no file contents or expected answers are used.
"""
import re
from copy import deepcopy


_NAME = r'[\w-]+?'
_END = r'(?:해\s*(?:줘|주세요)|하세요)[.!]?'
_REQUEST = re.compile(
    rf'\s*(?P<file>[^\s/\\]+\.csv)에서\s+(?P<value>{_NAME})\s+(?P<subject>{_NAME})만\s+대상으로\s+'
    rf'(?P<left>{_NAME})\s*(?:곱하기|×|\*)\s*(?P<right>{_NAME})(?:을|를)\s+'
    rf'(?P<measure>{_NAME})(?:으로|로)\s*계산{_END}\s+'
    rf'(?P<group>{_NAME})별\s+(?P=measure)\s+합계(?:와|\s*및)\s+(?:(?P=subject)\s+)?행\s*(?:개수|수)를\s*구해서\s+'
    rf'열\s*이름을\s*(?P=group)\s*,\s*(?P<total>{_NAME})\s*,\s*(?P<count>{_NAME})(?:으로|로)\s*{_END}\s+'
    rf'(?P=group)\s+(?P<direction>오름차순|내림차순)(?:으로)?\s*정렬한\s*뒤\s+'
    rf'(?P<output>[^\s/\\]+\.csv)(?:으로|로)\s*저장{_END}'
    rf'(?:\s+(?P=left)(?:이|가)\s*0인\s+(?P=value)\s+(?P=subject)도\s+(?P=count)(?:에는|에)\s*포함{_END})?\s*', re.I)


def grouped_totals_packet(state):
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    assets = state.get('resources', [])
    if (len(users) != 1 or len(users[0]) > 2000 or len(assets) != 1 or assets[0].get('kind') != 'table'
            or any(state.get(k) for k in ('base_agent_id', 'required_node_refs', 'redesign_context'))):
        return None
    match = _REQUEST.fullmatch(users[0])
    if not match:
        return None
    v = match.groupdict()
    asset = assets[0]
    columns = asset.get('columns', [])
    status = v['subject'] + '상태'
    if (asset.get('name') != v['file'] or not asset.get('id')
            or not {v['left'], v['right'], v['group'], status} <= set(columns)
            or not all(v[key].isidentifier() for key in ('left', 'right'))
            or len({v['group'], v['measure'], v['total'], v['count'], 'sum', 'count'}) != 6
            or v['measure'] in columns or v['group'] in (v['left'], v['right'])):
        return None
    specs = [
        dict(kind='read', file_ids=[asset['id']]),
        dict(kind='filter', match='all', filters=[dict(field=status, operator='eq', value=v['value'],
                                                    compare_field=None, value_type='text')]),
        dict(kind='compute', formula=f"{v['left']} * {v['right']}", output_column=v['measure']),
        dict(kind='aggregate', fields=[v['group']], aggregation='sum', sum_field=v['measure']),
        dict(kind='rename', source='sum', target=v['total']),
        dict(kind='aggregate', fields=[v['group']], aggregation='count', sum_field=None),
        dict(kind='rename', source='count', target=v['count']),
        dict(kind='sort', sort=[dict(field=v['group'], direction='ascending' if v['direction']=='오름차순' else 'descending', value_type='text')]),
        dict(kind='select_fields', fields=[v['group'], v['total'], v['count']]),
        dict(kind='export', format='csv'),
    ]
    return dict(tasks=[dict(source_message=0, description=s['kind'], spec=s) for s in specs], unresolved=[])


def connect_grouped_totals(tasks, state):
    """Both measures consume the same rows; final consumers need both branches."""
    packet = grouped_totals_packet(state)
    if packet is None:
        return tasks
    expected = [t['spec'] for t in packet['tasks']]
    if [t['spec'] for t in tasks] != expected:
        raise ValueError('합계·행 개수 요청의 계산, 선별, 집계, 열 이름, 정렬·출력 조건이 원문과 다릅니다.')
    result = deepcopy(tasks)
    parents = [[], [0], [1], [2], [3], [2], [5], [4, 6], [7], [8]]
    for item, indices in zip(result, parents):
        item['depends_on'] = [result[i]['id'] for i in indices]
    return result


def grouped_totals_filename(state):
    if grouped_totals_packet(state) is None:
        return None
    text = next(m['content'] for m in state['messages'] if m['role'] == 'user')
    return _REQUEST.fullmatch(text)['output']


def validate_grouped_totals(plan, state):
    if grouped_totals_packet(state) is None:
        return
    if plan['unresolved']:
        raise ValueError('원문에 명시된 합계·행 개수 조건을 다시 질문할 수 없습니다.')
    connected = connect_grouped_totals(plan['tasks'], state)
    if connected != plan['tasks']:
        raise ValueError('합계와 행 개수는 같은 선별 원본에서 계산하고 최종 결과에 함께 연결해야 합니다.')
