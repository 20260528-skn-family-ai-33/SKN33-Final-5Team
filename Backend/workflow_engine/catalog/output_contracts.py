"""Complete an explicit file request on a simple terminal output, never review candidates."""
from contracts.models import NodeSettings

def ensure_requested_output(draft,format):
    workflow=draft.get('workflow')
    if not workflow or not format or draft.get('questions') or draft.get('unsupported'):return
    nodes=workflow['nodes'];edges=workflow['edges']
    if any(n['tool'] in ['write_result','export_result','reply_review','saved_workflow','review_summary'] for n in nodes):return
    if any(e['branch']!='next' for e in edges):return
    terminals=[n for n in nodes if not any(e['source']==n['id'] for e in edges)]
    if len(terminals)!=1:return
    terminal=terminals[0];producer=terminal
    if terminal['tool']=='finish':
        incoming=[e for e in edges if e['target']==terminal['id']]
        if len(incoming)!=1:return
        producer=next(n for n in nodes if n['id']==incoming[0]['source'])
    port={'read_table':'table','filter_sort':'table','aggregate_table':'table','extract_information':'records','compose_answer':'text','agent_node':'text'}.get(producer['tool'])
    if not port or (port=='text' and format!='markdown') or (port!='text' and format not in ['csv','json']):return
    id='save_result'
    while any(n['id']==id for n in nodes):id+='x'
    settings=NodeSettings(file_ids=[],query=None,fields=[],instructions=None,format=format,question=None,condition_field=None,condition_operator=None).model_dump()
    tool='write_result' if port=='table' else 'export_result'
    output={'id':id,'tool':tool,'label':'요청한 결과 파일 저장','settings':settings,'bindings':[{'input':port,'node_id':producer['id'],'output':port}]}
    if producer is not terminal:
        edges[:]=[e for e in edges if not(e['source']==producer['id'] and e['target']==terminal['id'])]
        edges.append({'source':id,'target':terminal['id'],'branch':'next'})
    edges.append({'source':producer['id'],'target':id,'branch':'next'})
    if producer is not terminal:nodes.insert(nodes.index(terminal),output)
    else:nodes.append(output)
    draft['tool_ids']=list(dict.fromkeys(n['tool'] for n in nodes))


def final_report_matches(draft, requested_format):
    """Check the last artifact on every complete execution path."""
    from workflow_engine.catalog.canonical import parse_arguments
    from workflow_engine.catalog.registry import node_ports, uses_operation

    workflow = draft.get('workflow') or {}
    nodes = {node.get('id'): node for node in workflow.get('nodes', [])}
    if None in nodes or workflow.get('entry') not in nodes:
        return False
    successors = {node_id: [] for node_id in nodes}
    for edge in workflow.get('edges', []):
        if edge['source'] not in nodes or edge['target'] not in nodes:
            return False
        successors[edge['source']].append(edge['target'])

    # An artifact with an unknown format still replaces the prior artifact.
    unknown_format = object()
    formats = {}
    for node_id, node in nodes.items():
        settings = node['settings']
        operation = settings.get('operation')
        if not operation and node['tool'] == 'write_result':
            formats[node_id] = settings.get('format') or draft.get('execution_plan', {}).get('output_format')
        elif not operation and node['tool'] == 'export_result':
            formats[node_id] = settings.get('format')
        elif node['tool'] == 'export_result' and operation in ('write_csv', 'write_json'):
            formats[node_id] = operation.removeprefix('write_')
        elif node['tool'] == 'export_result' and operation == 'serialize':
            try:
                value = parse_arguments(settings).get('format')
                formats[node_id] = value if isinstance(value, str) else unknown_format
            except ValueError:
                formats[node_id] = unknown_format
        elif uses_operation(node) and 'asset' in node_ports(node)[1]:
            formats[node_id] = unknown_format

    pending = [(workflow.get('entry'), None)]
    visited = set()
    reached_end = False
    while pending:
        node_id, previous = pending.pop()
        if node_id not in nodes:
            return False
        current = formats.get(node_id, previous)
        if (node_id, current) in visited:
            continue
        visited.add((node_id, current))
        if successors[node_id]:
            pending.extend((target, current) for target in successors[node_id])
        elif current != requested_format:
            return False
        else:
            reached_end = True
    return reached_end
