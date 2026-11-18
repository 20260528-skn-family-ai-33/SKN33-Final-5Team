"""Decode the proven sum/count contract using existing table operations."""
from copy import deepcopy

from contracts.models import ExecutionPlan
from .grouped_totals_request import grouped_totals_packet, grouped_totals_filename, validate_grouped_totals
from .requirement_generation import _literal, generation_requirements

# An execution safety ceiling, never a row selection or a silently truncated result.
MAX_GROUPED_ROWS = 100000


def grouped_totals_steps(state):
    specs, _ = generation_requirements(state)
    if not specs or grouped_totals_packet(state) is None:
        return []
    validate_grouped_totals(state['requirement_ledger'], state)
    steps = []
    def bind(index, port='table', name='table'):
        return [dict(input=name, step=index, output=port)]
    def add(tool, settings, bindings, operation=None):
        steps.append(dict(tool=tool, operation=operation, settings=settings, bindings=bindings))
    def plan(**kw):
        return ExecutionPlan.model_validate({**dict(missing_policy='keep', duplicate_policy='keep',
            duplicate_field=None, output_format='json', filters=[], sort=[], match='all'), **kw}).model_dump(mode='json')
    add('read_table', dict(file_ids=specs[0]['file_ids']), [])
    add('filter_sort', dict(execution_plan=plan(filters=specs[1]['filters'], match='all', missing_policy='exclude')), bind(1))
    add('calculate_columns', dict(table_operation='compute', calculation_formula=specs[2]['formula'],
                                 output_column=specs[2]['output_column']), bind(2))
    for spec, rename in ((specs[3], specs[4]), (specs[5], specs[6])):
        add('aggregate_table', {**{k:spec[k] for k in ('fields','aggregation','sum_field')},
                                'execution_plan':plan()}, bind(3))
        add('calculate_columns', dict(table_operation='rename', rename_from=rename['source'],
                                     rename_to=rename['target']), bind(len(steps)))
    add('join_tables', dict(arguments_json=dict(left={'$binding':'left'}, right={'$binding':'right'},
        keys=specs[3]['fields'], how='inner', cardinality='one_to_one', null_keys='error', max_rows=MAX_GROUPED_ROWS)),
        bind(5, name='left') + bind(7, name='right'), 'join')
    add('filter_sort', dict(execution_plan=plan(sort=specs[7]['sort'])), bind(8))
    add('json_transform', dict(arguments_json={'path':['rows']}, output_port='records'), bind(9, name='value'), 'lookup')
    add('filter_sort', dict(arguments_json={'columns':specs[8]['fields']}), bind(10, 'records', 'rows'), 'select_columns')
    add('export_result', dict(arguments_json={'path':grouped_totals_filename(state), 'columns':specs[8]['fields']}), bind(11, 'records', 'rows'), 'write_csv')
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
