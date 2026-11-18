"""Generation-only native IDs/typed ports. Never rewrite a returned workflow."""
from copy import deepcopy
from workflow_engine.catalog.registry import PORTS, node_ports
from agent_builder.design.native_settings import NATIVE_SETTINGS, decoded_resource_tools

REQUIRED_INPUTS = {'filter_sort':{'table'}, 'write_result':{'table'}, 'aggregate_table':{'table'},
    'review_summary':{'reviews'}, 'search_document':{'documents'}, 'extract_information':{'documents'},
    'compose_answer':{'documents'}}
TABLE_PLAN_TOOLS = ('read_table','filter_sort','write_result','finish')
# ASCII form of workflow_errors' node ID rule; local grammars may not support \w.
NODE_ID_PATTERN = '^[a-zA-Z][a-zA-Z0-9_-]{0,39}$'
# Tools that end the flow (registry: finish "흐름을 끝냅니다"; the linear compiler
# keeps it last). A scoped grammar never offers their outputs as upstream inputs.
# Register any new terminal tool here; producers are otherwise derived from PORTS.
TERMINAL_TOOLS = ('finish',)


def typed_filter_conditions(schema):
    """Constrain constant-vs-column comparison without choosing user conditions."""
    variants = []
    base = schema['$defs']['FilterCondition']
    for kind, label in [('number','number'), ('string','text'), ('boolean','boolean')]:
        condition = deepcopy(base)
        props = condition['properties']
        props['value'] = {'type':kind}
        props['value_type'] = {'type':'string', 'enum':[label]}
        props['compare_field'] = {'type':'null'}
        props['operator']['enum'] = (['eq','ne'] if kind=='boolean' else
            ['eq','ne','lt','lte','gt','gte','contains'] if kind=='string' else ['eq','ne','lt','lte','gt','gte'])
        variants.append(condition)
    columns = deepcopy(base)
    columns['properties']['value'] = {'type':'null'}
    columns['properties']['compare_field'] = {'type':'string','minLength':1}
    columns['properties']['operator']['enum'] = ['eq','ne','lt','lte','gt','gte','contains']
    variants.append(columns)
    unary = deepcopy(base)
    unary['properties']['value'] = {'type':'null'}
    unary['properties']['compare_field'] = {'type':'null'}
    unary['properties']['operator']['enum'] = ['is_empty','not_empty']
    variants.append(unary)
    schema['$defs']['TypedFilterCondition'] = {'anyOf':variants}
    schema['$defs']['ExecutionPlan']['properties']['filters']['items'] = {'$ref':'#/$defs/TypedFilterCondition'}


def ready_table_slots(ledger):
    """Only complete, scoped table contracts may constrain READY generation."""
    ledger = ledger or {}
    operations = ledger.get('operations') or []
    required = {'filter': ('filter.field', 'filter.operator', 'filter.value'),
                'deduplicate': ('duplicate.field', 'duplicate.policy')}
    if (ledger.get('decision') != 'ready' or not operations or
            len(set(operations)) != len(operations) or not set(operations) <= set(required) or
            ledger.get('unresolved') or ledger.get('issues')):
        return None
    slots = {key: slot['value'] for key, slot in ledger.get('slots', {}).items()
             if slot.get('status') == 'specified' and 'value' in slot}
    if not all(key in slots for operation in operations for key in required[operation]):
        return None
    return slots


def literal_schema(value):
    """Use a single scalar branch; local grammars may ignore enum beside anyOf."""
    kind=('null' if value is None else 'boolean' if isinstance(value,bool) else
          'number' if isinstance(value,(int,float)) else 'string')
    return {'type':kind,'enum':[value]}


def table_plan_scope(state):
    from .output_selection_request import output_selection
    if output_selection(state):
        return False  # Column projection requires registered adapters beyond a native filter chain.
    return (bool(state.get('condition_enforced')) and not state.get('required_node_refs') and
            ready_table_slots(state.get('condition_ledger')) is not None and
            len([r for r in state.get('resources',[]) if r.get('kind')=='table']) <= 1)


def table_tool_issues(state):
    if not table_plan_scope(state):return []
    tools={n['tool'] for n in ((state.get('draft') or {}).get('workflow') or {}).get('nodes',[])}
    return (['확정된 선별·중복 제거 범위에 없는 도구가 추가됐습니다: '+', '.join(sorted(tools-set(TABLE_PLAN_TOOLS)))]
            if tools-set(TABLE_PLAN_TOOLS) else [])


def presort_request(request):
    """Return a separately confirmed presort, when the result has its own sort contract."""
    if keys := request.get('pre_sorts'):
        return {'kind': 'sorts', 'sorts': keys}
    if hint := request.get('pre_sort_hint'):
        return {'kind': 'unparsed', **hint}
    return None


def _sort_schema_bounds(request):
    if request['kind'] == 'sorts':
        keys = request['sorts']
        return [key['field'] for key in keys], [key['direction'] for key in keys], len(keys)
    direction = [request['direction']] if request.get('direction') else []
    return request.get('fields') or [], direction, 5


def constrain_sort(defs, plan, request, *, condition_name='RequestedSortCondition'):
    """Constrain a decoder's possible sorts to the confirmed table request.

    No written sort (or a withdrawn one) keeps sort empty. Otherwise the schema only
    limits which columns/directions may appear (exact request: those and at most that
    many; unparsed: what was read). It never forces a minimum, because the same plan
    shape is used by every filter_sort step (a dedup step after a sort step has none);
    the validator checks that the output is actually sorted.
    """
    kind = (request or {}).get('kind', 'none')
    plan['sort'].pop('minItems', None)
    if kind in ('none', 'cancelled'):
        plan['sort']['maxItems'] = 0
        return
    item = deepcopy(defs['SortCondition'])
    props = item['properties']
    separate_presort = presort_request(request)
    stages = (request, separate_presort) if separate_presort else (request,)
    fields, directions, lengths = zip(*(_sort_schema_bounds(stage) for stage in stages))
    for name, choices in (('field', fields), ('direction', directions)):
        if all(choices):
            props[name] = {'type': 'string', 'enum': list(dict.fromkeys(value for group in choices for value in group))}
    plan['sort']['maxItems'] = max(lengths)
    item['required'] = list(props)
    defs[condition_name] = item
    plan['sort']['items'] = {'$ref': '#/$defs/' + condition_name}


def split_sort_dedup_plans(defs, request):
    """Give the sort and duplicate-removal steps independent execution plans.

    The dedup step may carry a requested result sort because filter_sort removes
    duplicates before sorting.
    """
    separate_presort = presort_request(request)
    result_request = None
    if separate_presort and not request.get('result_sort_cancelled'):
        result_request = {key: value for key, value in request.items() if key not in ('pre_sorts', 'pre_sort_hint')}

    sort_plan = deepcopy(defs['ExecutionPlan'])
    sort_props = sort_plan['properties']
    presort = separate_presort or request
    constrain_sort(defs, sort_props, presort, condition_name='PreDedupSortCondition')
    sort_props['sort']['minItems'] = 1
    sort_props['duplicate_policy'] = literal_schema('keep')
    sort_props['duplicate_field'] = literal_schema(None)

    dedup_plan = deepcopy(defs['ExecutionPlan'])
    dedup_props = dedup_plan['properties']
    constrain_sort(defs, dedup_props, result_request, condition_name='ResultSortCondition')
    # A confirmed filter, if any, belongs to one step. Validation checks its placement.
    for props in (sort_props, dedup_props):
        props['filters'].pop('minItems', None)

    defs['SortBeforeDedupPlan'] = sort_plan
    defs['DedupAfterSortPlan'] = dedup_plan
    return {'anyOf': [{'$ref': '#/$defs/SortBeforeDedupPlan'}, {'$ref': '#/$defs/DedupAfterSortPlan'}]}


def table_sort_instruction(request, *, before_dedup=False):
    """Give all planners the stage ordering checked by the table contract."""
    if before_dedup:
        output_sort = presort_request(request) and not request.get('result_sort_cancelled')
        return ('중복 제거보다 정렬을 먼저 실행합니다. filter_sort를 서로 다른 ID로 두 단계 만드세요. '
                '첫 단계에 중복 제거 전 정렬과 duplicate_policy=keep, duplicate_field=null을 지정하세요. '
                '두 번째 단계의 table 입력을 첫 단계의 table 출력에 연결하고 확정 중복 정책을 적용하세요. '
                + ('결과 정렬은 두 번째 단계의 sort에 지정하세요.' if output_sort else '두 번째 단계의 sort는 비워 주세요.'))
    if request['kind'] in ('none', 'cancelled'):
        return '확정된 정렬 요청이 없으므로 sort=[]로 두고 표에서 행 순서를 유지하세요.'
    return ('확정 정렬의 열과 방향을 실제 결과에 적용하세요. 중복 제거도 요청된 경우에는 '
            '정렬을 적용하기 전에 원본의 첫 행 또는 마지막 행 기준으로 중복을 제거하세요.')


def native_decoder_schema(schema, resources, ledger=None, *, require_ready_draft=False, allowed_tools=None,
                          sort_request=None, sort_before_dedup=False):
    result=deepcopy(schema);defs=result['$defs']
    ids=list(dict.fromkeys(r['id'] for r in resources))
    root_ids=result['properties']['document_ids']
    file_ids=defs['NodeSettings']['properties']['file_ids']
    if ids:
        root_ids['items']['enum']=ids
        file_ids['items']['enum']=ids
    else:
        root_ids['maxItems']=file_ids['maxItems']=0
    defs['Requirement']['properties']['source_id']['enum']=['user',*ids]
    # Compact per-tool variants share settings and bindings; no per-tool copies
    # of the full settings schema. No nodes or edges are supplied by this code.
    if allowed_tools is not None and set(allowed_tools)-set(PORTS):
        # Fail loudly instead of silently dropping a tool from the grammar.
        raise ValueError('포트가 등록되지 않은 도구는 생성 범위에 넣을 수 없습니다: '+', '.join(sorted(set(allowed_tools)-set(PORTS))))
    output_types={}
    for tool,(_,outputs) in PORTS.items():
        # Scoped grammars offer only outputs an in-scope, non-terminal tool supplies.
        if allowed_tools is not None and (tool not in allowed_tools or tool in TERMINAL_TOOLS):continue
        for name,kind in outputs.items():output_types.setdefault(kind,set()).add(name)
    base=deepcopy(defs['WorkflowNode']);variants=[]
    base['properties']['id']={'type':'string','pattern':NODE_ID_PATTERN}
    from workflow_engine.catalog.registry import NATIVE_IDS
    for tool,(inputs,_) in PORTS.items():
        if tool not in NATIVE_IDS:continue
        if allowed_tools is not None and tool not in allowed_tools:continue
        node=deepcopy(base);props=node['properties'];props['tool']={'type':'string','enum':[tool]}
        # Native and operation executors have different settings and ports.
        # An operation must use its own variant, never a native node's grammar.
        settings = deepcopy(defs['NodeSettings'])
        keys = (*NATIVE_SETTINGS[tool], 'node_ref')
        settings['properties'] = {key: settings['properties'][key] for key in keys}
        settings['required'] = list(keys)
        props['settings'] = settings
        binding_variants=[]
        for port,kind in inputs.items():
            outputs=sorted(output_types.get(kind,set()))
            if not outputs:continue
            binding=deepcopy(defs['Binding'])
            binding['properties']['input']={'type':'string','enum':[port]}
            binding['properties']['output']={'type':'string','enum':outputs}
            binding_variants.append(binding)
        missing=REQUIRED_INPUTS.get(tool,set())-{port for port,kind in inputs.items() if output_types.get(kind)}
        if allowed_tools is not None and missing:
            # An in-scope tool whose required input no in-scope tool produces would
            # make an unsatisfiable grammar; the scope itself must be corrected.
            raise ValueError('생성 범위 안에 필수 입력을 공급할 도구가 없습니다: '+tool+' ('+', '.join(sorted(missing))+')')
        binding_key='NativeBinding_'+tool
        defs[binding_key]={'anyOf':binding_variants} if binding_variants else {
            'type':'object','properties':{},'additionalProperties':False}
        props['bindings']={'type':'array','items':{'$ref':'#/$defs/'+binding_key},
                           'maxItems':len(inputs),'minItems':1 if tool in REQUIRED_INPUTS else 0}
        if tool in ('read_table','read_document'):
            props['bindings']['maxItems']=props['bindings']['minItems']=0
        key='NativeNode_'+tool;defs[key]=node;variants.append({'$ref':'#/$defs/'+key})
    if allowed_tools is None:
        from workflow_engine.catalog.canonical import operation_variants
        operations = set(PORTS) - decoded_resource_tools(resources)
        for variant in operation_variants(base,defs['Binding'],operations):
            variant['properties']['id']={'type':'string','pattern':NODE_ID_PATTERN}
            variants.append(variant)
    defs['WorkflowNode']={'anyOf':variants}
    if allowed_tools is not None:
        result['properties']['tool_ids']['items']['enum']=list(allowed_tools)
        # A preceding sort needs its own filter_sort stage before deduplication.
        # Bounds limit repetitions; validation still checks data bindings.
        graph=defs['WorkflowDefinition']
        node_limit = len(allowed_tools) + sort_before_dedup
        graph['properties']['nodes']['maxItems'] = node_limit
        graph['properties']['edges']['maxItems'] = node_limit - 1
    # Enum values come from the already validated condition ledger, never from
    # expected test outputs. Incomplete or out-of-scope contracts stay untouched.
    ledger=ledger or {}
    slots=ready_table_slots(ledger)
    if slots is not None:
        plan=defs['ExecutionPlan']['properties']
        for key,field in [('duplicate.field','duplicate_field'),('duplicate.policy','duplicate_policy'),
                          ('missing.policy','missing_policy'),('output.format','output_format')]:
            if key in slots:plan[field]=literal_schema(slots[key])
        constrain_sort(defs, plan, sort_request)
        if 'filter' not in ledger['operations']:
            plan['filters']['maxItems']=0
        elif all(key in slots for key in ('filter.field','filter.operator','filter.value')):
            plan['filters'].update(minItems=1,maxItems=1)
            condition=defs['FilterCondition']['properties']
            for key,field in [('filter.field','field'),('filter.operator','operator'),('filter.value','value')]:
                condition[field]=literal_schema(slots[key])
            condition['compare_field']=literal_schema(None)
            value=slots['filter.value']
            condition['value_type']['enum']=['boolean' if isinstance(value,bool) else 'number' if isinstance(value,(int,float)) else 'text']
        # Optional properties could otherwise omit a constrained value and fall
        # back to an unrelated Pydantic default. Require the constrained fields.
        plan_def=defs['ExecutionPlan']
        plan_def['required']=list(dict.fromkeys([*plan_def.get('required',[]),
            'filters','sort','match',*[field for key,field in (
                ('duplicate.field','duplicate_field'),('duplicate.policy','duplicate_policy'),
                ('missing.policy','missing_policy'),('output.format','output_format')) if key in slots]]))
        plan['match']['enum']=['all']
        if 'filter' in ledger['operations']:
            condition_def=defs['FilterCondition']
            condition_def['required']=list(condition_def['properties'])
        if sort_before_dedup and 'NativeNode_filter_sort' in defs:
            settings = defs['NativeNode_filter_sort']['properties']['settings']['properties']
            settings['execution_plan'] = split_sort_dedup_plans(defs, sort_request)
        result['required']=list(dict.fromkeys([*result.get('required',[]),'execution_plan']))
        if require_ready_draft:
            # The preceding condition node owns clarification/unsupported routing.
            # This is only the scoped, already-READY generation stage.
            for key in ('questions','question_options','unsupported'):
                result['properties'][key]['maxItems']=0
            result['properties']['workflow']={'$ref':'#/$defs/WorkflowDefinition'}
            result['required']=list(dict.fromkeys([*result.get('required',[]),'workflow']))
    if allowed_tools is None and slots is None:
        typed_filter_conditions(result)
    return result


def binding_repair_options(draft):
    """Offer existing, type-compatible ancestor outputs. No auto rewiring."""
    workflow=(draft or {}).get('workflow') or {};nodes=workflow.get('nodes',[])
    by={n['id']:n for n in nodes};parents={key:set() for key in by}
    for edge in workflow.get('edges',[]):
        if edge['target'] in by and edge['source'] in by:parents[edge['target']].add(edge['source'])
    records=[]
    for node in nodes:
        ancestors=set(parents[node['id']])
        for _ in nodes:
            expanded=ancestors|set().union(*(parents[key] for key in ancestors)) if ancestors else set()
            if expanded==ancestors:break
            ancestors=expanded
        ins,_=node_ports(node);choices={}
        for port,kind in ins.items():
            candidates=[]
            for identifier in sorted(ancestors-{node['id']}):
                source=by[identifier];_,outs=node_ports(source)
                if source['tool'] in ('condition','ask_user'):
                    actual={b['input'] for b in source['bindings']}|({'decision'} if source['tool']=='condition' else {'answer'})
                    outs={k:v for k,v in outs.items() if k in actual}
                candidates += [{'node_id':identifier,'output':name} for name,value in outs.items() if value==kind]
            choices[port]=candidates
        records.append({'node_id':node['id'],'tool':node['tool'],
                        'required_inputs':sorted(REQUIRED_INPUTS.get(node['tool'],set())),
                        'compatible_existing_ancestor_outputs':choices})
    return records
