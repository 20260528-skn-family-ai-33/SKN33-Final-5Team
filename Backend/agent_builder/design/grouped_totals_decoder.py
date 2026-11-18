"""Decode the proven sum/count contract using existing table operations."""
from copy import deepcopy

from contracts.models import ExecutionPlan
from .grouped_totals_request import validate_grouped_totals
from .requirement_generation import _literal, generation_requirements

# An execution safety ceiling, never a row selection or a silently truncated result.
MAX_GROUPED_ROWS = 100000


def grouped_totals_steps(state):
    from .grouped_totals_contract import grouped_contract, checked_specs, contract_filename
    specs, _ = generation_requirements(state)
    contract = grouped_contract(state)
    if not specs or contract is None:
        return []
    validate_grouped_totals(state['requirement_ledger'], state)
    if len(contract['formats']) > 1:
        raise ValueError('복수 집계 결과 파일의 저장 경로는 아직 검증하지 못했습니다.')
    roles = [role for role, _ in checked_specs(contract, state['requirement_ledger']['tasks'])]
    named = dict(zip(roles, specs))
    steps = []
    def bind(index, port='table', name='table'):
        return [dict(input=name, step=index, output=port)]
    def add(tool, settings, bindings, operation=None):
        steps.append(dict(tool=tool, operation=operation, settings=settings, bindings=bindings))
        return len(steps)
    def plan(**kw):
        return ExecutionPlan.model_validate({**dict(missing_policy='keep', duplicate_policy='keep',
            duplicate_field=None, output_format='json', filters=[], sort=[], match='all'), **kw}).model_dump(mode='json')
    read = add('read_table', dict(file_ids=named['read']['file_ids']), [])
    filtered = add('filter_sort', dict(execution_plan=plan(filters=named['filter']['filters'], missing_policy='exclude')), bind(read))
    computed = add('calculate_columns', dict(table_operation='compute', calculation_formula=named['compute']['formula'],
        output_column=named['compute']['output_column']), bind(filtered))
    ends = []
    for role, rename_role in [('sum', 'total_name'), ('count', 'count_name')]:
        spec = named[role]
        end = add('aggregate_table', {**{k:spec[k] for k in ('fields','aggregation','sum_field')},
                                    'execution_plan':plan()}, bind(computed))
        if rename_role in named:
            rename = named[rename_role]
            end = add('calculate_columns', dict(table_operation='rename', rename_from=rename['source'],
                rename_to=rename['target']), bind(end))
        ends.append(end)
    output = add('join_tables', dict(arguments_json=dict(left={'$binding':'left'}, right={'$binding':'right'},
        keys=named['sum']['fields'], how='inner', cardinality='one_to_one', null_keys='error', max_rows=MAX_GROUPED_ROWS)),
        bind(ends[0], name='left') + bind(ends[1], name='right'), 'join')
    if 'sort' in named:
        output = add('filter_sort', dict(execution_plan=plan(sort=named['sort']['sort'])), bind(output))
    output = add('json_transform', dict(arguments_json={'path':['rows']}, output_port='records'), bind(output, name='value'), 'lookup')
    output = add('filter_sort', dict(arguments_json={'columns':named['select']['fields']}), bind(output, 'records', 'rows'), 'select_columns')
    for fmt in contract['formats']:
        if fmt not in ('csv', 'json'):
            raise ValueError('이 집계 계약의 출력 형식을 지원하지 않습니다.')
        arguments = {'path':contract_filename(contract, fmt)}
        if fmt == 'csv':
            arguments['columns'] = named['select']['fields']
        add('export_result', dict(arguments_json=arguments), bind(output, 'records', 'rows' if fmt == 'csv' else 'value'), 'write_' + fmt)
    return steps


def constrain_grouped_totals(schema, state):
    steps = grouped_totals_steps(state)
    if not steps or 'next_steps' not in schema['$defs']['WorkflowDefinition']['properties']:
        return schema
    result = deepcopy(schema)
    variants = result['$defs']['WorkflowNode']['anyOf']
    slots = []
    for step in steps:
        choices = []
        for variant in variants:
            props = variant['properties']
            settings = props['settings']['properties']
            if props['tool'].get('enum') != [step['tool']]:
                continue
            operation = settings.get('operation', {}).get('enum')
            if (step['operation'] is None and operation) or (step['operation'] is not None and operation != [step['operation']]):
                continue
            choice = deepcopy(variant)
            p = choice['properties']
            p['bindings'] = _literal(step['bindings'])
            for key, value in step['settings'].items():
                p['settings']['properties'][key] = _literal(value)
            choices.append(choice)
        if not choices:
            return schema  # No invented tool or runtime operation.
        slots.append({'anyOf':choices})
    for variant in slots[0]['anyOf']:
        variant['properties'].pop('bindings')
        variant['required'].remove('bindings')
    result['$defs']['FirstBusinessStep'] = slots[0]
    fields = {'step_'+str(i):slot for i,slot in enumerate(slots[1:], 2)}
    result['$defs']['WorkflowDefinition']['properties']['next_steps'] = dict(
        type='object', properties=fields, required=list(fields), additionalProperties=False)
    result['properties']['requirements'].update(minItems=1, maxItems=1)
    return result


def grouped_totals_issues(state, draft):
    """Recheck saved/drafted graphs, not just the constrained generation path."""
    steps = grouped_totals_steps(state)
    if not steps:
        return []
    from workflow_engine.execution_path import execution_path
    from workflow_engine.catalog.canonical import parse_arguments
    nodes = execution_path(draft['workflow'])
    if len(nodes) != len(steps):
        return ['합계·행 개수의 공통 원본과 최종 결합 경로가 요청 계약과 다릅니다.']
    for node, step in zip(nodes, steps):
        settings = node['settings']
        bindings = [dict(input=b['input'], node_id=nodes[b['step']-1]['id'], output=b['output']) for b in step['bindings']]
        if (node['tool'] != step['tool'] or settings.get('operation') != step['operation']
                or sorted(node['bindings'], key=lambda b:b['input']) != sorted(bindings, key=lambda b:b['input'])):
            return ['합계·행 개수는 같은 선별 원본을 사용하고 두 결과 모두 최종 CSV에 연결되어야 합니다.']
        for key, value in step['settings'].items():
            actual = parse_arguments(settings) if key == 'arguments_json' else settings.get(key)
            if key == 'execution_plan':
                actual = settings.get(key) or draft.get('execution_plan')
            if actual != value:
                return ['합계·행 개수의 실행 설정이 원문에서 확인한 조건과 다릅니다: '+key]
    return []
