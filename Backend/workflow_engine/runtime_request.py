"""Apply explicit extra table criteria to a manual run snapshot, never a saved agent.

This intentionally small grammar handles additive filters and output sorting.
Unknown expressions and topology changes require the normal design conversation.
"""
from copy import deepcopy
import re

from contracts.models import NodeSettings, ExecutionPlan
from agent_builder.conditions.input_requirements import table_run_request_notice


HELP = ('추가 실행 조건을 적용하지 못했습니다. 열 이름을 포함해 '
        '“주문상태 = 완료; 주문금액 >= 100000; 주문번호 오름차순”처럼 입력하세요. '
        '숫자로 정렬하려면 “주문금액 숫자 내림차순”처럼 지정하세요. '
        '기존 조건 변경·계산·출력 열 변경은 설정 수정에서 저장한 뒤 실행하세요.')


def _criteria(text, columns):
    text = re.sub(r'^(?:이번에는|이번 실행에만|추가로)\s*', '', text.strip())
    text = re.sub(r'\s*(?:만\s*)?(?:추출|골라|정리|실행|필터링|저장|정렬)(?:해)?\s*(?:줘|주세요)[.!\s]*$', '', text)
    text = text.strip(' .!')
    statuses = [c for c in columns if c in ('상태', '주문상태', '주문 상태')]
    if text in ('완료 주문', '완료 주문만') and len(statuses) == 1:
        text = statuses[0] + ' = 완료'
    # Semicolons/newlines avoid confusing quoted values or thousands separators
    # with conjunctions. Nothing unparsed is allowed to reach execution.
    parts = re.split(r'[;\n]+', text)
    filters, sorts = [], []
    fields = '|'.join(re.escape(c) for c in sorted(columns, key=len, reverse=True))
    if not fields:
        raise ValueError(HELP)
    for part in parts:
        match = re.fullmatch(rf'\s*({fields})\s+(?:(숫자|문자)\s+)?(오름차순|내림차순)(?:\s*정렬)?\s*', part)
        if match:
            sorts.append(dict(field=match[1], direction='ascending' if match[3] == '오름차순' else 'descending',
                              value_type='number' if match[2] == '숫자' else 'text'))
            continue
        match = re.fullmatch(rf'\s*({fields})\s*(>=|<=|!=|=|>|<)\s*(.+?)\s*', part)
        if match:
            field, operator, value = match.groups()
            op = {'=':'eq', '!=':'ne', '>=':'gte', '<=':'lte', '>':'gt', '<':'lt'}[operator]
        else:
            match = re.fullmatch(rf'\s*({fields})(?:이|가|은|는)?\s+([-+]?\d[\d,]*(?:\.\d+)?)\s*(이상|이하|초과|미만)(?:만)?\s*', part)
            if not match:
                raise ValueError(HELP)
            field, value, word = match.groups()
            op = {'이상':'gte', '이하':'lte', '초과':'gt', '미만':'lt'}[word]
        quoted = len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]
        if quoted:
            value = value[1:-1]
        numeric = re.fullmatch(r'[-+]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', value)
        if op not in ('eq', 'ne') and (not numeric or quoted):
            raise ValueError(HELP)
        # Equality is literal text, preserving IDs such as 00123. Ordered
        # comparisons are numeric. The existing executor validates cell values.
        kind = 'number' if op not in ('eq', 'ne') else 'text'
        if kind == 'number':
            value = float(value.replace(',', ''))
        elif not quoted and (re.search(r'\s|[<>=!;]', value) or re.search(r'해줘|해주세요|이상|이하|정렬', value)):
            raise ValueError(HELP)
        filters.append(dict(field=field, operator=op, value=value, compare_field=None, value_type=kind))
    if not filters and not sorts:
        raise ValueError(HELP)
    return ExecutionPlan(filters=filters, sort=sorts, match='all', missing_policy='error' if filters else 'keep',
                         duplicate_policy='keep', duplicate_field=None, output_format='csv').model_dump()


def apply_table_request(agent, request, storage, file_ids):
    config = agent['config']
    if not table_run_request_notice(config, request, agent.get('workflow_dependencies')):
        return agent, False
    # Old clients send descriptive launch text. Continue to report that such
    # text is not a condition; explicit modification requests must be parsed.
    if not re.search(r'[<>=]|필터|정렬|오름차순|내림차순|이상|이하|초과|미만|만\b|추가|계산|변경', request):
        return agent, False
    workflow = config.get('workflow') or {}
    from workflow_engine.execution_path import execution_path
    try:
        path = execution_path(workflow)
    except (ValueError, KeyError):
        raise ValueError(HELP) from None
    allowed = {'read_table', 'calculate_columns', 'filter_sort', 'write_result', 'finish'}
    if (len(path) >= 15 or len(path) != len(workflow['nodes']) or any(n['tool'] not in allowed or n['settings'].get('operation') for n in path)
            or sum(n['tool'] == 'read_table' for n in path) != 1
            or sum(n['tool'] == 'write_result' for n in path) != 1):
        raise ValueError(HELP)
    writer = next(n for n in path if n['tool'] == 'write_result')
    before = next((e for e in workflow['edges'] if e['target'] == writer['id']), None)
    binding = next((b for b in writer['bindings'] if b['input'] == 'table'), None)
    if not before or not binding:
        raise ValueError(HELP)
    from workflow_engine.executors.sources import source_file_ids
    reader = next(n for n in path if n['tool'] == 'read_table')
    ids = source_file_ids(reader, file_ids or [], storage, config)
    if len(ids) != 1:
        raise ValueError(HELP)
    columns = list(storage.asset(ids[0]).get('columns', []))
    for n in path:
        if n['id'] == writer['id']:
            break
        settings = n['settings']
        if n['tool'] == 'calculate_columns':
            if settings.get('table_operation') == 'rename':
                columns = [settings['rename_to'] if c == settings['rename_from'] else c for c in columns]
            elif settings.get('output_column') and settings['output_column'] not in columns:
                columns.append(settings['output_column'])
    plan = _criteria(request, columns)
    result = deepcopy(agent)
    target = result['config']['workflow']
    identifier = 'runtime_conditions'
    while any(n['id'] == identifier for n in target['nodes']):
        identifier += '_'
    target['nodes'].append(dict(id=identifier, tool='filter_sort', label='이번 실행의 추가 조건',
        settings=NodeSettings(file_ids=[], fields=[], query=None, instructions=None, format=None, question=None,
            condition_field=None, condition_operator=None, execution_plan=plan).model_dump(), bindings=[deepcopy(binding)]))
    target_writer = next(n for n in target['nodes'] if n['id'] == writer['id'])
    next(b for b in target_writer['bindings'] if b['input'] == 'table').update(node_id=identifier, output='table')
    next(e for e in target['edges'] if e['target'] == writer['id'])['target'] = identifier
    target['edges'].append(dict(source=identifier, target=writer['id'], branch='next'))
    if 'filter_sort' not in result['config']['tool_ids']:
        result['config']['tool_ids'].append('filter_sort')
    return result, True
