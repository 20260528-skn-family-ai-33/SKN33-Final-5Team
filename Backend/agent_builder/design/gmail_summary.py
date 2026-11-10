"""A closed, read-only Gmail summary recipe grounded in registered operation contracts.

This recognizes complete bounded requests, not a Gmail keyword. The registered
daily-09:00 previous-day recipe is explicit; other recurrence, filters, delivery,
other channels, attachments and additional tasks stay with the planner.
Account authorization remains an execution prerequisite and is never inferred here.
"""
import json
import re

from contracts.models import AgentDraft, NodeSettings, ScheduleSpec


_REQUEST = re.compile(
    r'(?:지메일|gmail)(?:에서|의)?(?:최근|최신)([1-9]\d{0,3})(?:개|건)(?:의)?'
    r'(?:메일|이메일)(?:의|을|를)?(?:내용|본문)?(?:을|를)?'
    r'요약(?:해서정리|하고정리|정리)?해(?:줘|주세요|주라)', re.I)
RECIPE = 'gmail_recent_summary.v1'
SCHEDULED_RECIPE = 'gmail_previous_day_daily_summary.v1'
PREVIOUS_DAY_QUERY = '{{gmail.previous_day}}'
SCHEDULED_MAX_RESULTS = 100
MAX_RESULTS = 500
_KOREAN_TIME = r'(?:(?:한국시간|KST|Asia/Seoul)(?:기준)?(?:으로)?)?'
_PREVIOUS_DAY = r'(?:그전날|전날|어제)(?:에)?(?:온|받은|수신한|도착한)?'
_GMAIL = r'(?:지메일|gmail)(?:에서|의)?'
_DAILY_REQUEST = re.compile(
    _KOREAN_TIME + r'매일오전9시(?:00분|정각)?(?:에)?' + _KOREAN_TIME +
    r'(?:' + _PREVIOUS_DAY + _GMAIL + r'(?:메일|이메일)|' +
    _GMAIL + _PREVIOUS_DAY + r'(?:메일|이메일))'
    r'(?:들)?(?:의|을|를)?(?:(?:중|가운데)?(?:최대)?(?P<limit>[1-9]\d{0,3})(?:개|건)(?:의|만|을|를)?)?'
    r'(?:내용|본문)?(?:을|를)?'
    r'요약(?:해서정리|하고정리|정리)?(?:해(?:줘|주세요|주라))?', re.I)
_COUNT_CHANGE = re.compile(r'(?:(?:조회|요약)(?:할)?(?:메일)?(?:수|건수|한도)(?:를|는|을)?|메일(?:조회)?(?:한도|건수)(?:를|는|을)?)?'
                           r'(?:최대)?([1-9]\d{0,3})(?:개|건)(?:으로|로)(?:바꿔|변경해|설정해)(?:줘|주세요)', re.I)


def requested_count(text):
    if not isinstance(text,str) or len(text)>2000:
        return None
    compact = re.sub(r'\s+', '', text.strip().rstrip('.!~'))
    match = _REQUEST.fullmatch(compact)
    return int(match[1]) if match and int(match[1]) <= MAX_RESULTS else None


def requested_schedule(text):
    if not isinstance(text, str) or len(text) > 2000:
        return None
    compact = re.sub(r'\s+', '', text.strip().rstrip('.!~'))
    match = _DAILY_REQUEST.fullmatch(compact)
    if not match or match['limit'] and int(match['limit']) > MAX_RESULTS:
        return None
    return ScheduleSpec(frequency='daily', timezone='Asia/Seoul', hour=9, minute=0, weekday=None).model_dump()


def _request_settings(text):
    count = requested_count(text)
    if count is not None:
        return dict(count=count)
    schedule = requested_schedule(text)
    if schedule:
        match = _DAILY_REQUEST.fullmatch(re.sub(r'\s+', '', text.strip().rstrip('.!~')))
        return dict(count=int(match['limit']) if match['limit'] else SCHEDULED_MAX_RESULTS,
                    schedule=schedule, query=PREVIOUS_DAY_QUERY)
    return None


def _base_summary(state):
    """Count-only edits may preserve a saved, file-free read -> summary graph."""
    draft = state.get('draft') or {}
    workflow = draft.get('workflow') or {}
    nodes = workflow.get('nodes', [])
    if draft.get('document_ids') or len(nodes) != 2 or set(draft.get('tool_ids', [])) != {'email.list', 'compose_answer'}:
        return None
    reader = next((n for n in nodes if n.get('tool') == 'email.list'), None)
    summary = next((n for n in nodes if n.get('tool') == 'compose_answer'), None)
    if not reader or not summary or reader.get('bindings') or reader.get('settings', {}).get('operation') != 'read_messages':
        return None
    if summary.get('settings', {}).get('operation') or summary.get('bindings') != [dict(input='documents', node_id=reader['id'], output='documents')]:
        return None
    if workflow.get('entry') != reader['id'] or workflow.get('edges') != [dict(source=reader['id'], target=summary['id'], branch='next')]:
        return None
    try:
        arguments = json.loads(reader['settings'].get('arguments_json') or '{}')
    except (ValueError, TypeError):
        return None
    if (not isinstance(arguments, dict) or set(arguments) - {'query', 'max_results'}
            or type(arguments.get('max_results')) is not int or not 1 <= arguments['max_results'] <= MAX_RESULTS):
        return None
    query = arguments.get('query')
    if query not in (None, PREVIOUS_DAY_QUERY) or bool(draft.get('schedule')) != (query == PREVIOUS_DAY_QUERY):
        return None
    result = dict(count=arguments['max_results'])
    if query == PREVIOUS_DAY_QUERY:
        from workflow_engine.scheduling.recurrence import schedule_errors
        schedule = ScheduleSpec.model_validate(draft['schedule']).model_dump()
        if schedule_errors(schedule):return None
        result.update(schedule=schedule, query=query)
    return result


def _edited_request(state, users):
    settings = _base_summary(state)
    if not settings:
        return None
    changed = False
    for text in users:
        complete = _request_settings(text)
        if complete:
            settings = complete
            continue
        compact = re.sub(r'\s+', '', text.strip().rstrip('.!~'))
        match = _COUNT_CHANGE.fullmatch(compact)
        if not match or int(match[1]) > MAX_RESULTS:
            return None
        settings = {**settings, 'count': int(match[1])}
        changed = True
    if changed:
        settings['summary_instructions'] = (f"조회한 Gmail 메일 최대 {settings['count']}건의 제목·보낸 사람·본문을 근거로 "
                                            '메일별 핵심 내용을 요약하세요. 메일이 없으면 없다고 안내하세요.')
    return settings


def gmail_summary_request(state):
    if state.get('required_node_refs') or state.get('resources'):
        return None
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    if not users:
        return None
    settings = _edited_request(state, users) if state.get('base_agent_id') else _request_settings(users[-1])
    # Do not silently erase earlier independent work or a changed instruction.
    if settings is None or (not state.get('base_agent_id') and any(_request_settings(text) != settings for text in users)):
        return None
    from workflow_engine.catalog.canonical import operation_spec, arguments_spec, ports
    from workflow_engine.catalog.registry import TOOLS
    try:
        spec = operation_spec('email.list', 'read_messages')
        args = arguments_spec('email.list', spec)
        _, outputs = ports('email.list', spec)
    except ValueError:
        return None
    summary = next((tool for tool in TOOLS if tool['id'] == 'compose_answer'), {})
    if (set(args) != {'query', 'max_results'} or any(args.values())
            or outputs.get('documents') != 'documents'
            or summary.get('inputs', {}).get('documents') != 'documents'):
        return None
    return dict(request=users[-1], source_message=len(users)-1, **settings)


def gmail_summary_assessment(state):
    request = gmail_summary_request(state)
    if not request:
        return None
    return dict(decision='existing_tools', tool_ids=['email.list', 'compose_answer'],
                missing_conditions=[], missing_actions=[], user_quotes=[request['request']],
                reason='등록된 Gmail 본문 묶음 읽기의 documents 출력과 근거 요약 입력을 연결할 수 있습니다. '
                       '계정 읽기 권한과 모델 연결은 실행 전에 확인하며 설계 시 기능 부재로 취급하지 않습니다.',
                registered_workflow=SCHEDULED_RECIPE if request.get('schedule') else RECIPE,
                model_calls=0, allows_execution=False, allows_generation=False,
                runtime_prerequisites=['Gmail 읽기 권한 연결', '요약 모델 연결'],
                scope='catalog assessment only; not condition READY')


def _schedule_description(spec):
    zone = '한국 시간' if spec['timezone'] == 'Asia/Seoul' else spec['timezone'] + ' 기준'
    if spec['frequency'] == 'hourly':
        return f"{zone} 매시간 {spec['minute']}분"
    clock = f"{spec['hour']:02d}:{spec['minute']:02d}"
    if spec['frequency'] == 'daily':
        return f'{zone} 매일 {clock}'
    if spec['frequency'] == 'weekly':
        weekday = ['월', '화', '수', '목', '금', '토', '일'][spec['weekday']]
        return f'{zone} 매주 {weekday}요일 {clock}'
    day = '말일' if spec.get('month_end') else str(spec['monthday']) + '일'
    return f'{zone} 매월 {day} {clock}'


def gmail_summary_plan(state):
    request = gmail_summary_request(state)
    if not request:
        return None
    scheduled = request.get('schedule')
    spec = dict(kind='gmail_scheduled_summary' if scheduled else 'gmail_summary',
                max_results=request['count'], request=request['request'])
    if request.get('summary_instructions'):
        spec['summary_instructions'] = request['summary_instructions']
    if scheduled:
        spec.update(schedule=scheduled, query=request['query'])
    description = (f"{_schedule_description(scheduled)} 전날 수신 메일 최대 {request['count']}건 읽기와 원문 요약"
                   if scheduled else f"최근 메일 최대 {request['count']}건 읽기와 원문 요약")
    return dict(tasks=[dict(id='r1', source_message=request['source_message'], quote=request['request'],
                           description=description, depends_on=[], spec=spec)],
                unresolved=[])


def gmail_summary_reply(state):
    request = gmail_summary_request(state)
    if not request:
        return None
    from agent_builder.conditions.condition_readiness import input_issues
    if input_issues(state):
        return None
    from agent_builder.capabilities.capability_gate import input_fingerprint
    from agent_builder.design.requirement_ledger import validate_plan, ledger_fingerprint, plan_digest
    count, quote = request['count'], request['request']
    scheduled = request.get('schedule')
    explanation = ((f'{_schedule_description(scheduled)}에 전날 00:00부터 당일 00:00 직전까지 받은 메일 중 최대 {count}건을 읽습니다. '
                    '조회 기간은 매번 예약 실행일을 기준으로 계산합니다. 조회 한도를 넘으면 일부 메일은 포함되지 않을 수 있습니다. '
                    '요약은 앱의 실행 기록에 저장하며 예약은 저장 후 별도로 켭니다. ')
                   if scheduled else f'최근 메일을 최대 {count}건 읽고 ')
    explanation += ('제목·보낸 사람·본문의 핵심 내용을 메일별로 요약합니다. '
                   '메일이 적으면 실제 조회된 메일만 정리하고, 내용이 없는 경우에는 없다고 안내합니다. '
                   '실행할 때 서비스 연결의 Gmail 읽기 권한과 AI 사용 가능 상태를 확인합니다.')
    defaults = NodeSettings(file_ids=[], query=None, fields=[], instructions=None, format=None,
                            question=None, condition_field=None, condition_operator=None).model_dump()
    arguments = dict(max_results=count)
    if scheduled:
        arguments['query'] = request['query']
    nodes = [
        dict(id='mail', tool='email.list', label=f'전날 메일 최대 {count}건 읽기' if scheduled else f'최근 메일 {count}건 읽기', bindings=[],
             settings={**defaults, 'operation':'read_messages', 'arguments_json':json.dumps(arguments)}),
        dict(id='summary', tool='compose_answer', label='메일 내용 요약',
             bindings=[dict(input='documents', node_id='mail', output='documents')],
             settings={**defaults, 'instructions':request.get('summary_instructions') or quote}),
    ]
    name = ((state.get('draft') or {}).get('name') if state.get('base_agent_id') else None)
    draft = AgentDraft(name=name or ('매일 오전 9시 전날 메일 요약' if scheduled else f'최근 메일 {count}건 요약'), purpose=quote, instructions=explanation,
                       tool_ids=['email.list', 'compose_answer'], document_ids=[],
                       requirements=[dict(description='전날 메일 예약 조회와 요약' if scheduled else '최근 메일 본문 조회와 요약', source_id='user', quote=quote)],
                       questions=[], unsupported=[], explanation=explanation,
                       execution_plan=dict(filters=[], sort=[], match='all', missing_policy='keep',
                                           duplicate_policy='keep', duplicate_field=None, output_format='json'),
                       workflow=dict(entry='mail', nodes=nodes,
                                     edges=[dict(source='mail', target='summary', branch='next')]),
                       schedule=scheduled, report_destination='reports' if scheduled else None).model_dump()
    plan = validate_plan(gmail_summary_plan(state), state)
    return dict(draft=draft, confirmed=False, saved_agent_id=None, pending_questions=[],
                issues=[], semantic_issues=[], condition_enforced=False, condition_required=False,
                condition_template=False, condition_ledger=None,
                capability_enforced=True, capability_assessment=gmail_summary_assessment(state),
                capability_fingerprint=input_fingerprint(state), capability_questions=[], capability_deferred=False,
                requirement_required=True, requirement_ledger=plan, requirement_fingerprint=ledger_fingerprint(state),
                requirement_digest=plan_digest(plan), gap_condition_review=None,
                messages=state['messages']+[dict(role='assistant', content=explanation)],
                trace=state.get('trace', [])+[dict(node='gmail_summary', label='메일 요약 설계', status='completed',
                    detail='요청 전체·조회 건수·등록 문서 입출력을 확인해 읽기와 요약을 연결했습니다.', model_calls=0)])
