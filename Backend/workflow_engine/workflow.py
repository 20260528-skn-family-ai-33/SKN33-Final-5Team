"""Validate and normalize workflow definitions without generating executable source."""
import re
from workflow_engine.catalog.registry import node_ports, uses_operation
from documents.resources.document_resources import supports

def port_issue_detail(node,binding,source,ins):
    """Say which side of a binding is wrong and what the source really produces."""
    tool=node['tool']
    if binding['input'] not in ins:
        return f"{tool}에는 {binding['input']} 입력이 없습니다. 받을 수 있는 입력: {', '.join(ins) or '없음'}."
    if not source:
        return f"참조한 노드 {binding['node_id']}가 없습니다."
    outs=node_ports(source)[1]
    detail=(f"{binding['node_id']}({source['tool']})의 출력은 {', '.join(outs) or '없음'}이며 {binding['output']}가 아닙니다. "
            f"{tool}.{binding['input']}에는 {ins[binding['input']]} 자료가 필요합니다.")
    return detail+table_hint(ins[binding['input']],outs)


def table_hint(needed,outs):
    if needed=='table' and 'documents' in outs:
        return ' 문서·이미지 항목을 표로 정리하려면 extract_information(records) → export_result(format=csv)를 사용하세요.'
    if needed=='table' and 'records' in outs:
        return ' 추출 결과(records)를 CSV·JSON으로 저장하려면 write_result 대신 export_result(records)를 사용하세요.'
    return ''


def workflow_errors(workflow,tool_ids,resources,default_plan=None,*,runtime_resources=()):
    nodes=workflow['nodes'];edges=workflow['edges'];by={n['id']:n for n in nodes};issues=[]
    if not nodes or len(by)!=len(nodes):return ['워크플로우 노드가 없거나 ID가 중복되었습니다.']
    if any(not re.fullmatch(r'[a-zA-Z][\w-]{0,39}',n['id']) for n in nodes):issues.append('노드 ID 형식을 확인하세요.')
    if workflow['entry'] not in by:issues.append('시작 노드가 없습니다. entry는 실제 nodes에 존재하는 노드 ID여야 합니다.')
    outgoing={id:[] for id in by};parents={id:set() for id in by}
    if len({(e['source'],e['target'],e['branch']) for e in edges})!=len(edges):issues.append('중복된 연결이 있습니다.')
    for e in edges:
        if e['source'] not in by or e['target'] not in by:
            # Name the edge and the missing id so a model repair can act on it.
            missing=', '.join(x for x in (e['source'],e['target']) if x not in by)
            issues.append(f"존재하지 않는 노드에 연결했습니다: {e['source']} → {e['target']} (없는 노드: {missing}). nodes에 있는 id만 연결하세요.");continue
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
    if workflow['entry'] in by:reach(workflow['entry'])
    if reached!=set(by):issues.append('시작 노드에서 실행할 수 없는 노드가 있습니다. entry에서 각 노드로 이어지는 edges를 확인하세요.')
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
            if b['input'] not in ins or not source or b['output'] not in node_ports(source)[1]:
                issues.append('입출력 포트가 존재하지 않습니다: '+id+' · '+port_issue_detail(n,b,source,ins));continue
            if source['tool'] in ['condition','ask_user'] and b['output'] not in ({'decision'} if source['tool']=='condition' else {'answer'})|{x['input'] for x in source['bindings']}:issues.append('앞 노드가 실제로 전달하지 않는 출력입니다: '+id)
            if ins[b['input']]!='json' and ins[b['input']]!=node_ports(source)[1][b['output']]:issues.append('입출력 자료형이 맞지 않습니다: '+id+table_hint(ins[b['input']],node_ports(source)[1]))
            if b['node_id'] not in ancestors.get(id,set()):issues.append('앞 단계가 아닌 노드의 출력을 참조했습니다: '+id+' (참조: '+b['node_id']+'.'+b['output']+' → '+b['input']+'). 연결 순서를 확인하세요.')
        if uses_operation(n):
            from workflow_engine.executors.canonical_operation import validate_settings
            try: validate_settings(n, [*resources,*runtime_resources])
            except (ValueError, KeyError, TypeError) as error: issues.append(id+': '+str(error))
            continue
        supplied={b['input'] for b in bindings}
        required={'filter_sort':{'table'},'aggregate_table':{'table'},'calculate_columns':{'table'},'text_transform':{'text'},'base64_codec':{'text'},'text_hash':{'text'},'date_transform':{'text'},'text_statistics':{'text'},'review_summary':{'reviews'},'write_result':{'table'},'search_document':{'documents'},'extract_information':{'documents'},'compose_answer':{'documents'}}.get(tool,set())
        if not required<=supplied:issues.append('필수 입력 연결이 없습니다: '+id)
        if tool=='calculate_columns':
            operation=settings.get('table_operation')
            if operation=='compute':
                from .table_formula import formula_columns
                if not isinstance(settings.get('output_column'),str) or not settings['output_column'].strip():issues.append('계산 결과 열 이름이 필요합니다: '+id)
                try:formula_columns(settings.get('calculation_formula'))
                except ValueError:issues.append('계산 수식은 열 이름·숫자·사칙연산과 %만 사용할 수 있습니다: '+id)
            elif operation=='rename':
                if any(not isinstance(settings.get(k),str) or not settings[k].strip() for k in ('rename_from','rename_to')):issues.append('기존 열과 바꿀 열 이름이 필요합니다: '+id)
            elif operation=='parse_dates':
                if not settings.get('fields') or any(not isinstance(settings.get(k),str) or not settings[k].strip() for k in ('date_input_format','date_timezone')):issues.append('날짜 열·입력 형식·시간대가 필요합니다: '+id)
            else:issues.append('열 계산, 이름 변경 또는 날짜 변환을 선택하세요: '+id)
        if tool=='text_transform' and (not isinstance(settings.get('text_find'),str) or not settings['text_find'] or not isinstance(settings.get('text_replacement'),str)):
            issues.append('바꿀 문자열과 교체 문자열이 필요합니다: '+id)
        if tool=='base64_codec' and settings.get('codec_mode') not in ('encode','decode'):
            issues.append('Base64 인코딩 또는 디코딩을 선택하세요: '+id)
        if tool=='date_transform':
            if settings.get('date_operation')=='adjust':
                if type(settings.get('date_seconds')) is not int:issues.append('조정할 초 단위가 필요합니다: '+id)
            elif settings.get('date_operation')=='format':
                if not isinstance(settings.get('date_output_format'),str) or not settings['date_output_format'].strip():issues.append('날짜 출력 형식이 필요합니다: '+id)
            else:issues.append('날짜 조정 또는 형식 변경을 선택하세요: '+id)
        if tool=='text_statistics':
            unit=settings.get('statistics_unit')
            if unit not in ('characters','words') or (unit=='characters' and type(settings.get('include_whitespace')) is not bool):issues.append('문자·단어 단위와 공백 포함 여부를 확인하세요: '+id)
        if tool in ['export_result','condition'] and not supplied.intersection({'records','text','answer','documents','reviews','table'}) and not (tool=='condition' and settings['condition_operator']=='semantic'):issues.append('처리할 결과 입력이 필요합니다: '+id)
        if tool=='condition' and settings['condition_operator'] in ['all_pass','any_revision'] and 'reviews' not in supplied:issues.append('검수 판정 분기는 reviews 입력이 필요합니다: '+id)
        if tool=='condition' and settings['condition_operator']=='has_missing' and 'records' not in supplied:issues.append('누락 분기는 추출 결과 records가 필요합니다: '+id)
        if tool=='condition' and settings['condition_operator']=='semantic' and not settings['instructions']:issues.append('의미 분기의 판단 기준이 필요합니다: '+id)
        if tool=='extract_information' and not settings['fields']:issues.append('추출할 항목이 필요합니다: '+id)
        if tool=='extract_information' and settings.get('extraction_mode')=='rows':
            from workflow_engine.executors.document.extract_information import reserved_field_issue
            if reserved_field_issue(settings['fields']):issues.append(reserved_field_issue(settings['fields'])+': '+id)
        if tool=='ask_user' and not settings['question']:issues.append('확인 질문이 필요합니다: '+id)
        if tool=='export_result':
            if not supplied.intersection({'records','text','answer'}):issues.append('저장할 records/text/answer 결과 입력이 필요합니다: '+id)
            if 'records' in supplied and settings['format'] not in ['csv','json']:issues.append('표 결과는 CSV/JSON을 선택하세요: '+id)
            if 'records' not in supplied and supplied.intersection({'text','answer'}) and settings['format'] not in ['markdown','json']:issues.append('텍스트 결과는 Markdown 또는 JSON을 선택하세요: '+id)
        if tool in ['read_document','read_table']:
            lookup={r['id']:r for r in resources}
            if any(f not in lookup for f in settings['file_ids']):issues.append('연결되지 않은 파일 ID가 있습니다: '+id+'. file_ids에는 노드 ID가 아닌 이번 대화에 연결된 자료의 실제 ID만 사용하세요.')
            expected='document' if tool=='read_document' else 'table'
            if any(not supports(lookup[f],expected) for f in settings['file_ids'] if f in lookup):issues.append('읽기 노드와 파일 종류가 맞지 않습니다: '+id)
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
        if uses_operation(node):
            column_cache[id]=None;return None
        if tool=='read_table':
            ids=node['settings']['file_ids'] or list(table_resources)
            if len(ids)>1:issues.append('표 읽기 노드는 한 데이터 소스를 선택해야 합니다: '+id)
            if len(ids)==1 and ids[0] in table_resources:columns=set(table_resources[ids[0]]['columns'])
        elif tool=='review_summary':columns={'review_id','verdict','summary','candidate_reply','approval_state'}
        elif tool in ['filter_sort','aggregate_table','calculate_columns','reply_review']:
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
            elif tool=='calculate_columns':
                columns=set(incoming) if incoming is not None else None
                operation=node['settings'].get('table_operation')
                if columns is not None and operation=='compute':
                    from .table_formula import formula_columns
                    try:
                        if formula_columns(node['settings'].get('calculation_formula'))-columns:issues.append('계산 수식에 앞 단계에 없는 열이 있습니다: '+id)
                    except ValueError:pass
                    output=node['settings'].get('output_column')
                    if output in columns:issues.append('기존 열은 덮어쓸 수 없습니다: '+id)
                    elif output:columns.add(output)
                elif columns is not None and operation=='rename':
                    old,new=node['settings'].get('rename_from'),node['settings'].get('rename_to')
                    if old not in columns or (new!=old and new in columns):issues.append('기존 열이 없거나 바꿀 이름이 중복됩니다: '+id)
                    elif new:columns.remove(old);columns.add(new)
                elif columns is not None and operation=='parse_dates' and not set(node['settings'].get('fields',[]))<=columns:
                    issues.append('날짜 열이 앞 단계에 없습니다: '+id)
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
        if not uses_operation(node) and node['tool'] in ['read_table','filter_sort','aggregate_table','calculate_columns','reply_review']:columns_for(node['id'])
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

def _binding_chain(nodes,by):
    """The single linear order implied by bindings, or None if it is not unique."""
    if not nodes or any(n['tool'] in ('condition','ask_user') for n in nodes):return None
    parent={}
    for n in nodes:
        sources={b['node_id'] for b in n['bindings']}
        if not sources<=set(by) or len(sources)>1:return None
        if sources:parent[n['id']]=sources.pop()
    roots=[n['id'] for n in nodes if n['id'] not in parent]
    children={}
    for child,source in parent.items():
        if source in children:return None  # fan-out
        children[source]=child
    if len(roots)!=1:return None
    chain=[roots[0]]
    while chain[-1] in children and len(chain)<=len(nodes):chain.append(children[chain[-1]])
    return chain if len(chain)==len(nodes) and len(set(chain))==len(nodes) else None


def _edges_usable(workflow,by):
    """Edges reach every node from entry and order each binding source before its node."""
    edges=workflow['edges']
    if workflow['entry'] not in by or any(e['source'] not in by or e['target'] not in by for e in edges):return False
    after={id:set() for id in by}
    for e in edges:after[e['source']].add(e['target'])
    def reach(start):
        seen,stack=set(),[start]
        while stack:
            for nxt in after[stack.pop()]:
                if nxt not in seen:seen.add(nxt);stack.append(nxt)
        return seen
    if reach(workflow['entry'])|{workflow['entry']}!=set(by):return False
    return all(n['id'] in reach(b['node_id']) for n in workflow['nodes'] for b in n['bindings'])


def _reader_join_chain(nodes,by):
    """Serialize independent native reads already consumed by one explicit join.

    Reading two local sources in array order changes no data operation. Other
    ambiguous roots, branching, side effects and unused readers remain invalid.
    """
    roots=[n for n in nodes if not n['bindings']]
    if len(roots)<2 or any(n['tool'] not in ('read_table','read_document') or uses_operation(n) for n in roots):return None
    if any(n['tool'] in ('condition','ask_user','saved_workflow') for n in nodes):return None
    joins=[n for n in nodes if n['tool']=='join_tables' and n['settings'].get('operation')=='join']
    if len(joins)!=1:return None
    sources={b['node_id'] for b in joins[0]['bindings']}
    if sources!={n['id'] for n in roots}:return None
    dependencies={n['id']:{b['node_id'] for b in n['bindings']} for n in nodes}
    if any(not values<=set(by) for values in dependencies.values()):return None
    order=[]
    while len(order)<len(nodes):
        ready=[n for n in nodes if n['id'] not in order and dependencies[n['id']]<=set(order)]
        if not ready or len(ready)>1 and any(n not in roots for n in ready):return None
        order.extend(n['id'] for n in ready)
    # A single result chain must follow the join, with no extra consumers.
    previous=joins[0]['id']
    for identifier in order[len(roots)+1:]:
        if dependencies[identifier]!={previous}:return None
        previous=identifier
    return order


def normalize_connections(workflow):
    """Resolve unambiguous port aliases using registered types; never invent topology."""
    if not workflow:return workflow
    by={n['id']:n for n in workflow['nodes']}
    def node_of(reference,side):
        # "node:port" or "node.port" names one real node only when that node owns the port.
        # Node IDs cannot contain ':' or '.', so the separator is unambiguous.
        if reference in by or not isinstance(reference,str) or not re.search(r'[:.]',reference):return reference
        identifier,port=re.split(r'[:.](?=[^:.]*$)',reference)
        return identifier if identifier in by and port in node_ports(by[identifier])[side] else reference
    for edge in workflow['edges']:
        edge['source']=node_of(edge['source'],1);edge['target']=node_of(edge['target'],0)
    for node in workflow['nodes']:
        for binding in node['bindings']:
            binding['node_id']=node_of(binding['node_id'],1)
        # An exact repeat of the same binding adds nothing; keep one copy.
        node['bindings']=[b for i,b in enumerate(node['bindings']) if b not in node['bindings'][:i]]
    chain=_binding_chain(workflow['nodes'],by) or _reader_join_chain(workflow['nodes'],by)
    if chain and not _edges_usable(workflow,by):
        # The model states the data path twice (bindings and edges). When the edges are
        # unusable but bindings name one chain, or independent local readers feeding
        # one explicit join, serialize that data path. Other ambiguity stays invalid.
        workflow['edges']=[{'source':a,'target':b,'branch':'next'} for a,b in zip(chain,chain[1:])]
        workflow['entry']=chain[0]
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
            expected=inputs.get(binding['input'])
            if uses_operation(source) and binding['output']=='result' and output_type=='json' and expected and expected!='json':
                candidates=[name for name,kind in outputs.items() if kind==expected]
                if len(candidates)==1:
                    binding['output']=candidates[0]
                    output_type=expected
            if output_type and inputs.get(binding['input'])!=output_type:
                candidates=[name for name,type in inputs.items() if type==output_type]
                if len(candidates)==1:binding['input']=candidates[0]
    return workflow
