"""Decode a complete single-table contract through real projection adapters."""
from copy import deepcopy

from contracts.models import ExecutionPlan
from .requirement_generation import generation_requirements, _literal


def constrain_projected_table(schema,state):
    specs,_=generation_requirements(state)
    kinds=[s['kind'] for s in specs]
    assets=state.get('resources',[])
    if (len(assets)!=1 or assets[0].get('kind')!='table' or state.get('required_node_refs')
            or (state.get('requirement_ledger') or {}).get('unresolved')
            or not set(kinds)<={'read','compute','filter','sort','select_fields','export'}
            or any(kinds.count(k)>1 for k in ('read','compute','select_fields','export'))
            or kinds.count('select_fields')!=1 or kinds.count('export')!=1
            or kinds[-2:]!=['select_fields','export']):return schema
    exports=[s for s in specs if s['kind']=='export']
    if exports[0]['format']!='csv':return schema
    filters=[s for s in specs if s['kind']=='filter']
    if len(filters)>1 and any(s['match']!='all' for s in filters):return schema
    # Moving a calculation across a filter/sort can change errors and results.
    # Admit only an already ordered, complete ledger; preserve general plans.
    if 'compute' in kinds and any(k in ('filter','sort','policy') for k in kinds[:kinds.index('compute')]):return schema
    if 'sort' in kinds and 'filter' in kinds[kinds.index('sort'):]:return schema
    definitions=schema['$defs']
    if 'next_steps' not in definitions['WorkflowDefinition']['properties']:return schema
    variants=definitions['WorkflowNode']['anyOf']
    def choice(tool,operation=None):
        found=[]
        for v in variants:
            p=v['properties'];settings=p['settings']['properties']
            if p['tool'].get('enum')!=[tool]:continue
            op=settings.get('operation',{})
            if operation is not None and op.get('enum')==[operation]:found.append(deepcopy(v))
            elif operation is None and not op.get('enum'):found.append(deepcopy(v))
        return found
    steps=[]
    def append(tool,operation,settings,bindings):
        selected=choice(tool,operation)
        if not selected:raise LookupError(tool)
        for variant in selected:
            properties=variant['properties']
            properties['bindings']=_literal(bindings)
            for key,value in settings.items():properties['settings']['properties'][key]=_literal(value)
        steps.append({'anyOf':selected})
    def bind(name,port):return [dict(input=name,step=len(steps),output=port)]
    try:
        append('read_table',None,{'file_ids':[assets[0]['id']]},[])
        compute=next((s for s in specs if s['kind']=='compute'),None)
        if compute:
            append('calculate_columns',None,{'table_operation':'compute','calculation_formula':compute['formula'],
                                            'output_column':compute['output_column']},bind('table','table'))
        for spec in specs:
            if spec['kind'] not in ('filter','sort'):continue
            plan=ExecutionPlan.model_validate(dict(missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='json',
                filters=spec.get('filters',[]),match=spec.get('match','all'),sort=spec.get('sort',[]))).model_dump(mode='json')
            append('filter_sort',None,{'execution_plan':plan},bind('table','table'))
        fields=next(s['fields'] for s in specs if s['kind']=='select_fields')
        append('json_transform','lookup',{'arguments_json':{'path':['rows']},'output_port':'records'},bind('value','table'))
        append('filter_sort','select_columns',{'arguments_json':{'columns':fields}},bind('rows','records'))
        append('export_result','write_csv',{},bind('rows','records'))
        for variant in steps[-1]['anyOf']:
            args=variant['properties']['settings']['properties']['arguments_json']
            args['properties'].pop('rows',None)
            args['properties']['columns']=_literal(fields)
            args['required']=list(dict.fromkeys([k for k in args.get('required',[]) if k!='rows']+['columns']))
    except LookupError:return schema
    if len(steps)>15:return schema
    result=deepcopy(schema)
    first=steps[0]
    for variant in first['anyOf']:
        variant['properties'].pop('bindings');variant['required'].remove('bindings')
    result['$defs']['FirstBusinessStep']=first
    slots={'step_'+str(index):slot for index,slot in enumerate(steps[1:],2)}
    result['$defs']['WorkflowDefinition']['properties']['next_steps']={
        'type':'object','properties':slots,'required':list(slots),'additionalProperties':False}
    return result
