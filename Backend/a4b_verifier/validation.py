"""결정론적 구조/출처 검증. 사용자 의도 자체의 정답 판정기는 아니다."""
import json,re
from decimal import Decimal,InvalidOperation
from urllib.parse import quote
from .contracts import VerificationInput,VerificationOutput,CodeFinding

def evidence_bank(data: VerificationInput):
    bank={'user_request':data.user_request}
    for m in data.messages:
        if m.role=='user':bank['message/'+quote(m.id,safe='')]=m.content
    for d in data.documents:
        prefix='document/'+quote(d.id,safe='')+'/'+quote(d.version,safe='')
        for c in d.chunks:bank[prefix+'/chunk/'+quote(c.id,safe='')]=c.text
        for t in d.tables:
            bank[prefix+'/table/'+quote(t.id,safe='')]=json.dumps({'columns':t.columns,'sample_rows':t.sample_rows},ensure_ascii=False,allow_nan=False)
    return bank

def pointer(root,path):
    if path=='':return root
    if not path.startswith('/'):raise ValueError('JSON Pointer는 /로 시작해야 합니다.')
    value=root
    for part in path.split('/')[1:]:
        # RFC6901 escaping, no Python attribute access or evaluation.
        if re.search(r'~(?![01])',part):
            raise ValueError('JSON Pointer 이스케이프가 잘못됐습니다.')
        part=part.replace('~1','/').replace('~0','~')
        if isinstance(value,list):
            if not part.isdigit() or (len(part)>1 and part[0]=='0'):raise ValueError('잘못된 배열 인덱스입니다.')
            value=value[int(part)]
        elif isinstance(value,dict):value=value[part]
        else:raise ValueError('스칼라 아래를 참조했습니다.')
    return value

def canonical(value):
    if isinstance(value,bool):return 'true' if value else 'false'
    if value is None:return 'null'
    if isinstance(value,(int,float,Decimal)):
        x=Decimal(str(value))
        if not x.is_finite():raise ValueError('유한한 수가 필요합니다.')
        return format(x.normalize(),'f')
    if isinstance(value,str):
        # Numeric strings normalize only conventional JSON numbers, never currency/unit prose.
        try:
            x=json.loads(value)
            if isinstance(x,(int,float)) and not isinstance(x,bool):return canonical(x)
        except (ValueError,TypeError):pass
        return value
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))

def code_checks(data: VerificationInput):
    findings=[]
    def add(code,detail,path=None,severity='block'):
        findings.append(CodeFinding(code=code,severity=severity,detail=detail,path=path))
    # Never silently accept NaN/Infinity in loosely typed node settings/sample rows.
    try:json.dumps(data.model_dump(mode='python'),allow_nan=False)
    except ValueError:add('nonfinite_json','입력에 NaN 또는 Infinity가 있습니다.');return findings
    if not data.documents or any(d.parse_status!='ready' for d in data.documents):
        add('evidence_incomplete','문서 파싱 누락·부분 실패가 있어 자동 확정할 수 없습니다.',severity='review')
    draft=data.candidate_draft
    if draft is None:return findings
    doc_by={d.id:d for d in data.documents}
    for did in draft.document_ids:
        if did not in doc_by:add('unknown_document','초안의 문서 ID가 입력에 없습니다: '+did)
    wf=draft.workflow;nodes={n.id:n for n in wf.nodes};tools={t.id:t for t in data.tools}
    if len(nodes)!=len(wf.nodes):add('duplicate_node','노드 ID가 중복됩니다.')
    if wf.entry not in nodes:add('unknown_entry','entry 노드가 없습니다.')
    graph={n:[] for n in nodes};indegree={n:0 for n in nodes};seen=set()
    for e in wf.edges:
        key=(e.source,e.target,e.branch)
        if key in seen:add('duplicate_edge','연결이 중복됩니다.')
        seen.add(key)
        if e.source not in nodes or e.target not in nodes:add('unknown_edge_node','연결 대상 노드가 없습니다.');continue
        if e.branch in ['yes','no'] and nodes[e.source].tool!='condition':add('invalid_branch','yes/no는 condition 노드에서만 시작합니다.')
        graph[e.source].append(e.target);indegree[e.target]+=1
    queue=[n for n,d in indegree.items() if d==0];order=[]
    while queue:
        n=queue.pop();order.append(n)
        for other in graph[n]:
            indegree[other]-=1
            if indegree[other]==0:queue.append(other)
    if len(order)!=len(nodes):add('cycle','순환 워크플로우는 지원하지 않습니다.')
    def reachable(start):
        reached=set();todo=[start]
        while todo:
            n=todo.pop()
            if n in reached:continue
            reached.add(n);todo.extend(graph.get(n,[]))
        return reached
    if wf.entry in nodes and reachable(wf.entry)!=set(nodes):add('unreachable_node','entry에서 도달할 수 없는 노드가 있습니다.')
    node_columns={};node_docs={}
    for nid in order:
        n=nodes[nid];t=tools.get(n.tool);path='/workflow/nodes/'+str(wf.nodes.index(n))
        if t is None or not t.enabled:add('unavailable_tool','미등록 또는 비활성 도구입니다: '+n.tool,path);continue
        for key in t.required_settings:
            if key not in n.settings or n.settings[key] is None:add('missing_setting','필수 설정 누락: '+key,path+'/settings')
        bound=set()
        inherited=[]
        for b in n.bindings:
            if b.input in bound:add('duplicate_binding','입력 포트에 여러 연결이 있습니다.',path)
            bound.add(b.input);source=nodes.get(b.node_id);st=tools.get(source.tool) if source else None
            if source is None or st is None:add('unknown_binding_node','입출력 원본 노드를 찾지 못했습니다.',path);continue
            if b.node_id==n.id or n.id not in reachable(b.node_id):add('binding_dependency','입출력 연결의 실행 의존성이 없습니다.',path)
            if b.input not in t.input_ports or b.output not in st.output_ports:add('unknown_port','미등록 입출력 포트입니다.',path);continue
            if t.input_ports[b.input]!=st.output_ports[b.output]:add('port_type_mismatch','입출력 자료형이 다릅니다.',path)
            if b.input=='table' and b.node_id in node_columns:
                inherited.append(node_columns[b.node_id]);node_docs[nid]=node_docs.get(b.node_id,[])
        for key in t.required_input_ports:
            if key not in bound:add('missing_binding','필수 입력 연결 누락: '+key,path)
        if t.check_scope=='dynamic':add('dynamic_node_review','동적 노드의 전체 의미 검증은 메인 실행기의 추가 검사가 필요합니다.',path,'review')
        if n.tool in ['read_table','read_document']:
            ids=n.settings.get('file_ids')
            if not isinstance(ids,list) or not ids or any(not isinstance(x,str) for x in ids):add('missing_file','읽을 file_ids가 필요합니다.',path)
            else:
                for did in ids:
                    if did not in doc_by:add('unknown_file','읽을 문서가 입력에 없습니다: '+did,path)
                ds=[doc_by[x] for x in ids if x in doc_by];node_docs[nid]=ids
                if n.tool=='read_table':
                    if len(ds)!=1 or len(ds[0].tables)!=1:add('ambiguous_table','표 읽기 예제는 문서 한 개의 표 한 개를 요구합니다.',path)
                    else:node_columns[nid]=set(ds[0].tables[0].columns)
        elif inherited:
            if any(v!=inherited[0] for v in inherited):add('ambiguous_table_input','여러 표 스키마가 충돌합니다.',path)
            node_columns[nid]=set(inherited[0])
        if n.tool=='aggregate_table':
            fields=n.settings.get('fields');operation=n.settings.get('aggregation') or 'count'
            if not isinstance(fields,list) or any(not isinstance(f,str) for f in fields):
                add('invalid_aggregate_fields','집계 fields는 문자열 목록이어야 합니다.',path)
            elif operation not in ['count','sum']:add('invalid_aggregation','집계는 count/sum만 지원합니다.',path)
            elif nid not in node_columns:add('unresolved_table','집계 원본 스키마를 확인할 수 없습니다.',path,'review')
            else:
                oldcols=node_columns[nid];resultcol='sum' if operation=='sum' else 'count'
                if any(f not in oldcols for f in fields) or resultcol in fields:add('invalid_aggregate_column','집계 열이 없거나 출력 열과 충돌합니다.',path)
                if operation=='sum' and n.settings.get('sum_field') not in oldcols:add('missing_sum_field','합계할 실제 열이 없습니다.',path)
                node_columns[nid]=set(fields)|{resultcol}
        if n.tool=='filter_sort':
            plan=n.settings.get('execution_plan')
            if not isinstance(plan,dict):add('invalid_filter_plan','execution_plan 객체가 필요합니다.',path);continue
            if plan.get('match','all') not in ['all','any']:add('invalid_match','조건 결합은 all/any만 지원합니다.',path)
            if plan.get('missing_policy','ask') not in ['ask','exclude','keep','error']:add('invalid_missing_policy','빈값 정책이 잘못됐습니다.',path)
            if plan.get('duplicate_policy','keep') not in ['ask','keep','first','last','error']:add('invalid_duplicate_policy','중복 정책이 잘못됐습니다.',path)
            sorts=plan.get('sort',[])
            if not isinstance(sorts,list) or len(sorts)>5:add('invalid_sort','정렬 목록은 최대 5개입니다.',path)
            else:
                for sort in sorts:
                    if not isinstance(sort,dict) or not isinstance(sort.get('field'),str) or sort.get('direction') not in ['ascending','descending'] or sort.get('value_type') not in ['number','text']:
                        add('invalid_sort','정렬 열·방향·자료형이 잘못됐습니다.',path);continue
                    if nid in node_columns and sort['field'] not in node_columns[nid]:add('unknown_sort_column','정렬 열이 실제 표에 없습니다.',path)
            filters=plan.get('filters',[])
            if not isinstance(filters,list) or len(filters)>20:add('invalid_filters','필터 목록은 최대 20개입니다.',path);continue
            for f in filters:
                if not isinstance(f,dict):add('invalid_filter','필터 객체가 필요합니다.',path);continue
                field=f.get('field');op=f.get('operator');other=f.get('compare_field');value=f.get('value')
                if not isinstance(field,str) or not field or (other is not None and not isinstance(other,str)):
                    add('invalid_filter_field','조건 열과 비교 열은 문자열이어야 합니다.',path);continue
                if nid not in node_columns:add('unresolved_table','필터의 원본 표 스키마를 확인할 수 없습니다.',path,'review')
                elif field not in node_columns[nid] or (other is not None and other not in node_columns[nid]):add('unknown_column','실제 표에 없는 조건 열입니다.',path)
                if op not in ['eq','ne','lt','lte','gt','gte','contains','is_empty','not_empty']:add('unknown_operator','미지원 연산자입니다.',path)
                if f.get('value_type') not in ['number','text','boolean']:add('invalid_filter_type','필터 자료형이 잘못됐습니다.',path)
                if op not in ['is_empty','not_empty']:
                    if other is None and value is None:add('missing_filter_value','필터 기준값이 없습니다.',path)
                    if other is not None and value is not None:add('conflicting_filter_value','열 비교와 상수값을 동시에 설정했습니다.',path)
                if f.get('value_type')=='number' and value is not None:
                    try:
                        if isinstance(value,bool) or not Decimal(str(value)).is_finite():raise InvalidOperation
                    except (ValueError,InvalidOperation):add('invalid_numeric_value','숫자 비교에는 유한한 숫자가 필요합니다.',path)
        if n.tool=='write_result' and n.settings.get('format') not in ['csv','json']:
            add('invalid_output_format','결과 저장은 csv/json만 지원합니다.',path)
    return findings

def output_checks(data:VerificationInput,out:VerificationOutput):
    findings=[]
    def add(code,detail):findings.append(CodeFinding(code=code,severity='review',detail=detail))
    bank=evidence_bank(data)
    if out.request_status=='ready' and (out.missing_conditions or out.unsupported_actions):add('contradictory_ready','ready와 누락·지원 불가 항목이 동시에 있습니다.')
    if out.request_status=='clarify' and not out.missing_conditions:add('missing_clarification_detail','clarify에는 구체적인 누락 조건이 필요합니다.')
    if out.request_status=='unsupported' and not out.unsupported_actions:add('missing_unsupported_reason','지원 불가의 필수 작업을 명시해야 합니다.')
    if out.request_status is None and out.evidence_status=='sufficient':add('missing_status','근거가 충분하면 요청 상태가 필요합니다.')
    if out.evidence_status=='insufficient' and (out.request_status is not None or out.draft_verdict=='pass' or out.reflection_verdict=='pass'):
        add('insufficient_evidence_pass','근거 부족 상태를 자동 확정하거나 통과시켰습니다.')
    expected_draft='not_applicable' if data.candidate_draft is None else None
    if expected_draft and out.draft_verdict!=expected_draft:add('draft_not_applicable','초안이 없으므로 초안 검증은 비적용입니다.')
    if data.candidate_draft is not None and out.draft_verdict=='not_applicable':add('draft_check_omitted','제공된 초안의 검증을 생략했습니다.')
    if data.latest_user_answer_id is None:
        if out.reflection_verdict!='not_applicable':add('reflection_not_applicable','사용자 후속 답변이 없어 반영 검증은 비적용입니다.')
    elif data.previous_draft is None or data.candidate_draft is None:
        if out.reflection_verdict!='undetermined':add('missing_reflection_inputs','반영 검증에 필요한 이전·수정 초안이 부족합니다.')
    elif out.reflection_verdict=='not_applicable':add('reflection_check_omitted','사용자 답변과 초안 쌍이 있는데 반영 검증을 생략했습니다.')
    if out.reflection_verdict=='fail' and out.draft_verdict=='pass':add('contradictory_reflection_pass','답변 미반영 초안을 전체 통과시켰습니다.')
    semantic_codes={i.code for i in out.issues}
    if 'request_missing' in semantic_codes and out.request_status!='clarify':add('contradictory_missing','누락 진단과 요청 상태가 충돌합니다.')
    if 'unsupported' in semantic_codes and out.request_status!='unsupported':add('contradictory_unsupported','지원 불가 진단과 요청 상태가 충돌합니다.')
    if 'draft_mismatch' in semantic_codes and out.draft_verdict!='fail':add('contradictory_draft','초안 불일치와 판정이 충돌합니다.')
    if 'reflection_mismatch' in semantic_codes and out.reflection_verdict!='fail':add('contradictory_reflection','반영 불일치와 판정이 충돌합니다.')
    if (out.draft_verdict=='fail' or out.reflection_verdict=='fail') and not ({'draft_mismatch','reflection_mismatch'}&semantic_codes):
        add('missing_mismatch_detail','실패 판정에 불일치 진단이 없습니다.')
    citations=list(out.evidence)
    for m in out.missing_conditions:
        citations.extend(m.evidence)
        if not m.evidence:add('missing_evidence','누락 조건에 원문 근거가 없습니다.')
    for i in out.issues:
        citations.extend(i.evidence)
        if not i.evidence:add('missing_evidence','진단에 원문 근거가 없습니다.')
        if i.code in ['draft_mismatch','reflection_mismatch']:
            if i.path is None or i.expected is None or i.actual is None:add('missing_mismatch_location','불일치에는 path/expected/actual이 필요합니다.');continue
            try:
                actual=pointer(data.candidate_draft.model_dump(mode='json'),i.path)
                if canonical(actual)!=canonical(i.actual):add('fabricated_actual','진단한 actual이 실제 초안 값과 다릅니다.')
                if canonical(i.expected)==canonical(i.actual):add('equal_mismatch','기대값과 실제값이 같은데 불일치로 보고했습니다.')
            except (ValueError,KeyError,IndexError,AttributeError):add('invalid_mismatch_path','불일치 위치가 실제 초안을 가리키지 않습니다.')
    for c in citations:
        if c.evidence_id not in bank or c.quote not in bank[c.evidence_id]:add('invalid_quote','근거 ID 또는 원문 인용이 일치하지 않습니다.')
    if out.evidence_status=='sufficient' and not citations:add('no_evidence','충분 판정에는 최소 한 개의 원문 근거가 필요합니다.')
    if data.latest_user_answer_id and data.previous_draft and data.candidate_draft and out.reflection_verdict in ['pass','fail']:
        if not any(c.evidence_id=='message/'+quote(data.latest_user_answer_id,safe='') for c in citations):
            add('missing_latest_answer_evidence','답변 반영 판정에 최신 사용자 답변의 인용이 없습니다.')
    return findings

def choose_route(data,out,findings):
    # Output-contract / evidence failures are not user ambiguity or plan repair.
    if any(f.severity=='review' for f in findings) or out.evidence_status=='insufficient' or out.request_status is None:return 'manual_review'
    if out.request_status=='unsupported':return 'unsupported'
    if out.request_status=='clarify':return 'ask_user'
    if any(f.severity=='block' for f in findings) or out.draft_verdict=='fail' or out.reflection_verdict=='fail':return 'repair_plan'
    if out.draft_verdict=='undetermined' or out.reflection_verdict=='undetermined':return 'manual_review'
    return 'continue_review'
