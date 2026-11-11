"""Compare request criteria with executable settings and data bindings.

An AI's statement that a task is implemented is not accepted as a match.
Unknown semantics are held for review, rather than silently dropped.
"""
import ast
import json
import os
from decimal import Decimal

from contracts.numeric_criteria import number_value
from .requirement_ledger import ledger_fingerprint, plan_digest, validate_plan


def _formula(value):
    from workflow_engine.table_formula import formula_columns
    formula_columns(value)
    def key(n):
        if isinstance(n,ast.BinOp):
            pair=[key(n.left),key(n.right)]
            if isinstance(n.op,(ast.Add,ast.Mult)):pair.sort(key=repr)
            return (type(n.op).__name__,*pair)
        if isinstance(n,ast.UnaryOp):return (type(n.op).__name__,key(n.operand))
        if isinstance(n,ast.Name):return ('name',n.id)
        return ('number',str(Decimal(str(n.value)).normalize()))
    return key(ast.parse(value,mode='eval').body)


def _condition(value):
    result=dict(value)
    if result.get('value_type')=='number' and result.get('value') is not None:
        if isinstance(result['value'],bool):raise ValueError('숫자 기준에 bool을 사용할 수 없습니다.')
        result['value']=str(number_value(result['value']).normalize())
    return json.dumps(result,ensure_ascii=False,sort_keys=True)


def _same_filters(actual, expected, actual_match, expected_match):
    same_match=(actual_match==expected_match or
                len(actual)==len(expected)==1 and actual_match in ('all','any') and expected_match in ('all','any'))
    return (same_match and
            sorted(map(_condition,actual))==sorted(map(_condition,expected)))


def _arguments(node):
    from workflow_engine.catalog.canonical import parse_arguments
    return parse_arguments(node['settings']) if node['settings'].get('operation') else {}


def _matches(spec,node,draft,sources):
    tool=node['tool'];settings=node['settings'];operation=settings.get('operation')
    args=_arguments(node);kind=spec['kind']
    if kind=='document_analysis':
        if tool!='compose_answer' or operation or spec['request'] not in (settings.get('instructions') or ''):return False
        return any(b['input']=='documents' and b['output']=='documents' for b in node['bindings'])
    if kind=='human_review':
        # An explicit execution-time review must really pause, not let an LLM
        # treat the earlier design request as an already supplied confirmation.
        if (tool!='ask_user' or operation or not (settings.get('question') or '').strip()
                or settings.get('instructions')):return False
        # The executor previews records, falling back to text only for empty
        # records. With both inputs, the requested result could be hidden.
        previews=[b for b in node['bindings'] if b['input'] in ('records','text')]
        if len(previews)!=1:return False
        binding=previews[0]
        source=next((n for n in draft['workflow']['nodes'] if n['id']==binding['node_id']),None)
        from workflow_engine.catalog.registry import node_ports
        return bool(source and node_ports(source)[1].get(binding['output'])==binding['input'])
    if kind=='rename':
        return (tool=='calculate_columns' and (
            not operation and settings.get('table_operation')=='rename' and settings.get('rename_from')==spec['source'] and settings.get('rename_to')==spec['target']
            or operation=='rename_columns' and args.get('mapping')=={spec['source']:spec['target']}))
    if kind=='fill_missing':
        # JSON booleans are not numbers, even though Python equates False/0 and True/1.
        return (tool=='filter_sort' and operation=='handle_missing' and args.get('action')=='fill'
                and args.get('columns')==spec['fields']
                and isinstance(args.get('fill_value'),bool)==isinstance(spec['value'],bool)
                and args.get('fill_value')==spec['value'])
    if kind=='select_fields':
        return tool=='filter_sort' and operation=='select_columns' and args.get('columns')==spec['fields']
    if kind=='schedule':
        from contracts.models import ScheduleSpec
        return (bool(draft.get('schedule')) and
                ScheduleSpec.model_validate(draft['schedule']).model_dump()==spec['schedule'] and
                draft.get('report_destination')=='reports')
    if kind in ('gmail_summary', 'gmail_scheduled_summary'):
        if tool!='compose_answer' or operation or settings.get('instructions')!=(spec.get('summary_instructions') or spec['request']):return False
        documents=[binding for binding in node['bindings'] if binding['input']=='documents']
        if len(documents)!=1 or documents[0]['output']!='documents':return False
        source=next((n for n in draft['workflow']['nodes'] if n['id']==documents[0]['node_id']),None)
        if not source or source['tool']!='email.list' or source['settings'].get('operation')!='read_messages':return False
        read_args=_arguments(source)
        if kind=='gmail_scheduled_summary':
            if draft.get('schedule')!=spec['schedule'] or draft.get('report_destination')!='reports':return False
            query=spec['query']
        else:
            query=spec.get('query')
        return (not source['bindings'] and read_args.get('max_results')==spec['max_results']
                and read_args.get('query')==query and set(read_args)<={'query','max_results'})
    if kind=='read':
        return tool in ('read_table','read_document') and set(spec['file_ids'])<=sources(node['id'])
    if kind in ('extract_fields', 'receipt_items'):
        mode = 'fields' if kind == 'extract_fields' else 'receipt_items'
        return (tool=='extract_information' and not operation and settings.get('extraction_mode')==mode
                and settings.get('fields')==spec['fields'])
    if kind=='receipt_workbook':
        if tool != 'create_spreadsheet' or operation != 'create':
            return False
        from workflow_engine.executors.canonical_operation import validate_settings
        _, validated = validate_settings(node)
        sheets = validated.get('sheets')
        if not isinstance(sheets, list) or [s['name'] for s in sheets] != spec['sheets']:
            return False
        bindings = {b['input']: b for b in node['bindings']}
        producers = {n['id']: n for n in draft['workflow']['nodes']}
        for sheet in sheets:
            table = sheet['table']
            binding = bindings.get(table.get('$binding')) if isinstance(table, dict) and len(table) == 1 else None
            if not binding or binding['output'] != 'table':
                return False
            producer = producers.get(binding['node_id'], {})
            mode = 'receipt_items' if sheet['name'] == '상품내역' else 'fields'
            if producer.get('tool') != 'extract_information' or producer['settings'].get('extraction_mode') != mode:
                return False
        return True
    if kind=='compute':
        if tool!='calculate_columns':return False
        if operation in ('compute','calculate_records'):
            formula=args.get('formula');column=args.get('output_column')
        elif not operation and settings.get('table_operation')=='compute':
            formula=settings.get('calculation_formula');column=settings.get('output_column')
        else:return False
        return column==spec['output_column'] and _formula(formula)==_formula(spec['formula'])
    if kind=='parse_dates':
        if tool!='calculate_columns':return False
        if operation=='parse_dates':
            fields=args.get('columns');fmt=args.get('input_format');zone=args.get('timezone')
        elif not operation and settings.get('table_operation')=='parse_dates':
            fields=settings.get('fields');fmt=settings.get('date_input_format');zone=settings.get('date_timezone')
        else:return False
        return (isinstance(fields,list) and set(fields)==set(spec['fields'])
                and fmt==spec['input_format'] and zone==spec['timezone'])
    if kind=='filter':
        if tool!='filter_sort' or operation:return False
        plan=settings.get('execution_plan') or draft['execution_plan']
        return _same_filters(plan['filters'],spec['filters'],plan['match'],spec['match'])
    if kind=='sort':
        if tool!='filter_sort' or operation:return False
        plan=settings.get('execution_plan') or draft['execution_plan']
        return plan['sort']==spec['sort']
    if kind=='aggregate':
        if tool!='aggregate_table':return False
        if operation in ('sum','count','group_aggregate'):
            mode=args.get('operation') if operation=='group_aggregate' else operation
            if operation=='count' and args.get('mode')!='rows':return False
            fields=args.get('group_by');column=args.get('value_column',args.get('column'))
        elif not operation:
            mode=settings.get('aggregation') or 'count';fields=settings.get('fields',[]);column=settings.get('sum_field')
        else:return False
        return (mode==spec['aggregation'] and fields==spec['fields']
                and (mode=='count' or column==spec['sum_field']))
    if kind=='join':
        if tool!='join_tables' or operation!='join':return False
        if any(args.get(k)!=spec[k] for k in ('keys','how','cardinality','null_keys','max_rows')):return False
        for side in ('left','right'):
            # Operation arguments may explicitly reference a differently named binding.
            table = args.get(side, {'$binding': side})
            port = table.get('$binding') if isinstance(table, dict) and len(table) == 1 else None
            binding=next((b for b in node['bindings'] if b['input']==port),None)
            if spec[side+'_sources'] and (not binding or set(spec[side+'_sources'])!=sources(binding['node_id'])):return False
        return True
    if kind=='export':
        fmt=spec['format']
        if tool in ('write_result','export_result') and not operation:
            return settings.get('format')==('markdown' if fmt=='md' else fmt)
        if tool=='export_result' and operation in ('serialize','write_json','write_csv'):
            return args.get('format',operation.removeprefix('write_'))==fmt
        if tool=='create_spreadsheet' and fmt=='xlsx':
            from workflow_engine.executors.canonical_operation import validate_settings
            op, workbook_args = validate_settings(node)
            if op['id'] != 'create':
                return False
            sheets = workbook_args.get('sheets')
            # Literal workbooks are valid exports. With no bindings they cannot
            # satisfy the separate dependency check for a requested processing step.
            if not node['bindings']:
                return True
            # For a processed workbook, verify the actual sheet table binding.
            if not isinstance(sheets,list) or len(sheets)!=1:return False
            table=sheets[0].get('table')
            return (isinstance(table,dict) and set(table)=={'$binding'} and
                    any(b['input']==table['$binding'] and b['output']=='table' for b in node['bindings']))
        return False
    if kind=='policy':
        if tool!='filter_sort' or operation:return False
        plan=settings.get('execution_plan') or draft['execution_plan']
        requested={k:spec[k] for k in ('missing_policy','duplicate_policy','duplicate_field') if spec[k] is not None}
        return bool(requested) and all(plan.get(k)==v for k,v in requested.items())
    return False


def coverage_report(state,draft):
    if not state.get('requirement_required') and os.getenv('A4B_REQUIREMENT_MODE','off')!='enforce':
        return dict(enabled=False,issues=[],tasks=[])
    problems=[]
    ledger=state.get('requirement_ledger')
    try:
        if not ledger or state.get('requirement_fingerprint')!=ledger_fingerprint(state):
            raise ValueError('요청·자료가 바뀌었거나 원문 업무 목록이 없습니다.')
        if state.get('requirement_digest')!=plan_digest(ledger):
            raise ValueError('요구사항 목록이 추출 후 변경됐습니다.')
        plan=validate_plan(ledger,state)
        if plan['unresolved']:
            raise ValueError(' / '.join(question['text'] if isinstance(question,dict) else question
                                       for question in plan['unresolved']))
    except (ValueError,TypeError,KeyError) as error:
        return dict(enabled=True,tasks=[],issues=['요구사항 검증: 원문·자료에 맞는 업무 목록을 다시 확인해야 합니다. '+str(error)])
    workflow=draft.get('workflow') or {};nodes={n['id']:n for n in workflow.get('nodes',[])}
    from .content_lineage import content_ancestors
    from .reader_sources import reader_sources
    upstream={id:content_ancestors(nodes,id) for id in nodes}
    for id,n in nodes.items():
        if n['tool']=='ask_user':
            # A terminal human check consumes its preview even when no later
            # output port is requested. Export lineage remains port-specific.
            upstream[id]=set().union(*(upstream.get(b['node_id'],set())|{b['node_id']} for b in n['bindings']))
    def sources(id):
        result=set()
        for parent in upstream.get(id,set()) | {id}:
            n=nodes.get(parent)
            if n and n['tool'] in ('read_table','read_document'):
                result.update(reader_sources(n,state.get('resources',[]),draft.get('document_ids',[])))
        return result
    if any(e.get('branch') in ('yes','no') for e in workflow.get('edges',[])):
        problems.append('요구사항 검증: 분기별 업무 포함 여부는 추가 검토가 필요합니다. 공통 경로의 포함만으로 승인하지 않습니다.')
    results=[];candidates={}
    for task in plan['tasks']:
        spec=task['spec'];matched=[]
        if spec['kind']!='other':
            for id,n in nodes.items():
                try:
                    if _matches(spec,n,draft,sources):matched.append(id)
                except (ValueError,TypeError,KeyError,ArithmeticError):pass
        status='matched' if matched else 'unverified' if spec['kind']=='other' else 'missing_or_mismatched'
        candidates[task['id']]=matched
        results.append(dict(requirement_id=task['id'],description=task['description'],kind=spec['kind'],
                            status=status,node_ids=matched,expected=spec))
        if not matched:
            reason='현재 규칙으로 실행 설정을 검증할 수 없습니다' if spec['kind']=='other' else '일치하는 실제 실행 단계·설정이 없습니다'
            problems.append(f"요구사항 검증: {task['id']} {task['description']} — {reason}.")
    # Find a single consistent assignment, not disconnected matches in different
    # branches or unused parallel inputs. Same-node filter/sort/policy is valid.
    tasks=plan['tasks'];selected={};attempts=0
    exports=[t['id'] for t in tasks if t['spec']['kind'] in ('export','receipt_workbook')]
    from workflow_engine.execution_path import artifact_output_ids, artifact_origin
    try:
        output_ids=artifact_output_ids(workflow)
        final_output=artifact_origin(workflow,output_ids[-1]) if output_ids else None
    except (ValueError,KeyError,TypeError) as error:
        final_output=None
        problems.append('요구사항 검증: 최종 다운로드 경로를 확인할 수 없습니다. '+str(error))
    if len(exports)>1:
        problems.append('요구사항 검증: 복수 결과 파일의 저장 위치·개별 출력은 추가 검토가 필요합니다.')
    def assign(index):
        nonlocal attempts
        attempts+=1
        if attempts>4096:return False
        if index==len(tasks):
            if exports and any(selected[e]!=final_output for e in exports):return False
            return all(any(selected[t['id']]==selected[e] or selected[t['id']] in upstream[selected[e]] for e in exports)
                       for t in tasks if t['id'] not in exports) if exports else True
        task=tasks[index]
        for node_id in candidates[task['id']]:
            if all(selected[d]==node_id or selected[d] in upstream[node_id] for d in task['depends_on']):
                selected[task['id']]=node_id
                if assign(index+1):return True
        selected.pop(task['id'],None)
        return False
    if not problems and not assign(0):
        problems.append('요구사항 검증: 처리 단계의 데이터 연결·선행 업무·최종 저장 경로가 요청과 일치하지 않습니다.')
    if not problems:
        for item in results:item['node_ids']=[selected[item['requirement_id']]];item['status']='covered'
    return dict(enabled=True,issues=problems,tasks=results)


def requirement_issues(state,draft):
    # Pending graphs may be shown as a design preview, never persisted as an
    # executable configuration, even when semantic ledger checking is disabled.
    if draft.get('questions') or draft.get('unsupported'):
        return ['요구사항 검증: 질문·미지원이 남은 초안은 승인하거나 저장할 수 없습니다.']
    if not state.get('requirement_required') and os.getenv('A4B_REQUIREMENT_MODE','off')!='enforce':return []
    return coverage_report(state,draft)['issues']
