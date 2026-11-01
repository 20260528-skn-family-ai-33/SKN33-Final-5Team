"""Adapt existing v4 table operations to the agent-builder node contract."""
from workflow_engine.table_formula import formula_columns

def execute(node, state, storage, config, llm):
    from workflow_engine.unified_tools.dispatcher import execute as execute_unified
    inputs = {b['input']: state['outputs'][b['node_id']][b['output']] for b in node['bindings']}
    table = inputs['table']
    settings = node['settings']
    operation = settings.get('table_operation')
    columns = table['columns']
    if operation == 'compute':
        formula = settings.get('calculation_formula')
        output = settings.get('output_column')
        if not isinstance(output, str) or not output.strip():
            raise ValueError('새 열 이름이 필요합니다.')
        if output in columns:
            raise ValueError('이미 존재하는 열은 덮어쓸 수 없습니다.')
        if formula_columns(formula) - set(columns):
            raise ValueError('수식에 표에 없는 열이 있습니다.')
        result = execute_unified('calculate_columns', 'compute',
            {'table':table, 'output_column':output, 'formula':formula})['table']
        result['columns'] = [*columns, output]
    elif operation == 'rename':
        old, new = settings.get('rename_from'), settings.get('rename_to')
        if not isinstance(old, str) or not old.strip() or not isinstance(new, str) or not new.strip():
            raise ValueError('기존 열과 바꿀 열 이름이 필요합니다.')
        if old not in columns or (new != old and new in columns):
            raise ValueError('기존 열이 없거나 바꿀 이름이 중복됩니다.')
        result = execute_unified('calculate_columns', 'rename_columns',
            {'table':table, 'mapping':{old:new}})['table']
    elif operation == 'parse_dates':
        fields = settings.get('fields'); fmt = settings.get('date_input_format'); zone = settings.get('date_timezone')
        if not isinstance(fields, list) or not fields or any(not isinstance(f, str) or f not in columns for f in fields):
            raise ValueError('날짜로 변환할 기존 열을 선택하세요.')
        if not isinstance(fmt, str) or not fmt.strip() or not isinstance(zone, str) or not zone.strip():
            raise ValueError('날짜 입력 형식과 시간대가 필요합니다.')
        result = execute_unified('calculate_columns', 'parse_dates',
            {'table':table, 'columns':fields, 'input_format':fmt, 'timezone':zone})['table']
        result['columns'] = list(columns)
    else:
        raise ValueError('열 계산, 이름 변경 또는 날짜 변환을 선택하세요.')
    # Keep file provenance and review markers so later approval guards remain effective.
    return {'table': {**table, **result}}
