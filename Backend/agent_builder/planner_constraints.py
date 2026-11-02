"""Generation-only native IDs/typed ports. Never rewrite a returned workflow."""
from copy import deepcopy
from .registry import PORTS, node_ports

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
    return (bool(state.get('condition_enforced')) and not state.get('required_node_refs') and
            ready_table_slots(state.get('condition_ledger')) is not None and
            len([r for r in state.get('resources',[]) if r.get('kind')=='table']) <= 1)


def table_tool_issues(state):
    if not table_plan_scope(state):return []
    tools={n['tool'] for n in ((state.get('draft') or {}).get('workflow') or {}).get('nodes',[])}
    return (['확정된 선별·중복 제거 범위에 없는 도구가 추가됐습니다: '+', '.join(sorted(tools-set(TABLE_PLAN_TOOLS)))]
            if tools-set(TABLE_PLAN_TOOLS) else [])


def native_decoder_schema(schema, resources, ledger=None, *, require_ready_draft=False, allowed_tools=None):
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
    for tool,(inputs,_) in PORTS.items():
        if allowed_tools is not None and tool not in allowed_tools:continue
        node=deepcopy(base);props=node['properties'];props['tool']={'type':'string','enum':[tool]}
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
    defs['WorkflowNode']={'anyOf':variants}
    if allowed_tools is not None:
        result['properties']['tool_ids']['items']['enum']=list(allowed_tools)
        # Scoped single-table chain: one node per allowed tool at most, linked
        # in one path. Bounds repetition only; validation still checks topology.
        graph=defs['WorkflowDefinition']
        graph['properties']['nodes']['maxItems']=len(allowed_tools)
        graph['properties']['edges']['maxItems']=len(allowed_tools)-1
    # Enum values come from the already validated condition ledger, never from
    # expected test outputs. Incomplete or out-of-scope contracts stay untouched.
    ledger=ledger or {}
    slots=ready_table_slots(ledger)
    if slots is not None:
        plan=defs['ExecutionPlan']['properties']
        for key,field in [('duplicate.field','duplicate_field'),('duplicate.policy','duplicate_policy'),
                          ('missing.policy','missing_policy'),('output.format','output_format')]:
            if key in slots:plan[field]=literal_schema(slots[key])
        plan['sort']['maxItems']=0
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
        result['required']=list(dict.fromkeys([*result.get('required',[]),'execution_plan']))
        if require_ready_draft:
            # The preceding condition node owns clarification/unsupported routing.
            # This is only the scoped, already-READY generation stage.
            for key in ('questions','question_options','unsupported'):
                result['properties'][key]['maxItems']=0
            result['properties']['workflow']={'$ref':'#/$defs/WorkflowDefinition'}
            result['required']=list(dict.fromkeys([*result.get('required',[]),'workflow']))
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
