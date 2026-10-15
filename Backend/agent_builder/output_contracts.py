"""Complete an explicit file request on a simple terminal output, never review candidates."""
from .models import NodeSettings

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
