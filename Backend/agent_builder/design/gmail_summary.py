"""A closed, read-only Gmail summary recipe grounded in registered operation contracts.

This recognizes complete bounded requests, not a Gmail keyword. The registered
daily-09:00 previous-day and hourly unread recipes are explicit; other filters, delivery,
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
HOURLY_UNREAD_RECIPE = 'gmail_hourly_unread_summary.v1'
SAVED_SCHEDULE_RECIPE = 'gmail_saved_schedule_count_update.v1'
PREVIOUS_DAY_QUERY = '{{gmail.previous_day}}'
UNREAD_QUERY = 'is:unread -in:sent -in:drafts'
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
_HOURLY_UNREAD_REQUEST = re.compile(
    _KOREAN_TIME + r'(?:매(?:시간|시)(?:마다)?(?:정각|정시)|매정시(?:마다)?|정시마다)(?:에)?' + _KOREAN_TIME +
    r'(?:지메일|gmail)(?:에서|의|에)?'
    r'(?:'
    r'(?:안읽은|읽지않은)(?:메일|이메일)(?:들)?(?:만)?(?:의|을|를)?|'
    r'(?:온|받은|수신한|도착한)?(?:메일|이메일)(?:들)?(?:을|를)?확인하고'
    r'(?:안읽은|읽지않은)(?:(?:메일|이메일)(?:들)?|것들?|것만)(?:만)?(?:의|을|를)?|'
    r'(?:온|받은|수신한|도착한)?(?:메일|이메일)(?:들)?(?:중|가운데)'
    r'(?:안읽은|읽지않은)(?:(?:메일|이메일)(?:들)?|것들?|것만)(?:만)?(?:의|을|를)?'
    r')'
    r'(?:(?:최대)?(?P<limit>[1-9]\d{0,3})(?:개|건)(?:의|만|을|를)?)?'
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


def _scheduled_settings(text):
    if not isinstance(text, str) or len(text) > 2000:
        return None
    compact = re.sub(r'\s+', '', text.strip().rstrip('.!~'))
    for pattern, frequency, hour, query in (
        (_DAILY_REQUEST, 'daily', 9, PREVIOUS_DAY_QUERY),
        (_HOURLY_UNREAD_REQUEST, 'hourly', None, UNREAD_QUERY),
    ):
        match = pattern.fullmatch(compact)
        if match and (not match['limit'] or int(match['limit']) <= MAX_RESULTS):
            return dict(count=int(match['limit']) if match['limit'] else SCHEDULED_MAX_RESULTS,
                        schedule=ScheduleSpec(frequency=frequency, timezone='Asia/Seoul', hour=hour,
                                              minute=0, weekday=None).model_dump(), query=query)
    return None


def requested_schedule(text):
    settings = _scheduled_settings(text)
    return settings['schedule'] if settings else None


def _request_settings(text):
    count = requested_count(text)
    if count is not None:
        return dict(count=count)
    return _scheduled_settings(text)


def _count_summary_instructions(count):
    return (f'조회한 Gmail 메일 최대 {count}건의 제목·보낸 사람·본문을 근거로 '
            '메일별 핵심 내용을 요약하세요. 메일이 없으면 없다고 안내하세요.')


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
    if query not in (None, PREVIOUS_DAY_QUERY, UNREAD_QUERY) or bool(draft.get('schedule')) != (query is not None):
        return None
    instructions = summary['settings'].get('instructions')
    source = _request_settings(instructions)
    if not (source and source['count'] == arguments['max_results'] and source.get('query') == query):
        if instructions != _count_summary_instructions(arguments['max_results']):
            # Preserve custom summary work by letting the planner edit it.
            return None
    result = dict(count=arguments['max_results'])
    if query is not None:
        from workflow_engine.scheduling.recurrence import schedule_errors
        try:
            schedule = ScheduleSpec.model_validate(draft['schedule']).model_dump()
        except ValueError:
            return None
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
        settings['summary_instructions'] = _count_summary_instructions(settings['count'])
    return settings


def gmail_summary_request(state):
    if state.get('required_node_refs') or state.get('resources'):
        return None
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    if not users:
        return None
    from .request_meaning import gmail_selection, sealed_meaning
    meaning = sealed_meaning(state)
    settings = gmail_selection(state) if meaning else (_edited_request(state, users) if state.get('base_agent_id') else _request_settings(users[-1]))
    # Do not silently erase earlier independent work or a changed instruction.
    if settings is None or (not meaning and not state.get('base_agent_id') and any(_request_settings(text) != settings for text in users)):
        return None
    settings = {**settings, 'summary_instructions': settings.get('summary_instructions') or _count_summary_instructions(settings['count'])}
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


def _recipe(request):
    if request.get('semantic_selection'):
        return 'source_summary.semantic.v1'
    schedule = request.get('schedule')
    if not schedule:
        return RECIPE
    daily = ScheduleSpec(frequency='daily', timezone='Asia/Seoul', hour=9, minute=0, weekday=None).model_dump()
    hourly = ScheduleSpec(frequency='hourly', timezone='Asia/Seoul', hour=None, minute=0, weekday=None).model_dump()
    if request['query'] == PREVIOUS_DAY_QUERY and schedule == daily:
        return SCHEDULED_RECIPE
    if request['query'] == UNREAD_QUERY and schedule == hourly:
        return HOURLY_UNREAD_RECIPE
    # Only editing an existing bounded graph can supply another saved schedule.
    # Do not audit that preservation as a newly requested daily/hourly recipe.
    return SAVED_SCHEDULE_RECIPE


def gmail_summary_assessment(state):
    request = gmail_summary_request(state)
    if not request:
        return None
    return dict(decision='existing_tools', tool_ids=['email.list', 'compose_answer'],
                missing_conditions=[], missing_actions=[], user_quotes=[request['request']],
                reason='등록된 Gmail 본문 묶음 읽기의 documents 출력과 근거 요약 입력을 연결할 수 있습니다. '
                       '계정 읽기 권한과 모델 연결은 실행 전에 확인하며 설계 시 기능 부재로 취급하지 않습니다.',
                registered_workflow=_recipe(request),
                model_calls=0, allows_execution=False, allows_generation=False,
                runtime_prerequisites=['Gmail 읽기 권한 연결', '요약 모델 연결'],
                scope='catalog assessment only; not condition READY')


def _schedule_description(spec):
    zone = '한국 시간' if spec['timezone'] == 'Asia/Seoul' else spec['timezone'] + ' 기준'
    if spec['frequency'] == 'hourly':
        return f"{zone} 매시간 정각" if spec['minute'] == 0 else f"{zone} 매시간 {spec['minute']}분"
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
    elif request.get('query'):
        spec['query'] = request['query']
    scope = '안 읽은 수신 메일' if request.get('query') == UNREAD_QUERY else '전날 수신 메일'
    description = (f"{_schedule_description(scheduled)} {scope} 최대 {request['count']}건 읽기와 원문 요약"
                   if scheduled else f"최근 메일 최대 {request['count']}건 읽기와 원문 요약")
    if request.get('semantic_selection'):
        description = _semantic_scope(request, state) + f" 최대 {request['count']}건 읽기와 원문 요약"
    return dict(tasks=[dict(id='r1', source_message=request['source_message'], quote=request['request'],
                           description=description, depends_on=[], spec=spec)],
                unresolved=[])


def _semantic_scope(request, state):
    selection = request['semantic_selection']
    labels = {'unread':'안 읽은', 'important':'중요 표시된', 'starred':'별표 표시된'}
    scope = ' '.join([*(label for key, label in labels.items() if key in selection['filters']), '메일'])
    if request.get('schedule'):
        return _schedule_description(request['schedule']) + (' 전날 ' if selection['period'] == 'yesterday' else ' ') + scope
    if selection['period'] in ('today', 'yesterday'):
        anchor = state['temporal_context']['user_message_anchors'][selection['period_source_message']]
        date = anchor['relative_days'][selection['period']]['date']
        return f"{anchor['timezone']} 기준 {date}에 받은 {scope}"
    return '최근 ' + scope


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
    unread = request.get('query') == UNREAD_QUERY
    recipe = _recipe(request)
    explanation = ((f'{_schedule_description(scheduled)}에 안 읽은 수신 메일을 최대 {count}건 읽습니다. '
                    '보낸 메일과 임시보관함은 제외합니다. 읽음 상태를 바꾸지 않아 계속 안 읽은 메일은 다음 실행에도 포함될 수 있습니다. '
                    '조회 한도를 넘으면 일부 메일은 포함되지 않을 수 있습니다. '
                    '요약은 앱의 실행 기록에 저장하며 예약은 저장 후 별도로 켭니다. ')
                   if unread and scheduled else
                   (f'{_schedule_description(scheduled)}에 전날 00:00부터 당일 00:00 직전까지 받은 메일 중 최대 {count}건을 읽습니다. '
                    '조회 기간은 매번 예약 실행일을 기준으로 계산합니다. 조회 한도를 넘으면 일부 메일은 포함되지 않을 수 있습니다. '
                    '요약은 앱의 실행 기록에 저장하며 예약은 저장 후 별도로 켭니다. ')
                   if scheduled else f'최근 메일을 최대 {count}건 읽고 ')
    if request.get('semantic_selection'):
        explanation = _semantic_scope(request, state) + f'을 최대 {count}건 읽도록 구성했습니다. '
        if request.get('query'):
            explanation += '조회 한도를 넘으면 일부 메일은 포함되지 않을 수 있습니다. '
        if scheduled:
            explanation += '조회 기간은 매번 실행일 기준으로 계산하며, 예약은 저장 후 별도로 켭니다. '
    explanation += ('제목·보낸 사람·본문의 핵심 내용을 메일별로 요약합니다. '
                   '메일이 적으면 실제 조회된 메일만 정리하고, 내용이 없는 경우에는 없다고 안내합니다. '
                   '실행할 때 서비스 연결의 Gmail 읽기 권한과 AI 사용 가능 상태를 확인합니다.')
    defaults = NodeSettings(file_ids=[], query=None, fields=[], instructions=None, format=None,
                            question=None, condition_field=None, condition_operator=None).model_dump()
    arguments = dict(max_results=count)
    if request.get('query'):
        arguments['query'] = request['query']
    nodes = [
        dict(id='mail', tool='email.list', label=f'안 읽은 메일 최대 {count}건 읽기' if unread else f'전날 메일 최대 {count}건 읽기' if scheduled else f'최근 메일 {count}건 읽기', bindings=[],
             settings={**defaults, 'operation':'read_messages', 'arguments_json':json.dumps(arguments)}),
        dict(id='summary', tool='compose_answer', label='메일 내용 요약',
             bindings=[dict(input='documents', node_id='mail', output='documents')],
             settings={**defaults, 'instructions':request.get('summary_instructions') or quote}),
    ]
    if request.get('semantic_selection') and (request.get('query') or scheduled):
        nodes[0]['label'] = _semantic_scope(request, state) + f' 최대 {count}건 읽기'
    name = ((state.get('draft') or {}).get('name') if state.get('base_agent_id') else None)
    default_name = ('매시간 정각 안 읽은 메일 요약' if recipe == HOURLY_UNREAD_RECIPE else
                    '매일 오전 9시 전날 메일 요약' if recipe == SCHEDULED_RECIPE else
                    f"{_schedule_description(scheduled)} {'안 읽은' if unread else '전날'} 메일 요약" if scheduled else
                    f'최근 메일 {count}건 요약')
    if request.get('semantic_selection'):
        default_name = _semantic_scope(request, state) + f' {count}건 요약'
    draft = AgentDraft(name=name or default_name, purpose=quote, instructions=explanation,
                       tool_ids=['email.list', 'compose_answer'], document_ids=[],
                       requirements=[dict(description='안 읽은 메일 예약 조회와 요약' if unread else '전날 메일 예약 조회와 요약' if scheduled else '최근 메일 본문 조회와 요약', source_id='user', quote=quote)],
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
                    detail=plan['tasks'][0]['description'] + ' · 등록 문서 입출력 연결 확인',
                    registered_workflow=recipe, model_calls=0)])
