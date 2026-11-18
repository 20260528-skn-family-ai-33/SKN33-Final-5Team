"""The runner follows one path; use that same order for input/output checks."""
from documents.resources.document_resources import TABLE_SCOPE_INCOMPLETE
from workflow_engine.catalog.registry import node_ports, uses_operation


ROWS_SCOPE_VERIFIED = '_rows_scope_verified'
TABLE_SCOPE_ERROR = ('검색에서 문서의 일부 표가 제외되었거나 전체 표 포함 여부를 확인할 수 없습니다. '
                     '사용할 파일이나 표를 직접 선택하고 다시 실행해 주세요.')


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


def _bound_source(node, port):
    binding = next((binding for binding in node.get('bindings', []) if binding['input'] == port), None)
    if binding and binding['output'] == port:
        return binding['node_id']
    return None


def records_source_node(node, nodes, *, dependencies=None):
    """Resolve the producer of a records input through exact record forwarding."""
    identifier = _bound_source(node, 'records')
    seen = set()
    while identifier in nodes:
        source = nodes[identifier]
        if id(source) in seen:
            return None
        seen.add(id(source))
        if uses_operation(source):
            return source
        tool = source['tool']
        settings = source['settings']
        if tool in ('condition', 'ask_user'):
            identifier = _bound_source(source, 'records')
        elif tool == 'saved_workflow' and settings.get('output_port') == 'records':
            child = (dependencies or {}).get(identifier)
            if not child:
                return None
            nodes = {n['id']: n for n in (child['config'].get('workflow') or {}).get('nodes', [])}
            dependencies = child.get('workflow_dependencies')
            identifier = settings.get('output_node')
        else:
            return source
    return None


def _table_ids(documents):
    return {(part['file_id'], part['source_id']) for part in documents if 'table_cells' in part}


def document_table_scope_incomplete(node, outputs, config):
    return _table_scope_incomplete(_bound_source(node, 'documents'), 'documents', outputs, config)


def records_table_scope_incomplete(node, outputs, config):
    return output_records_table_scope_incomplete(_bound_source(node, 'records'), outputs, config)


def output_records_table_scope_incomplete(identifier, outputs, config):
    return _table_scope_incomplete(identifier, 'records', outputs, config)


def _table_scope_incomplete(identifier, port, outputs, config, *, external_scope=False):
    """Recover table coverage when a suspended run predates scope metadata."""
    config = config or {}
    nodes = {node['id']: node for node in (config.get('workflow') or {}).get('nodes', [])}
    seen = set()
    while identifier in nodes and (identifier, port) not in seen:
        seen.add((identifier, port))
        node = nodes[identifier]
        output = (outputs or {}).get(identifier, {})
        if port == 'documents' and TABLE_SCOPE_INCOMPLETE in output:
            return output[TABLE_SCOPE_INCOMPLETE]
        if port == 'records' and output.get(ROWS_SCOPE_VERIFIED):
            return False
        if uses_operation(node):
            return False
        tool = node['tool']
        if tool in ('condition', 'ask_user'):
            identifier = _bound_source(node, port)
        elif port == 'records' and tool == 'extract_information' and node['settings'].get('extraction_mode') == 'rows':
            port = 'documents'
            identifier = _bound_source(node, port)
        elif port == 'documents' and tool == 'search_document':
            identifier = _bound_source(node, port)
            incoming = (outputs or {}).get(identifier, {}).get('documents')
            selected = output.get('documents')
            # Legacy pinned outputs omit their child checkpoint. A search there
            # cannot vouch for tables it may no longer be carrying.
            if incoming is None or selected is None or _table_ids(incoming) - _table_ids(selected):
                return True
        elif tool == 'saved_workflow' and node['settings'].get('output_port') == port:
            child = config.get('_saved_workflows', {}).get(identifier)
            if not child:
                return True
            child_config = {**child['config'], '_saved_workflows': child.get('workflow_dependencies', {})}
            child_external_scope = _table_scope_incomplete(
                _bound_source(node, 'documents'), 'documents', outputs, config, external_scope=external_scope,
            )
            return _table_scope_incomplete(node['settings'].get('output_node'), port, None, child_config,
                                           external_scope=child_external_scope)
        elif port == 'documents' and tool == 'read_document':
            return external_scope
        else:
            return False
    return False
