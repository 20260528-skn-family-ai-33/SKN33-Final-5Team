"""The runner follows one path; use that same order for input/output checks."""
from workflow_engine.catalog.registry import node_ports, uses_operation


def execution_path(workflow, outputs=None, start=None):
    """Return the known path, stopping at an unresolved branch (never guessing)."""
    by={n['id']:n for n in workflow.get('nodes',[])}
    current=start or workflow.get('entry');seen=set();result=[]
    while current in by:
        if current in seen:raise ValueError('순환 실행 경로는 지원하지 않습니다.')
        seen.add(current);node=by[current];result.append(node)
        edges=[e for e in workflow.get('edges',[]) if e['source']==current]
        if node['tool']=='finish':break
        if node['tool']=='condition':
            decision=(outputs or {}).get(current,{}).get('decision')
            if type(decision) is not bool:break
            edges=[e for e in edges if e['branch']==('yes' if decision else 'no')]
        if not edges:break
        if len(edges)!=1:raise ValueError('실행 경로를 하나로 확정할 수 없습니다.')
        current=edges[0]['target']
    return result


def artifact_output_ids(workflow, outputs=None):
    """Select in execution order, not node-list or output-dictionary order."""
    result=[]
    for node in execution_path(workflow,outputs):
        identifier=node['id']
        if outputs is not None:
            if identifier not in outputs:break  # Suspended/unfinished execution.
            emits=bool(outputs[identifier].get('artifact'))
        else:
            emits='artifact' in node_ports(node)[1]
            if node['tool']=='finish' and not uses_operation(node):
                emits=any(b['input']=='artifact' for b in node.get('bindings',[]))
        if emits:result.append(identifier)
    return result


def artifact_origin(workflow, identifier):
    """Only explicit artifact forwarding can preserve the writer's identity."""
    by={n['id']:n for n in workflow['nodes']};seen=set()
    while identifier in by and identifier not in seen:
        seen.add(identifier);node=by[identifier]
        if node['tool']!='finish' or uses_operation(node):return identifier
        binding=next((b for b in node.get('bindings',[]) if b['input']=='artifact' and b['output']=='artifact'),None)
        if not binding:return None
        identifier=binding['node_id']
    return None
