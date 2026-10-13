"""Pin saved graph versions; prohibit recursive calls and ambiguous output contracts."""
from .registry import node_ports

REVIEW_EXPORT_ERROR='검수 후보 응대를 최종 응대로 자동 내보내지 마세요. 검수 보고서는 review_summary→write_result로 저장하고 최종 응대는 승인하세요.'

def contains_review(config,dependencies=None):
    """Approval requirements propagate through every pinned child graph."""
    if any(n['tool']=='reply_review' for n in (config.get('workflow') or {}).get('nodes',[])):return True
    dependencies=dependencies if dependencies is not None else config.get('_saved_workflows',{})
    return any(contains_review(agent['config'],agent.get('workflow_dependencies',{})) for agent in dependencies.values())

def reject_review_export(config,dependencies):
    if any(n['tool']=='export_result' for n in (config.get('workflow') or {}).get('nodes',[])) and contains_review(config,dependencies):
        raise ValueError(REVIEW_EXPORT_ERROR)

def guaranteed_output(config,node_id,port):
    workflow=config.get('workflow') or {};nodes={n['id']:n for n in workflow.get('nodes',[])}
    if node_id not in nodes or port not in node_ports(nodes[node_id])[1]:raise ValueError('저장된 워크플로우의 출력 노드·포트가 없습니다.')
    parents={id:set() for id in nodes};outgoing={id:[] for id in nodes}
    for e in workflow['edges']:parents[e['target']].add(e['source']);outgoing[e['source']].append(e['target'])
    memo={}
    def dom(id):
        if id not in memo:memo[id]={id}|(set.intersection(*(dom(p) for p in parents[id])) if parents[id] else set())
        return memo[id]
    if any(node_id not in dom(id) for id in nodes if not outgoing[id]):raise ValueError('분기에 따라 없는 출력은 재사용 노드의 출력으로 선택할 수 없습니다.')
    source=nodes[node_id]
    if source['tool'] in ['condition','ask_user','finish'] and port not in ['decision','answer'] and port not in {b['input'] for b in source['bindings']}:
        if not(source['tool']=='finish' and port=='text'):raise ValueError('선택한 노드가 실제로 전달하는 출력이 아닙니다.')

def pin_dependencies(storage,draft,model,credential_id,parent_id=None):
    result={}
    def inspect(agent,path):
        if agent['id'] in path:raise ValueError('저장된 워크플로우의 재귀 연결은 지원하지 않습니다.')
        if len(path)>=4:raise ValueError('저장된 워크플로우 연결은 최대 4단계까지 지원합니다.')
        if agent['model']!=model or agent['credential_id']!=credential_id:raise ValueError('재사용할 워크플로우는 같은 모델·연결 계정을 사용해야 합니다.')
        if draft.get('schedule') and any(n['tool']=='ask_user' for n in agent['config']['workflow']['nodes']):raise ValueError('예약 실행에는 응답 대기가 있는 저장된 흐름을 연결할 수 없습니다.')
        for child in agent.get('workflow_dependencies',{}).values():inspect(child,path+[agent['id']])
        reject_review_export(agent['config'],agent.get('workflow_dependencies',{}))
    for node in (draft.get('workflow') or {}).get('nodes',[]):
        if node['tool']!='saved_workflow':continue
        settings=node['settings']
        if not settings.get('agent_id') or not settings.get('agent_version'):raise ValueError('재사용할 에이전트 ID와 설정 버전이 필요합니다.')
        agent=storage.agent_version(settings['agent_id'],settings['agent_version'])
        inspect(agent,[parent_id] if parent_id else [])
        guaranteed_output(agent['config'],settings.get('output_node'),settings.get('output_port'))
        result[node['id']]=agent
    reject_review_export(draft,result)
    return result

def catalog(storage,model,credential_id):
    results=[]
    for a in storage.agents():
        if a['model']!=model or a['credential_id']!=credential_id:continue
        outputs=[];columns={}
        for n in (a['config'].get('workflow') or {}).get('nodes',[]):
            if n['tool']=='read_table':
                ids=n['settings']['file_ids'] or a['config']['document_ids']
                tables=[storage.asset(id) for id in ids if storage.asset(id)['kind']=='table']
                if len(tables)==1:columns[n['id']]=tables[0]['columns']
            elif n['tool']=='aggregate_table':columns[n['id']]=n['settings']['fields']+(['sum'] if n['settings'].get('aggregation')=='sum' else ['count'])
            elif n['tool']=='filter_sort':
                b=next((b for b in n['bindings'] if b['input']=='table'),None)
                if b:columns[n['id']]=columns.get(b['node_id'])
        for n in (a['config'].get('workflow') or {}).get('nodes',[]):
            for port in node_ports(n)[1]:
                try:guaranteed_output(a['config'],n['id'],port);outputs.append({'node_id':n['id'],'port':port,'label':n['label'],'columns':columns.get(n['id']) if port=='table' else None})
                except ValueError:pass
        if outputs:results.append({'id':a['id'],'version':a['version'],'name':a['config']['name'],'outputs':outputs})
    return results[:30]

def requires_model(config):
    for node in config['workflow']['nodes']:
        tool=node['tool'];settings=node['settings']
        if tool in ['search_document','extract_information','compose_answer','reply_review','agent_node'] or tool=='condition' and settings['condition_operator']=='semantic' or tool=='ask_user' and settings['instructions']:return True
        if tool=='saved_workflow':
            agent=config.get('_saved_workflows',{}).get(node['id'])
            if agent and requires_model({**agent['config'],'_saved_workflows':agent.get('workflow_dependencies',{})}):return True
    return False
