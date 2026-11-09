"""Correct an invalid aggregate column against its explicit source table."""
from copy import deepcopy
import json


def column_contract(workflow, resources):
    tables = {r['id']: r for r in resources if r.get('kind') == 'table'}
    by = {n['id']: n for n in workflow['nodes']}; pending = []
    for node in workflow['nodes']:
        settings = node['settings']
        if node['tool'] != 'aggregate_table' or settings.get('operation') or settings.get('aggregation') != 'sum':
            continue
        bindings = [b for b in node['bindings'] if b['input'] == 'table' and b['output'] == 'table']
        if len(bindings) != 1:
            continue
        source = by.get(bindings[0]['node_id'])
        if not source or source['tool'] != 'read_table' or source['settings'].get('operation'):
            continue  # Computed/joined/ambiguous columns need full planning.
        ids = source['settings'].get('file_ids') or list(tables)
        if len(ids) != 1 or ids[0] not in tables:
            continue
        columns = tables[ids[0]].get('columns', [])
        if not columns or settings.get('sum_field') in columns:
            continue
        pending.append({'node_id': node['id'], 'invalid_column': settings.get('sum_field'),
                        'source_columns': columns, 'group_columns': settings.get('fields', [])})
    return pending


def correct_sum_columns(workflow, state, llm):
    pending = column_contract(workflow, state['resources'])
    if not pending:
        return workflow, []
    schema = {'title': 'AggregateColumns', 'type': 'object', 'additionalProperties': False,
              'properties': {n['node_id']: {'enum': [None, *n['source_columns']]} for n in pending},
              'required': [n['node_id'] for n in pending]}
    response = llm.with_structured_output(schema, method='json_schema', strict=True).invoke([
        {'role': 'system', 'content': '사용자가 요청한 합계 대상 열을 실제 원본 표의 열 이름 중에서 선택하세요. '
         '원래 요청의 의미를 바꾸지 마세요. 합계할 열을 특정할 수 없으면 null입니다. 다른 설정은 변경하지 않습니다.'},
        {'role': 'user', 'content': json.dumps({'user_messages': [m['content'] for m in state['messages'] if m['role'] == 'user'],
                                               'nodes': pending}, ensure_ascii=False)}])
    if hasattr(response, 'model_dump'):
        response = response.model_dump()
    if not isinstance(response, dict) or set(response) != set(schema['properties']):
        raise ValueError('합계 열 수정 응답의 범위가 다릅니다.')
    result = deepcopy(workflow); by = {n['id']: n for n in result['nodes']}; changes = []
    for item in pending:
        column = response[item['node_id']]
        if not isinstance(column, str) or column not in item['source_columns']:
            raise ValueError('사용자 요청에 맞는 실제 합계 열을 확인해야 합니다.')
        by[item['node_id']]['settings']['sum_field'] = column
        changes.append({'node_id': item['node_id'], 'field': 'sum_field', 'before': item['invalid_column'],
                        'after': column, 'rule': 'model_selected_explicit_source_column'})
    return result, changes
