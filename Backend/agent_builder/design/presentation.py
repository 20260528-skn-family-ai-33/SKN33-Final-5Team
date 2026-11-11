"""Render deterministic table settings from actual executable parameters."""
import json
PURE_TABLE={'read_table','filter_sort','aggregate_table','write_result','finish'}
OPS={'eq':'=','ne':'≠','lt':'<','lte':'≤','gt':'>','gte':'≥','contains':'포함','is_empty':'빈 값','not_empty':'값 있음'}
def canonical_table_draft(draft,messages,resources):
    workflow=draft.get('workflow')
    if not workflow or any(n['tool'] not in PURE_TABLE or n['settings'].get('operation') for n in workflow['nodes']):return draft
    plan=draft['execution_plan'];schedule=draft.get('schedule');lines=[]
    if schedule:
        frequency='매일' if schedule['frequency']=='daily' else '매주 '+(['월','화','수','목','금','토','일'][schedule['weekday']]+'요일' if schedule['weekday'] is not None else '요일 확인 필요')
        lines.append(f"예약: {frequency} {schedule['hour'] if schedule['hour'] is not None else '시간 확인'}:{str(schedule['minute'] or 0).zfill(2)} · {schedule['timezone'] or '시간대 확인 필요'}")
    lines.append('입력: 연결한 관리 데이터 소스의 실행 시점 버전을 읽습니다.' if schedule else '입력: 이번 실행 자료 또는 연결한 표 파일을 읽습니다.')
    lines.append('처리 순서: '+' → '.join(n['label'] for n in workflow['nodes']))
    if any(n['tool']=='filter_sort' for n in workflow['nodes']):
        conditions=[]
        for f in plan['filters']:
            right=f['compare_field'] if f['compare_field'] else '' if f['operator'] in ['is_empty','not_empty'] else json.dumps(f['value'],ensure_ascii=False)
            conditions.append(f"{f['field']} {OPS[f['operator']]} {right}".strip())
        if conditions:lines.append('조건: '+(' AND ' if plan['match']=='all' else ' OR ').join(conditions))
        lines.append('빈 값: '+{'ask':'처리 정책 확인 필요','exclude':'제외','keep':'유지하되 다른 조건도 적용','error':'오류로 중단'}[plan['missing_policy']])
        lines.append('중복: '+{'ask':'처리 정책 확인 필요','keep':'유지','first':'원본 첫 행 유지','last':'원본 마지막 행 유지','error':'오류로 중단'}[plan['duplicate_policy']]+(' · 기준 '+plan['duplicate_field'] if plan['duplicate_field'] else ''))
        if plan['sort']:lines.append('정렬: '+', '.join(s['field']+' '+('숫자 ' if s['value_type']=='number' else '문자 ')+('오름차순' if s['direction']=='ascending' else '내림차순') for s in plan['sort']))
        else:lines.append('정렬: 원본 순서 유지')
        lines.append('숫자 비교·정렬에서 숫자로 변환할 수 없는 값은 실패로 기록합니다.')
    for node in workflow['nodes']:
        if node['tool']=='aggregate_table':lines.append('집계: '+(', '.join(node['settings']['fields'])+'별 ' if node['settings']['fields'] else '전체 ')+(str(node['settings'].get('sum_field'))+' 합계' if node['settings'].get('aggregation')=='sum' else '건수'))
        if node['tool']=='aggregate_table' and node['settings'].get('aggregation')=='sum':lines.append('합계 열의 빈 값·논리값·비숫자·비유한 숫자는 실행 실패로 기록합니다.')
        if schedule and node['tool']=='read_table':node['settings']['file_ids']=[]
        if node['tool']=='filter_sort':
            own=node['settings'].get('execution_plan') or plan
            node['settings']['instructions']=json.dumps(own,ensure_ascii=False)
            lines.append('노드 '+node['label']+'의 실제 규칙: '+json.dumps(own,ensure_ascii=False))
        if node['tool']=='write_result':node['settings']['format']=node['settings'].get('format') or plan['output_format']
    formats=list(dict.fromkeys(n['settings']['format'] for n in workflow['nodes'] if n['tool']=='write_result'))
    if formats:lines.append('출력: '+' / '.join(f.upper() for f in formats)+' · A4B 서버 보고서 보관함')
    draft['instructions']='\n'.join(lines)
    operations=[]
    if any(n['tool']=='filter_sort' for n in workflow['nodes']):operations.append('설정된 조건으로 행을 정리')
    if any(n['tool']=='aggregate_table' for n in workflow['nodes']):operations.append('설정된 항목의 건수 또는 합계를 집계')
    draft['purpose']=('설정된 예약 시간에 ' if schedule else '요청할 때 ')+'연결한 표 데이터를 읽고 '+('하고 '.join(operations) if operations else '처리')+'하여 결과를 제공합니다.'
    # These are original request records, not model-generated claims about quotations.
    users=[m['content'] for m in messages if m['role']=='user'][-30:]
    draft['requirements']=[{'description':f'사용자 요청 {i+1}','source_id':'user','quote':text[:2000]} for i,text in enumerate(users) if text.strip()]
    return draft


# These names describe registered operations, not model-authored labels or prose.
_OPERATION_LABELS = {
    ('email.list', 'read_messages'): '메일 본문 읽기',
    ('compose_answer', 'summarize'): '근거 자료 요약',
    ('filter_sort', 'filter'): '조건에 맞는 행 선별',
    ('filter_sort', 'sort'): '행 정렬',
    ('filter_sort', 'deduplicate'): '중복 행 정리',
    ('filter_sort', 'handle_missing'): '빈 값 처리',
    ('filter_sort', 'select_columns'): '출력 열 선택',
}
_OUTPUT_FORMATS = {'csv', 'json', 'md', 'xlsx', 'pdf', 'txt'}


def _public_step(node, catalog):
    """Expose only catalogue names and small, typed executable settings."""
    tool = node.get('tool')
    registered = catalog.get(tool)
    if registered is None:
        return '지원 확인이 필요한 단계'
    settings = node.get('settings') or {}
    operation = settings.get('operation')
    label = registered['name']
    arguments = {}
    if operation:
        from workflow_engine.catalog.canonical import operation_spec, arguments_spec
        try:
            spec = operation_spec(tool, operation)
            allowed = arguments_spec(tool, spec)
        except (TypeError, ValueError):
            return label + ' (동작 확인 필요)'
        label = _OPERATION_LABELS.get((tool, operation), label)
        try:
            raw = json.loads(settings.get('arguments_json') or '{}')
        except (TypeError, ValueError):
            return label + ' (설정 확인 필요)'
        if not isinstance(raw, dict):
            return label + ' (설정 확인 필요)'
        arguments = {key: value for key, value in raw.items() if key in allowed}
    details = []
    count = arguments.get('max_results')
    if type(count) is int and 0 < count <= 1000000:
        details.append(f'최대 {count}건')
    if isinstance(arguments.get('query'), str) and arguments['query'].strip():
        # Search text may contain private addresses or model prose. Its presence
        # is useful here; the full value remains in the detailed configuration.
        details.append('검색 조건 적용')
    output_format = arguments.get('format') if operation else settings.get('format')
    if operation in ('write_csv', 'write_json'):
        output_format = operation.removeprefix('write_')
    if tool in ('write_result', 'export_result', 'create_spreadsheet') and output_format in _OUTPUT_FORMATS:
        details.append(output_format.upper())
    if not operation and tool == 'aggregate_table':
        aggregate = {'count': '건수', 'sum': '합계'}.get(settings.get('aggregation'))
        if aggregate:
            details.append(aggregate)
    if not operation and tool == 'filter_sort':
        plan = settings.get('execution_plan') or {}
        for key, name in [('filters', '선별 조건'), ('sort', '정렬 기준')]:
            items = plan.get(key)
            if isinstance(items, list) and items:
                details.append(f'{name} {len(items)}개')
    return label + (' (' + ', '.join(details) + ')' if details else '')


def _public_workflow_summary(workflow):
    from workflow_engine.catalog.registry import TOOLS
    from workflow_engine.execution_path import execution_path
    catalog = {tool['id']: tool for tool in TOOLS}
    try:
        path = execution_path(workflow)
        if not path:
            return ''
        steps = [_public_step(node, catalog) for node in path[:6]]
    except (KeyError, TypeError, ValueError):
        return ''
    result = '초안의 처리 순서: ' + ' → '.join(steps)
    if len(path) > 6:
        result += f' → 나머지 {len(path) - 6}단계'
    if path[-1].get('tool') == 'condition':
        result += '\n분기별 후속 단계는 처리 순서 화면에서 확인해 주세요.'
    elif len(path) < len(workflow.get('nodes', [])):
        result += '\n연결되지 않은 단계가 있어 처리 순서를 확인해야 합니다.'
    return result


def public_draft_explanation(draft):
    """Describe the actual draft without exposing analysis or claiming execution."""
    result = dict(draft)
    if result.get('unsupported'):
        result['explanation'] = '요청한 작업의 지원 범위를 확인해 주세요.'
        return result
    summary = _public_workflow_summary(result['workflow']) if result.get('workflow') else ''
    if result.get('questions'):
        result['explanation'] = ('아래 항목을 확인해 주세요.' + ('\n' + summary if summary else ''))
    else:
        result['explanation'] = (summary + '\n업무 조건과 처리 순서를 확인해 주세요.' if summary else
                                 '초안의 처리 순서를 확인하지 못했습니다. 업무 설정을 다시 확인해 주세요.')
    return result


def public_requirement_errors(state):
    """Keep legacy extraction diagnostics out of the customer conversation."""
    prefixes=('요청 업무 목록을 확정하지 못했습니다',
              '업무 조건을 확인하지 못했습니다. 처리할 내용과 필요한 기준을 조금 더 구체적으로')
    notice='입력하신 조건은 보존했습니다. 업무 흐름을 구성하는 중 문제가 생겼습니다. 같은 조건으로 다시 시도해주세요.'
    def public(text):
        return notice if isinstance(text,str) and text.startswith(prefixes) else text
    legacy=any(message.get('role')=='assistant' and isinstance(message.get('content'),str)
               and message['content'].startswith(prefixes) for message in state.get('messages',[]))
    return {**state,
            'messages':[{**message,'content':public(message.get('content',''))}
                        if message.get('role')=='assistant' else message
                        for message in state.get('messages',[])],
            'issues':list(dict.fromkeys(public(issue) for issue in state.get('issues',[]))),
            **({'requirement_failure':{'kind':'processing_error','retryable':True}} if legacy else {}),
            'trace':[{**step,'detail':public(step.get('detail',''))}
                     if step.get('node')=='requirements' else step
                     for step in state.get('trace',[])]}
