"""Use sealed request criteria as decoder constraints, without repairing raw output silently."""
from copy import deepcopy
import os

from .requirement_ledger import ledger_fingerprint, plan_digest


TOOLS={'compute':'calculate_columns','parse_dates':'calculate_columns','filter':'filter_sort','sort':'filter_sort',
       'policy':'filter_sort','aggregate':'aggregate_table','join':'join_tables',
       'export':'write_result','read':'read_table','rename':'calculate_columns',
       'fill_missing':'filter_sort','select_fields':'filter_sort',
       'document_analysis':'compose_answer','human_review':'ask_user'}


def required_operation_variants(specs,node_schema,binding_schema,variable_names,available=()):
    """Expose required registered operations absent from a tool's native settings."""
    from workflow_engine.catalog.canonical import operation_variants
    operations={s['kind'] for s in specs} & {'fill_missing','select_fields'}
    if not operations or 'filter_sort' in available:return []
    names={'fill_missing':'handle_missing','select_fields':'select_columns'}
    wanted={names[k] for k in operations}
    return [v for v in operation_variants(node_schema,binding_schema,{'filter_sort'},variable_names)
            if v['properties']['settings']['properties']['operation'].get('enum',[None])[0] in wanted]


def generation_mode():
    value=os.getenv('A4B_REQUIREMENT_GENERATION_MODE','baseline')
    if value not in ('baseline','bounded'):
        raise ValueError('A4B_REQUIREMENT_GENERATION_MODE는 baseline/bounded이어야 합니다.')
    return value


def output_budget(order,mode):
    return 4096 if mode!='bounded' or len(order)<=4 else min(8192,1024*(len(order)+1))


def generation_requirements(state):
    ledger=state.get('requirement_ledger')
    if (not state.get('requirement_required') or not ledger
            or state.get('requirement_fingerprint')!=ledger_fingerprint(state)
            or state.get('requirement_digest')!=plan_digest(ledger)):
        return [],[]
    specs=[t['spec'] for t in ledger['tasks']]
    complete=not any(s['kind'] not in TOOLS or s['kind']=='export' and s['format'] not in ('csv','json') for s in specs)
    # An unsupported requirement still blocks approval, but must not hide the
    # known calculation/document contracts from the model's tool selection.
    if not complete:return specs,[]
    # select_columns consumes/returns records, while the native table chain
    # reads/writes Table values. A task-to-tool list cannot reserve its required
    # adapters or records exporter. Keep the ordinary generation grammar for
    # these requests; all sealed specs still reach the coverage/save gate.
    if any(s['kind']=='select_fields' for s in specs):return specs,[]
    assets={a['id']:a for a in state.get('resources',[])}
    files=[]
    for s in specs:
        ids=(s.get('left_sources',[])+s.get('right_sources',[]) if s['kind']=='join' else s.get('file_ids',[]))
        for id in ids:
            if id not in files:files.append(id)
    if not files and len(assets)==1:files=list(assets)
    if (not files or any(id not in assets or assets[id].get('kind')!='table' for id in files)
            or state.get('required_node_refs')):return specs,[]
    order=['read_table']*len(files)
    previous_kind='read'
    for s in specs:
        if s['kind'] in ('read','policy'):continue
        tool=TOOLS[s['kind']]
        if s['kind'] in ('filter','sort') and order[-1:]==['filter_sort'] and previous_kind in ('filter','sort'):continue
        order.append(tool)
        previous_kind=s['kind']
    if any(s['kind']=='policy' for s in specs) and 'filter_sort' not in order:
        return specs,[]  # no invented policy placement
    return specs,order if 1<=len(order)<=15 else []


def _literal(value):
    if isinstance(value,dict):
        return dict(type='object',properties={k:_literal(v) for k,v in value.items()},
                    required=list(value),additionalProperties=False)
    if isinstance(value,list):
        if not value:return dict(type='array',maxItems=0)
        if len(value)==1:return dict(type='array',items=_literal(value[0]),minItems=1,maxItems=1)
        return dict(type='array',items={'anyOf':[_literal(v) for v in value]},
                    minItems=len(value),maxItems=len(value),uniqueItems=True)
    kind='null' if value is None else 'boolean' if isinstance(value,bool) else 'number' if isinstance(value,(int,float)) else 'string'
    return {'type':kind,'enum':[value]}


def constrain_request_settings(schema,state):
    """Carry sealed criteria to matching native settings, never create nodes."""
    result=deepcopy(schema);specs,order=generation_requirements(state)
    if not specs:return result
    analyses=list(dict.fromkeys(s['request'] for s in specs if s['kind']=='document_analysis'))
    human=any(s['kind']=='human_review' for s in specs)
    for variant in result['$defs']['WorkflowNode']['anyOf']:
        tool=variant['properties']['tool'].get('enum',[None])[0]
        settings=variant['properties']['settings']['properties']
        if settings.get('operation',{}).get('enum'):continue
        if tool=='compose_answer' and analyses:settings['instructions']=_literal('\n'.join(analyses))
        if tool=='ask_user' and human:
            settings['instructions']={'type':'null'}
            variant['properties']['bindings'].update(minItems=1)
    if order:
        nodes=result['$defs']['WorkflowDefinition']['properties'].get('nodes')
        if nodes is not None:nodes['minItems']=max(nodes.get('minItems',1),len(order))
    return result


def constrain_uploaded_join_inputs(schema,state):
    """Uploaded Tables must use graph inputs, not duplicate constant Table values.

    This is an input contract, not a prescribed graph: the model still chooses
    every node, source reader, policy value and connection. Unsealed/ambiguous
    sources and explicit literal-table calculations keep the general grammar.
    """
    result=deepcopy(schema);specs,order=generation_requirements(state)
    joins=[s for s in specs if s['kind']=='join']
    if len(joins)!=1 or (state.get('requirement_ledger') or {}).get('unresolved'):return result
    join=joins[0];left=join.get('left_sources',[]);right=join.get('right_sources',[])
    assets={r['id']:r for r in state.get('resources',[])}
    if (len(left)!=1 or len(right)!=1 or left==right
            or any(id not in assets or assets[id].get('kind')!='table' for id in left+right)):
        return result
    compatible=[]
    for variant in result['$defs']['WorkflowNode']['anyOf']:
        if variant['properties']['tool'].get('enum')!=['join_tables']:
            compatible.append(variant);continue
        settings=variant['properties']['settings']['properties']
        # join_records consumes/returns records and cannot implement this
        # Table join contract (including null-key and max-row policies).
        if settings.get('operation',{}).get('enum')!=['join']:continue
        compatible.append(variant)
        arguments=settings['arguments_json']
        if arguments.get('type')!='object':continue
        for name in ('left','right'):arguments['properties'].pop(name,None)
        from workflow_engine.catalog.canonical import arguments_spec, operation_spec
        required=arguments_spec('join_tables',operation_spec('join_tables','join'))
        arguments['required']=list(dict.fromkeys(
            [name for name in arguments.get('required',[]) if name not in ('left','right')]
            +[name for name,needed in required.items() if needed and name not in ('left','right')]))
        binding=variant['properties']['bindings']
        binding.update(minItems=2,maxItems=2)
        binding['items']['properties']['input']={'type':'string','enum':['left','right']}
    result['$defs']['WorkflowNode']['anyOf']=compatible
    roles=[s['kind'] for s in specs]
    minimum=(len(order) if order else 2+sum(k in ('compute','parse_dates','aggregate','join','export') for k in roles)
             +int(any(k in ('filter','sort','policy') for k in roles)))
    nodes=result['$defs'].get('WorkflowDefinition',{}).get('properties',{}).get('nodes')
    if nodes is not None:nodes['minItems']=max(nodes.get('minItems',1),minimum)
    return result


def constrain_generation(schema,state):
    result=deepcopy(schema);specs,order=generation_requirements(state)
    if not specs:return result
    joins=[s for s in specs if s['kind']=='join']
    variants=result['$defs']['WorkflowNode']['anyOf']
    if len(joins)==1:
        for variant in variants:
            if variant['properties']['tool'].get('enum')!=['join_tables']:continue
            settings=variant['properties']['settings']['properties']
            if settings.get('operation',{}).get('enum')!=['join']:continue
            constants={k:joins[0][k] for k in ('keys','how','cardinality','null_keys','max_rows')}
            # Table values must come through bindings, never duplicated literals.
            settings['arguments_json']=_literal(constants)
            binding=variant['properties']['bindings']
            binding['minItems']=2;binding['maxItems']=2
            binding['items']['properties']['input']={'type':'string','enum':['left','right']}
    if order:
        # The optional finish node cannot substitute for a requested processing step.
        result['$defs']['WorkflowNode']['anyOf']=[v for v in variants if v['properties']['tool'].get('enum')!=['finish']]
        workflow=result['$defs']['WorkflowDefinition']['properties']
        if 'nodes' in workflow:workflow['nodes']['minItems']=max(workflow['nodes'].get('minItems',1),len(order))
        if 'next_steps' in workflow:workflow['next_steps']['minItems']=max(workflow['next_steps'].get('minItems',0),len(order)-1)
        computes=[s for s in specs if s['kind']=='compute']
        dates=[s for s in specs if s['kind']=='parse_dates']
        aggregates=[s for s in specs if s['kind']=='aggregate']
        filters=[s for s in specs if s['kind']=='filter'];sorts=[s for s in specs if s['kind']=='sort']
        exports=[s for s in specs if s['kind']=='export']
        for variant in result['$defs']['WorkflowNode']['anyOf']:
            tool=variant['properties']['tool'].get('enum',[None])[0]
            properties=variant['properties']['settings']['properties']
            if properties.get('operation',{}).get('enum'):continue
            values={}
            if tool=='calculate_columns' and len(computes)==1 and not dates:
                values=dict(table_operation='compute',calculation_formula=computes[0]['formula'],output_column=computes[0]['output_column'])
                values.update({k:None for k in ('rename_from','rename_to','date_input_format','date_timezone') if k in properties})
                if 'fields' in properties:values['fields']=[]
            if tool=='calculate_columns' and len(dates)==1 and not computes:
                values=dict(table_operation='parse_dates',fields=dates[0]['fields'],
                            date_input_format=dates[0]['input_format'],date_timezone=dates[0]['timezone'])
                values.update({k:None for k in ('calculation_formula','output_column','rename_from','rename_to') if k in properties})
            if tool=='aggregate_table' and len(aggregates)==1:
                values={k:aggregates[0][k] for k in ('fields','aggregation','sum_field')}
            if tool=='filter_sort' and order.count('filter_sort')==1 and len(filters)<=1 and len(sorts)<=1:
                plan=dict(filters=filters[0]['filters'] if filters else [],sort=sorts[0]['sort'] if sorts else [],
                    match=filters[0]['match'] if filters else 'all',missing_policy='keep',duplicate_policy='keep',
                    duplicate_field=None,output_format=exports[0]['format'] if len(exports)==1 else 'json')
                policy={};conflict=False
                for s in specs:
                    if s['kind']!='policy':continue
                    for key in ('missing_policy','duplicate_policy','duplicate_field'):
                        if s[key] is None:continue
                        if key in policy and policy[key]!=s[key]:conflict=True
                        policy[key]=s[key]
                if not conflict:
                    plan.update(policy);values={'execution_plan':plan}
            if tool=='write_result' and len(exports)==1:values={'format':exports[0]['format']}
            for key,value in values.items():
                if key in properties:properties[key]=_literal(value)
    return result


def constrain_ordered_steps(schema,state):
    """For the supported single table chain, reserve a slot for each required role.

    The model still writes labels, native settings and data bindings. Raw model
    output is validated as received; no omitted task is appended after the fact.
    """
    specs,order=generation_requirements(state)
    if not order:return deepcopy(schema)
    result=deepcopy(schema);definitions=result['$defs']
    if 'next_steps' not in definitions['WorkflowDefinition']['properties']:return result
    variants=definitions['WorkflowNode']['anyOf'];prefix=[]
    first=definitions['FirstBusinessStep']['anyOf']
    first=[v for v in first if v['properties']['tool'].get('enum')==[order[0]]]
    if not first:return deepcopy(schema)
    definitions['FirstBusinessStep']={'anyOf':first}
    for tool in order[1:]:
        choices=deepcopy([v for v in variants if v['properties']['tool'].get('enum')==[tool]])
        # Prefer the native contract already checked by this requirement version.
        native=[v for v in choices if v['properties']['settings']['properties'].get('operation',{}).get('type')=='null']
        if native:choices=native
        if not choices:return deepcopy(schema)
        prefix.append({'anyOf':choices})
    joins=[s for s in specs if s['kind']=='join']
    files=[]
    if len(joins)==1 and len(joins[0]['left_sources'])==len(joins[0]['right_sources'])==1:
        files=joins[0]['left_sources']+joins[0]['right_sources']
    elif order.count('read_table')==1 and len(state.get('resources',[]))==1:
        files=[state['resources'][0]['id']]
    read_index=0
    for tool,slot in zip(order,[{'anyOf':first},*prefix]):
        if tool!='read_table':continue
        if read_index<len(files):
            for choice in slot['anyOf']:
                properties=choice['properties']['settings']['properties']
                if 'file_ids' in properties:properties['file_ids']=_literal([files[read_index]])
        read_index+=1
    steps=definitions['WorkflowDefinition']['properties']['next_steps']
    # The team's /v1 JSON compiler ignores prefixItems. Required numbered
    # object fields are supported; compile_workflow restores the stored array.
    if prefix:
        slots={'step_'+str(index):slot for index,slot in enumerate(prefix,2)}
        definitions['WorkflowDefinition']['properties']['next_steps']={
            'type':'object','properties':slots,'required':list(slots),'additionalProperties':False}
    else:
        steps.update(items={},minItems=0,maxItems=0)
    if (state.get('capability_assessment') or {}).get('decision')=='existing_tools':
        result['properties']['workflow']={'$ref':'#/$defs/WorkflowDefinition'}
        for key in ('questions','question_options','unsupported'):
            result['properties'][key]['maxItems']=0
    # Keep source evidence compact instead of repeating the whole request per node.
    result['properties']['requirements'].update(minItems=1,maxItems=1)
    result['$defs']['Requirement']['properties']['description']['maxLength']=160
    request='\n'.join(m['content'] for m in state.get('messages',[]) if m['role']=='user')
    if 0<len(request)<=2000:result['properties']['instructions']=_literal(request)
    for field,limit in [('purpose',300),('instructions',2000),('explanation',240)]:
        result['properties'][field]['maxLength']=min(result['properties'][field].get('maxLength',limit),limit)
    return result
