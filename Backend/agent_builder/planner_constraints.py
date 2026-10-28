"""Generation-only native IDs/typed ports. Never rewrite a returned workflow."""
from copy import deepcopy
from .registry import PORTS, node_ports

REQUIRED_INPUTS = {'filter_sort':{'table'}, 'write_result':{'table'}, 'aggregate_table':{'table'},
    'review_summary':{'reviews'}, 'search_document':{'documents'}, 'extract_information':{'documents'},
    'compose_answer':{'documents'}}


def native_decoder_schema(schema, resources, ledger=None, *, require_ready_draft=False):
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
    output_types={}
    for _,outputs in PORTS.values():
        for name,kind in outputs.items():output_types.setdefault(kind,set()).add(name)
    base=deepcopy(defs['WorkflowNode']);variants=[]
    for tool,(inputs,_) in PORTS.items():
        node=deepcopy(base);props=node['properties'];props['tool']={'type':'string','enum':[tool]}
        binding_variants=[]
        for port,kind in inputs.items():
            outputs=sorted(output_types.get(kind,set()))
            if not outputs:continue
            binding=deepcopy(defs['Binding'])
            binding['properties']['input']={'type':'string','enum':[port]}
            binding['properties']['output']={'type':'string','enum':outputs}
            binding_variants.append(binding)
        binding_key='NativeBinding_'+tool
        defs[binding_key]={'anyOf':binding_variants} if binding_variants else {
            'type':'object','properties':{},'additionalProperties':False}
        props['bindings']={'type':'array','items':{'$ref':'#/$defs/'+binding_key},
                           'maxItems':len(inputs),'minItems':1 if tool in REQUIRED_INPUTS else 0}
        if tool in ('read_table','read_document'):
            props['bindings']['maxItems']=props['bindings']['minItems']=0
        key='NativeNode_'+tool;defs[key]=node;variants.append({'$ref':'#/$defs/'+key})
    defs['WorkflowNode']={'anyOf':variants}
    # Enum values come from the already validated condition ledger, never from
    # expected test outputs. An incompatible/combined contract is left alone.
    ledger=ledger or {}
    if ledger.get('decision')=='ready' and ledger.get('operations') in (['filter'],['deduplicate']):
        slots={key:slot['value'] for key,slot in ledger.get('slots',{}).items() if slot['status']=='specified'}
        plan=defs['ExecutionPlan']['properties']
        for key,field in [('duplicate.field','duplicate_field'),('duplicate.policy','duplicate_policy'),
                          ('missing.policy','missing_policy'),('output.format','output_format')]:
            if key in slots:plan[field]['enum']=[slots[key]]
        plan['sort']['maxItems']=0
        if ledger['operations']==['deduplicate']:
            plan['filters']['maxItems']=0
        elif all(key in slots for key in ('filter.field','filter.operator','filter.value')):
            plan['filters'].update(minItems=1,maxItems=1)
            condition=defs['FilterCondition']['properties']
            for key,field in [('filter.field','field'),('filter.operator','operator'),('filter.value','value')]:
                condition[field]['enum']=[slots[key]]
            condition['compare_field']['enum']=[None]
            value=slots['filter.value']
            condition['value_type']['enum']=['boolean' if isinstance(value,bool) else 'number' if isinstance(value,(int,float)) else 'text']
        required=('filter.field','filter.operator','filter.value') if ledger['operations']==['filter'] else ('duplicate.field','duplicate.policy')
        if require_ready_draft and all(key in slots for key in required):
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
