"""Validate and normalize workflow definitions without generating executable source."""
import re
from .registry import node_ports

def workflow_errors(workflow,tool_ids,resources,default_plan=None):
    nodes=workflow['nodes'];edges=workflow['edges'];by={n['id']:n for n in nodes};issues=[]
    if not nodes or len(by)!=len(nodes):return ['워크플로우 노드가 없거나 ID가 중복되었습니다.']
    if any(not re.fullmatch(r'[a-zA-Z][\w-]{0,39}',n['id']) for n in nodes):issues.append('노드 ID 형식을 확인하세요.')
    if workflow['entry'] not in by:return issues+['시작 노드가 없습니다.']
    outgoing={id:[] for id in by};parents={id:set() for id in by}
    if len({(e['source'],e['target'],e['branch']) for e in edges})!=len(edges):issues.append('중복된 연결이 있습니다.')
    for e in edges:
        if e['source'] not in by or e['target'] not in by:issues.append('존재하지 않는 노드에 연결했습니다.');continue
        outgoing[e['source']].append(e);parents[e['target']].add(e['source'])
    visiting=set();done=set();ancestors={}
    def visit(id):
        if id in visiting:raise ValueError('이번 데모는 순환 연결을 지원하지 않습니다.')
        if id in done:return
        visiting.add(id)
        for parent in parents[id]:visit(parent)
        ancestors[id]=set(parents[id])|set().union(*(ancestors[p] for p in parents[id])) if parents[id] else set()
        visiting.remove(id);done.add(id)
    try:
        for id in by:visit(id)
    except ValueError as error:return issues+[str(error)]
    reached=set()
    def reach(id):
        if id in reached:return
        reached.add(id)
        for edge in outgoing[id]:reach(edge['target'])
    reach(workflow['entry'])
    if reached!=set(by):issues.append('시작 노드에서 실행할 수 없는 노드가 있습니다.')
    # This compiler executes one active path, not parallel fan-out/join.
    for n in nodes:
        id=n['id'];tool=n['tool'];bindings=n['bindings'];settings=n['settings'];ins,outs=node_ports(n)
        if tool not in tool_ids:issues.append('노드의 도구가 연결 도구 목록에 없습니다: '+id)
        own_plan=settings.get('execution_plan')
        if tool!='filter_sort' and own_plan and (own_plan.get('filters') or own_plan.get('sort') or own_plan.get('duplicate_field')
                or own_plan.get('missing_policy')=='exclude' or own_plan.get('duplicate_policy') not in (None,'keep','ask')):
            issues.append('필터·정렬·빈 값·중복 처리 조건은 filter_sort 노드에만 둘 수 있습니다. 이 노드에서는 조건이 적용되지 않습니다: '+id)
        if tool=='reply_review':
            if 'table' in {b['input'] for b in bindings} and len(settings['fields'])!=2:issues.append('검수에는 문의 열·초안 열 두 개를 순서대로 지정하세요: '+id)
            if 'table' in {b['input'] for b in bindings} and 'text' in {b['input'] for b in bindings}:issues.append('표 검수와 단일 초안 입력을 동시에 연결할 수 없습니다: '+id)
        if tool=='saved_workflow' and any(not settings.get(k) for k in ['agent_id','agent_version','output_node','output_port']):issues.append('저장된 워크플로우의 ID·버전·출력 노드·포트가 필요합니다: '+id)
        if tool=='condition':
            if sorted(e['branch'] for e in outgoing[id])!=['no','yes']:issues.append('조건 노드는 yes/no 경로가 각각 하나 필요합니다: '+id)
            if not settings['condition_operator']:issues.append('분기 조건이 필요합니다: '+id)
        elif len(outgoing[id])>1 or any(e['branch']!='next' for e in outgoing[id]):issues.append('일반 노드는 다음 경로 하나만 연결할 수 있습니다: '+id)
        if len({b['input'] for b in bindings})!=len(bindings):issues.append('입력이 중복 연결되었습니다: '+id)
        for b in bindings:
            source=by.get(b['node_id'])
            if b['input'] not in ins or not source or b['output'] not in node_ports(source)[1]:issues.append('입출력 포트가 존재하지 않습니다: '+id);continue
            if source['tool'] in ['condition','ask_user'] and b['output'] not in ({'decision'} if source['tool']=='condition' else {'answer'})|{x['input'] for x in source['bindings']}:issues.append('앞 노드가 실제로 전달하지 않는 출력입니다: '+id)
            if ins[b['input']]!=node_ports(source)[1][b['output']]:issues.append('입출력 자료형이 맞지 않습니다: '+id)
            if b['node_id'] not in ancestors.get(id,set()):issues.append('앞 단계가 아닌 노드의 출력을 참조했습니다: '+id)
        supplied={b['input'] for b in bindings}
        required={'filter_sort':{'table'},'aggregate_table':{'table'},'review_summary':{'reviews'},'write_result':{'table'},'search_document':{'documents'},'extract_information':{'documents'},'compose_answer':{'documents'}}.get(tool,set())
        if not required<=supplied:issues.append('필수 입력 연결이 없습니다: '+id)
        if tool in ['export_result','condition'] and not supplied.intersection({'records','text','answer','documents','reviews','table'}) and not (tool=='condition' and settings['condition_operator']=='semantic'):issues.append('처리할 결과 입력이 필요합니다: '+id)
        if tool=='condition' and settings['condition_operator'] in ['all_pass','any_revision'] and 'reviews' not in supplied:issues.append('검수 판정 분기는 reviews 입력이 필요합니다: '+id)
        if tool=='condition' and settings['condition_operator']=='has_missing' and 'records' not in supplied:issues.append('누락 분기는 추출 결과 records가 필요합니다: '+id)
        if tool=='condition' and settings['condition_operator']=='semantic' and not settings['instructions']:issues.append('의미 분기의 판단 기준이 필요합니다: '+id)
        if tool=='extract_information' and not settings['fields']:issues.append('추출할 항목이 필요합니다: '+id)
        if tool=='ask_user' and not settings['question']:issues.append('확인 질문이 필요합니다: '+id)
        if tool=='export_result':
            if not supplied.intersection({'records','text','answer'}):issues.append('저장할 records/text/answer 결과 입력이 필요합니다: '+id)
            if 'records' in supplied and settings['format'] not in ['csv','json']:issues.append('표 결과는 CSV/JSON을 선택하세요: '+id)
            if 'records' not in supplied and supplied.intersection({'text','answer'}) and settings['format'] not in ['markdown','json']:issues.append('텍스트 결과는 Markdown 또는 JSON을 선택하세요: '+id)
        if tool in ['read_document','read_table']:
            lookup={r['id']:r for r in resources}
            if any(f not in lookup for f in settings['file_ids']):issues.append('연결되지 않은 파일 ID가 있습니다: '+id)
            expected='document' if tool=='read_document' else 'table'
            if any(lookup[f]['kind']!=expected for f in settings['file_ids'] if f in lookup):issues.append('읽기 노드와 파일 종류가 맞지 않습니다: '+id)
    if set(tool_ids)!={n['tool'] for n in nodes}:issues.append('연결 도구 목록과 실제 노드 구성이 다릅니다.')
    # A binding must be available on EVERY path reaching the node, not just an ancestor.
    dominators={}
    def dominate(id):
        if id in dominators:return dominators[id]
        ps=parents[id]
        dominators[id]={id}|(set.intersection(*(dominate(p) for p in ps)) if ps else set())
        return dominators[id]
    for id in by:dominate(id)
    for n in nodes:
        for b in n['bindings']:
            if b['node_id'] not in dominators[n['id']]:issues.append('분기에 따라 입력이 없는 연결입니다: '+n['id'])
    if 'reply_review' in tool_ids and 'export_result' in tool_ids:issues.append('검수 후보 응대를 최종 응대로 자동 내보내지 마세요. 검수 보고서는 review_summary→write_result로 저장하고 최종 응대는 승인하세요.')
    if issues:return list(dict.fromkeys(issues))
    table_resources={r['id']:r for r in resources if r.get('kind')=='table'}
    column_cache={}
    def columns_for(id):
        if id in column_cache:return column_cache[id]
        node=by[id];tool=node['tool'];columns=None
        if tool=='read_table':
            ids=node['settings']['file_ids'] or list(table_resources)
            if len(ids)>1:issues.append('표 읽기 노드는 한 데이터 소스를 선택해야 합니다: '+id)
            if len(ids)==1 and ids[0] in table_resources:columns=set(table_resources[ids[0]]['columns'])
        elif tool=='review_summary':columns={'review_id','verdict','summary','candidate_reply','approval_state'}
        elif tool in ['filter_sort','aggregate_table','reply_review']:
            binding=next((b for b in node['bindings'] if b['input']=='table' and b['node_id'] in by),None)
            incoming=columns_for(binding['node_id']) if binding else None
            if tool=='aggregate_table':
                fields=node['settings']['fields']
                if incoming is not None and any(f not in incoming for f in fields):issues.append('집계 항목이 앞 단계의 열에 없습니다: '+id)
                operation=node['settings'].get('aggregation') or 'count';sum_field=node['settings'].get('sum_field')
                if operation=='sum' and (not sum_field or incoming is not None and sum_field not in incoming):issues.append('합계할 숫자 열이 필요합니다: '+id)
                output='sum' if operation=='sum' else 'count'
                if output in fields:issues.append('집계 결과 열과 그룹 열이 충돌합니다: '+id)
                columns=set(fields+[output])
            elif tool=='reply_review':
                if incoming is not None and any(f not in incoming for f in node['settings']['fields']):issues.append('검수할 문의·초안 열이 앞 단계에 없습니다: '+id)
            else:
                columns=incoming
                plan=node['settings'].get('execution_plan') or default_plan
                if tool=='filter_sort' and plan and incoming is not None:
                    fields=[f['field'] for f in plan['filters']]+[f['compare_field'] for f in plan['filters'] if f['compare_field']]+[s['field'] for s in plan['sort']]
                    if plan['duplicate_field']:fields.append(plan['duplicate_field'])
                    if any(f not in incoming for f in fields):issues.append('노드별 조건의 열이 앞 단계 출력에 없습니다: '+id)
        column_cache[id]=columns;return columns
    for node in nodes:
        if node['tool'] in ['read_table','filter_sort','aggregate_table','reply_review']:columns_for(node['id'])
    return list(dict.fromkeys(issues))

def legacy_workflow(draft):
    """Migration only: preserve previously saved table agents, never use as a planner."""
    settings={'file_ids':[],'query':None,'fields':[],'instructions':None,'format':None,'question':None,'condition_field':None,'condition_operator':None}
    nodes=[];edges=[];previous=None
    for tool in ['read_table','filter_sort','write_result']:
        if tool not in draft['tool_ids']:continue
        id=tool;bindings=[{'input':'table','node_id':previous,'output':'table'}] if previous else []
        nodes.append({'id':id,'tool':tool,'label':tool,'settings':dict(settings),'bindings':bindings})
        if previous:edges.append({'source':previous,'target':id,'branch':'next'})
        previous=id
    return {'entry':nodes[0]['id'],'nodes':nodes,'edges':edges} if nodes else None

def normalize_connections(workflow):
    """Resolve unambiguous port aliases using registered types; never invent topology."""
    if not workflow:return workflow
    by={n['id']:n for n in workflow['nodes']}
    if workflow['entry'] not in by:
        targets={e['target'] for e in workflow['edges']}
        roots=[id for id in by if id not in targets]
        if len(roots)==1:workflow['entry']=roots[0]
    for edge in workflow['edges']:
        if edge['source'] in by and by[edge['source']]['tool']!='condition':edge['branch']='next'
    for node in workflow['nodes']:
        inputs=node_ports(node)[0]
        for binding in node['bindings']:
            source=by.get(binding['node_id'])
            if not source:continue
            outputs=node_ports(source)[1]
            output_type=outputs.get(binding['output'])
            if output_type and inputs.get(binding['input'])!=output_type:
                candidates=[name for name,type in inputs.items() if type==output_type]
                if len(candidates)==1:binding['input']=candidates[0]
    return workflow
