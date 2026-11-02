"""Validate and normalize workflow definitions without generating executable source."""
import re,math
from .registry import node_ports
from .document_resources import supports

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
            if b['node_id'] not in ancestors.get(id,set()):issues.append('앞 단계가 아닌 노드의 출력을 참조했습니다: '+id+' (참조: '+b['node_id']+'.'+b['output']+' → '+b['input']+'). 연결 순서를 확인하세요.')
        supplied={b['input'] for b in bindings}
        required={'filter_sort':{'table'},'aggregate_table':{'table'},'calculate_columns':{'table'},'join_tables':{'left','right'},'compare_tables':{'baseline','candidate'},'text_transform':{'text'},'base64_codec':{'text'},'text_hash':{'text'},'date_transform':{'text'},'text_statistics':{'text'},'json_transform':{'text'},'list_transform':{'text'},'review_summary':{'reviews'},'write_result':{'table'},'search_document':{'documents'},'extract_information':{'documents'},'compose_answer':{'documents'}}.get(tool,set())
        if not required<=supplied:issues.append('필수 입력 연결이 없습니다: '+id)
        if tool=='join_tables':
            keys=settings.get('join_keys')
            if not isinstance(keys,list) or not keys or len(keys)>10 or any(not isinstance(k,str) or not k.strip() for k in keys) or len(set(keys))!=len(keys):issues.append('중복되지 않은 결합 열을 지정하세요: '+id)
            if settings.get('join_how') not in ('inner','left') or settings.get('join_cardinality') not in ('one_to_one','many_to_one'):issues.append('결합 방식과 키 관계를 명시하세요: '+id)
        if tool=='list_transform':
            op=settings.get('list_operation');index=settings.get('list_index')
            if op not in ('select','count') or (op=='select' and (type(index) is not int or index<0)):issues.append('목록 작업과 0 이상 인덱스를 확인하세요: '+id)
        if tool=='compare_tables':
            keys=settings.get('compare_keys');cols=settings.get('compare_columns');tol=settings.get('compare_tolerance')
            if any(not isinstance(v,list) or not v or any(not isinstance(x,str) or not x.strip() for x in v) or len(set(v))!=len(v) for v in (keys,cols)) or type(tol) not in (int,float) or not math.isfinite(tol) or tol<0:issues.append('비교 키·열과 0 이상의 허용 오차를 지정하세요: '+id)
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
        if tool=='scalar_calculate':
            from .table_formula import formula_columns
            try:
                if formula_columns(settings.get('calculation_formula')):raise ValueError('variables not supported')
            except ValueError:issues.append('단일 계산에는 숫자 상수와 사칙연산만 사용하세요: '+id)
        if tool=='text_transform' and (not isinstance(settings.get('text_find'),str) or not settings['text_find'] or not isinstance(settings.get('text_replacement'),str)):
            issues.append('바꿀 문자열과 교체 문자열이 필요합니다: '+id)
        if tool=='base64_codec' and settings.get('codec_mode') not in ('encode','decode'):
            issues.append('Base64 인코딩 또는 디코딩을 선택하세요: '+id)
        if tool=='json_transform':
            operation=settings.get('json_operation');path=settings.get('json_path')
            if operation not in ('parse','lookup'):issues.append('JSON 해석 또는 값 조회를 선택하세요: '+id)
            elif operation=='lookup' and (not isinstance(path,list) or len(path)>30 or any(type(part) not in (str,int) or (type(part) is int and part<0) for part in path)):
                issues.append('JSON 조회 경로의 문자열 키와 0 이상 인덱스를 확인하세요: '+id)
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
        if tool=='ask_user' and not settings['question']:issues.append('확인 질문이 필요합니다: '+id)
        if tool=='export_result':
            if not supplied.intersection({'records','text','answer','documents'}):issues.append('저장할 records/text/answer/documents 결과 입력이 필요합니다: '+id)
            if 'documents' in supplied and len(supplied)>1:issues.append('문서 저장에는 다른 결과 입력을 함께 연결할 수 없습니다: '+id)
            if 'records' in supplied and settings['format'] not in ['csv','json']:issues.append('표 결과는 CSV/JSON을 선택하세요: '+id)
            if 'records' not in supplied and supplied.intersection({'text','answer','documents'}) and settings['format'] not in ['markdown','json']:issues.append('텍스트·문서 결과는 Markdown 또는 JSON을 선택하세요: '+id)
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
        if tool=='read_table':
            ids=node['settings']['file_ids'] or list(table_resources)
            if len(ids)>1:issues.append('표 읽기 노드는 한 데이터 소스를 선택해야 합니다: '+id)
            if len(ids)==1 and ids[0] in table_resources:columns=set(table_resources[ids[0]]['columns'])
        elif tool=='review_summary':columns={'review_id','verdict','summary','candidate_reply','approval_state'}
        elif tool=='join_tables':
            refs={b['input']:b['node_id'] for b in node['bindings']}
            left=columns_for(refs['left']) if 'left' in refs else None
            right=columns_for(refs['right']) if 'right' in refs else None
            keys=set(node['settings'].get('join_keys') or [])
            if left is not None and right is not None:
                if not keys<=left or not keys<=right:issues.append('결합 열이 양쪽 표에 있어야 합니다: '+id)
                columns=set(left)
                for col in sorted(right-keys):
                    dest=col+'_right' if col in columns else col
                    if dest in columns:issues.append('결합 결과의 열 이름이 충돌합니다: '+id)
                    columns.add(dest)
        elif tool=='compare_tables':
            refs={b['input']:b['node_id'] for b in node['bindings']}
            a=columns_for(refs['baseline']) if 'baseline' in refs else None
            b=columns_for(refs['candidate']) if 'candidate' in refs else None
            required=set((node['settings'].get('compare_keys') or [])+(node['settings'].get('compare_columns') or []))
            if a is not None and b is not None and (not required<=a or not required<=b):issues.append('비교 키와 열이 양쪽 표에 있어야 합니다: '+id)
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
        if node['tool'] in ['read_table','filter_sort','aggregate_table','calculate_columns','join_tables','compare_tables','reply_review']:columns_for(node['id'])
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

def normalize_connections(workflow,audit=None):
    """Resolve port aliases and serialize an unambiguous pair of file reads."""
    if not workflow:return workflow
    by={n['id']:n for n in workflow['nodes']}
    for edge in workflow['edges']:
        if edge['source'] in by and by[edge['source']]['tool']!='condition':edge['branch']='next'
    # A model may express two independent read_table nodes as parallel inputs
    # to a join. This runtime has one active path. Only serialize the exact,
    # side-effect-free two-root shape, preserving the same inputs and join.
    for join in workflow['nodes']:
        if join.get('tool') not in ('join_tables','compare_tables'):continue
        pair=('left','right') if join['tool']=='join_tables' else ('baseline','candidate')
        refs={b['input']:b['node_id'] for b in join.get('bindings',[])}
        if not all(k in refs for k in pair):continue
        a,b=(refs[k] for k in pair)
        if a==b or any(x not in by or by[x]['tool']!='read_table' or by[x].get('bindings') for x in (a,b)):continue
        incoming_a=[e for e in workflow['edges'] if e['target']==a]
        incoming_b=[e for e in workflow['edges'] if e['target']==b]
        in_join=[e for e in workflow['edges'] if e['target']==join['id']]
        out_a=[e for e in workflow['edges'] if e['source']==a]
        out_b=[e for e in workflow['edges'] if e['source']==b]
        if (incoming_a or incoming_b or workflow['entry'] not in (a,b) or len(in_join)!=2
            or len(out_a)!=1 or len(out_b)!=1
            or any(e['target']!=join['id'] or e['branch']!='next' for e in out_a+out_b)):
            continue
        first=workflow['entry'];second=b if first==a else a
        next(e for e in workflow['edges'] if e['source']==first)['target']=second
        if audit is not None:audit.append({'type':'serialize_independent_table_reads','first':first,'second':second,'target':join['id']})
    if workflow['entry'] not in by:
        targets={e['target'] for e in workflow['edges']}
        roots=[id for id in by if id not in targets]
        if len(roots)==1:workflow['entry']=roots[0]
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
