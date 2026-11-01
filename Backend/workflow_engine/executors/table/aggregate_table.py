import json,re


def execute(node,state,storage,config,llm):
    tool=node["tool"];settings=node["settings"];inputs={b["input"]:state["outputs"][b["node_id"]][b["output"]] for b in node["bindings"]}
    table = inputs['table']
    fields = settings['fields']
    if any((f not in table['columns'] for f in fields)):
        raise ValueError('집계할 열이 실제 자료에 없습니다.')
    from decimal import Decimal, InvalidOperation
    operation = settings.get('aggregation') or 'count'
    column = 'sum' if operation == 'sum' else 'count'
    sum_field = settings.get('sum_field')
    if operation == 'sum' and sum_field not in table['columns']:
        raise ValueError('합계할 숫자 열이 없습니다.')
    if column in fields:
        raise ValueError('집계 결과 열과 그룹 열이 충돌합니다.')
    plan = settings.get('execution_plan') or config.get('execution_plan') or {}
    missing_policy = plan.get('missing_policy', 'keep')
    groups = {}
    for row in table['rows']:
        missing = any(row.get(f) is None or (isinstance(row.get(f), str) and not row[f].strip()) for f in fields)
        if missing and missing_policy in ['error', 'ask']:
            raise ValueError('그룹 열에 빈 값이 있습니다. 결측 처리 정책을 확인하세요.')
        if missing and missing_policy == 'exclude':
            continue
        key = json.dumps([row.get(f) for f in fields], ensure_ascii=False)
        if key not in groups:
            groups[key] = {**{f: row.get(f) for f in fields}, column: Decimal(0) if operation == 'sum' else 0}
        amount = 1
        if operation == 'sum':
            value = row.get(sum_field)
            if value is None or isinstance(value, bool) or (not str(value).strip()):
                raise ValueError('합계 열에 빈 값 또는 잘못된 숫자가 있습니다.')
            try:
                number = str(value).strip()
                # Accept conventional thousands grouping, reject malformed separators.
                if ',' in number:
                    if not re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?', number):
                        raise InvalidOperation
                    number = number.replace(',', '')
                amount = Decimal(number)
            except InvalidOperation:
                raise ValueError('합계 열에 숫자가 아닌 값이 있습니다.') from None
            if not amount.is_finite():
                raise ValueError('합계 열에는 유한한 숫자가 필요합니다.')
        groups[key][column] += amount
    rows = list(groups.values()) if fields else [groups.get('[]', {column: 0})]
    if operation == 'sum':
        for row in rows:
            amount = row[column]
            row[column] = int(amount) if amount == int(amount) else float(amount)
            import math
            if not math.isfinite(row[column]):
                raise ValueError('합계 결과가 숫자 범위를 초과했습니다.')
    result = {'rows': rows, 'columns': fields + [column], 'file_id': table['file_id']}
    if table.get('review_report') is True:
        result['review_report'] = True
    return {'table': result}
