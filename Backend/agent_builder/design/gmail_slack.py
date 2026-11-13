"""A bounded Gmail summary followed by a connected Slack message.

The destination is a user supplied Slack conversation ID.  Connection secrets
remain with the server, and an absent destination is always a clarification.
"""
import json
import re

from contracts.models import AgentDraft, NodeSettings, ScheduleSpec

from .gmail_summary import MAX_RESULTS, PREVIOUS_DAY_QUERY, UNREAD_QUERY


CHANNEL = re.compile(r'(?<![A-Z0-9])([CGD][A-Z0-9]{8,24})(?![A-Z0-9])')
_DAY = re.compile(r'어제|전날|오늘|최근|최신')
_RECENT_DAYS = re.compile(r'최근\s*([1-9]\d{0,2})\s*일(?:간|동안)?')
_UNREAD = re.compile(r'안\s*읽은|읽지\s*않은', re.I)
_UNREAD_EXCLUSION = re.compile(r'(?:안\s*읽은|읽지\s*않은).{0,15}(?:제외|빼고|말고|아닌)', re.I)
_COUNT = re.compile(r'(?:최대\s*)?([1-9]\d{0,2})\s*(?:건|개|통)')
_CLOCK = r'(?:(오전|오후)\s*)?(\d{1,2})(?:\s*시(?:\s*(\d{1,2})\s*분)?|:(\d{2}))'
_DAILY = re.compile(r'매일\s*' + _CLOCK)
_DAILY_FOLLOWUP = re.compile(r'매일\s*' + _CLOCK + r'(?:에\s*실행(?:해\s*줘|해\s*주세요)?)?')
_TIME = re.compile(_CLOCK)
_OTHER_WORK = re.compile(r'구글\s*시트|스프레드시트|캘린더|첨부|번역|표로|엑셀|웹훅|카카오|텔레그램|문서\s*파일|그리고\s*(?:또|추가)|중요\s*표시|별표\s*표시|지난\s*주|이번\s*주|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}', re.I)
_QUESTION = '요약을 보낼 Slack 채널 ID를 알려주세요. (예: C0123456789)'
_PERIOD_QUESTION = '어느 기간에 받은 Gmail 메일을 요약할까요? (예: 어제, 오늘, 최근)'
_TIME_QUESTION = '매일 몇 시에 실행할까요? (한국 시간, 예: 오전 9시)'
SUMMARY_CARD_FORMAT = (
    '\n메일이 있으면 메일마다 아래 세 필드를 순서대로 한 번씩 쓰고, 메일 사이는 빈 줄로 구분하세요. '
    '필드명은 바꾸지 말고 Markdown 표·목록이나 앞뒤 설명을 추가하지 마세요.\n'
    '제목: 실제 메일의 제목\n보낸 사람: 실제 메일의 발신자\n'
    '핵심: 원문에서 확인된 중요한 사실·기한·필요한 조치를 1~2문장으로 요약\n'
    '콜론 오른쪽에는 실제 메일에서 확인한 값을 적으세요. '
    '메일 본문 전체나 홍보 문구를 옮기지 말고 긴 URL을 복사하지 마세요. '
    '원문에 없는 메일·날짜·조치·사실을 만들지 마세요.'
)


def _users(state):
    return [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']


def _followup_values(content):
    """Read the values in the exact clarification labels emitted by our UI.

    The labels contain example dates and a sample Slack ID.  They must never
    be treated as the user's selected period or destination.
    """
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    values = []
    for line in lines:
        if '→' not in line:
            if len(lines) > 1:
                return None
            values.append((None, line))
            continue
        label, value = (part.strip() for part in line.split('→', 1))
        matched = _question_kind(label)
        if matched is None or not value:
            return None
        values.append((matched, value))
    return values or None


def _question_kind(label):
    return next((i for i, question in enumerate((_PERIOD_QUESTION, _TIME_QUESTION, _QUESTION))
                 if label.strip() in (question, question + '에 대해 알려주세요.')), None)


def _structured_values(state, message_index, content):
    for event in reversed(state.get('capability_answers', [])):
        if (event.get('turn') != message_index or event.get('answer') != content
                or not isinstance(event.get('answers'), list)):
            continue
        questions = {question.get('id'): _question_kind(question.get('text', ''))
                     for question in event.get('questions', []) if isinstance(question, dict)}
        values = []
        for answer in event['answers']:
            if not isinstance(answer, dict):
                return None
            kind = questions.get(answer.get('question_id'))
            value = answer.get('value')
            if kind is None or not isinstance(value, str) or not value.strip():
                return None
            values.append((kind, value.strip()))
        return values or None
    return None


def _request_values(state, users):
    values = [(0, None, users[0])]
    message_indices = [i for i, message in enumerate(state.get('messages', []))
                       if message.get('role') == 'user']
    for index, content in enumerate(users[1:], 1):
        followup = _structured_values(state, message_indices[index], content) or _followup_values(content)
        if followup is None:
            return None
        values.extend((index, question, value) for question, value in followup)
    return values


def _period_label(request):
    if request['recent_days']:
        return f"최근 {request['recent_days']}일"
    return {'yesterday': '어제', 'today': '오늘', 'recent': '최근'}[request['period']]


def gmail_slack_candidate(state):
    if state.get('resources') or state.get('required_node_refs') or state.get('base_agent_id'):
        return False
    users = _users(state)
    if not users:
        return False
    first = users[0]
    if (len(first) > 1200 or _OTHER_WORK.search(first) or _UNREAD_EXCLUSION.search(first)
            or not re.search(r'Gmail|지메일', first, re.I)
            or '요약' not in first
            or not re.search(r'Slack|슬랙', first, re.I)
            or not re.search(r'보내|전송|전달|올려|알려', first)):
        return False
    from agent_builder.capabilities.capability_judgement import delivery_directives
    directive = delivery_directives(state).get('slack')
    if directive and directive['intent'] == 'cancel_delivery':
        return False
    # Follow-up turns can supply only the requested values. A new instruction
    # must go through the full planner, even when it contains a valid date.
    values = _request_values(state, users)
    if values is None:
        return False
    for _, question, followup in values[1:]:
        compact = re.sub(r'\s+', '', followup).rstrip('.!?')
        period = bool(re.fullmatch(r'(?:어제|전날|오늘|최근|최신|최근[1-9]\d{0,2}일(?:간|동안)?)(?:받은|온)?(?:메일)?(?:로|으로)?(?:해줘)?', compact))
        count = bool(re.fullmatch(r'(?:최대)?[1-9]\d{0,2}(?:건|개|통)(?:으로|로)?(?:바꿔줘|변경해줘|해줘)?', compact))
        clock = bool(_TIME.fullmatch(re.sub(r'[.!?]', '', followup).strip()))
        channel = bool(CHANNEL.fullmatch(compact))
        daily = bool(_DAILY_FOLLOWUP.fullmatch(re.sub(r'[.!?]', '', followup).strip()))
        if not ((question is None and (period or count or clock or channel or daily)) or
                (question == 0 and period) or (question == 1 and (clock or daily)) or
                (question == 2 and channel)):
            return False
    return True


def gmail_slack_request(state):
    if not gmail_slack_candidate(state):
        return None
    users = _users(state)
    first = users[0]
    values = _request_values(state, users)
    period_value = next(((index, text) for index, _, text in reversed(values) if _DAY.search(text)), None)
    period_source, period_text = period_value if period_value else (None, '')
    period_match = _DAY.search(period_text)
    recent_days_match = _RECENT_DAYS.search(period_text) if period_match and period_match[0] == '최근' else None
    recent_days = int(recent_days_match[1]) if recent_days_match else None
    unread = bool(_UNREAD.search(first))
    period = ('recent' if unread and not period_match else None if not period_match else
              'yesterday' if period_match[0] in ('어제', '전날') else
              'today' if period_match[0] == '오늘' else 'recent')
    counts = [match for _, _, text in values for match in _COUNT.finditer(text)]
    count = int(counts[-1][1]) if counts else 100
    if count > MAX_RESULTS:
        return None
    ids = [match[1] for _, _, text in values for match in CHANNEL.finditer(text)]
    channel_id = ids[-1] if ids else None
    if len(set(ids)) > 1:
        return None
    scheduled = any('매일' in text for _, _, text in values)
    if scheduled and unread and period == 'yesterday':
        return None  # The dynamic previous-day query cannot combine with an unread filter.
    clock = next((match for _, _, text in reversed(values) if (match := _DAILY.search(text)) or
                  (scheduled and (match := _TIME.fullmatch(text.strip().rstrip('.!?'))))), None)
    schedule = None
    if clock:
        hour, minute = int(clock[2]), int(clock[3] or clock[4] or 0)
        if clock[1] == '오후' and hour < 12:
            hour += 12
        if clock[1] == '오전' and hour == 12:
            hour = 0
        try:
            schedule = ScheduleSpec(frequency='daily', timezone='Asia/Seoul', hour=hour,
                                    minute=minute, weekday=None).model_dump()
        except ValueError:
            return None
    missing = []
    if period is None:
        missing.append(_PERIOD_QUESTION)
    if scheduled and schedule is None:
        missing.append(_TIME_QUESTION)
    if channel_id is None:
        missing.append(_QUESTION)
    query = None
    if period == 'yesterday':
        if schedule:
            query = PREVIOUS_DAY_QUERY
        else:
            from .temporal_context import selected_day, temporal_context
            clock_state = {**state, 'temporal_context': temporal_context(state)}
            day = selected_day(clock_state, {'period': 'yesterday', 'period_source_message': period_source})
            if day:
                query = f"after:{day['start_epoch']} before:{day['end_epoch']} -in:sent -in:drafts"
            else:
                missing.append('어제의 날짜 기준 시간대를 알려주세요.')
    elif period == 'today':
        if schedule:
            return None  # The registered dynamic date query is previous-day only.
        from .temporal_context import selected_day, temporal_context
        clock_state = {**state, 'temporal_context': temporal_context(state)}
        day = selected_day(clock_state, {'period': 'today', 'period_source_message': period_source})
        if day:
            query = f"after:{day['start_epoch']} before:{day['end_epoch']} -in:sent -in:drafts"
        else:
            missing.append('오늘의 날짜 기준 시간대를 알려주세요.')
    if recent_days:
        query = f'newer_than:{recent_days}d -in:sent -in:drafts'
    if unread:
        query = f'is:unread {query}' if query else UNREAD_QUERY
    return dict(request=first, source_message=0, count=count, period=period, recent_days=recent_days,
                query=query, schedule=schedule, scheduled=scheduled, unread=unread,
                channel_id=channel_id, missing_conditions=missing,
                summary_instructions=(f'조회한 {"안 읽은 " if unread else ""}Gmail 메일 최대 {count}건을 사용자 요청에 맞춰 요약하세요: {first} '
                                      '메일이 없으면 없다고 안내하세요. Slack 전송은 다음 단계가 처리하므로 요약 텍스트만 작성하세요.'
                                      + SUMMARY_CARD_FORMAT))


def gmail_slack_assessment(state):
    request = gmail_slack_request(state)
    if request is None:
        return None
    from workflow_engine.catalog.registry import TOOLS
    ids = ['email.list', 'compose_answer', 'slack.post_message']
    if not set(ids) <= {tool['id'] for tool in TOOLS}:
        return None
    from workflow_engine.catalog.canonical import operation_spec, arguments_spec, ports
    try:
        reader=operation_spec('email.list','read_messages')
        send=operation_spec('slack.post_message','execute')
        _,read_outputs=ports('email.list',reader)
        send_inputs,_=ports('slack.post_message',send)
        summary_outputs=next(tool for tool in TOOLS if tool['id']=='compose_answer')['output_ports']
        if (not send.get('code_implemented') or set(arguments_spec('slack.post_message',send)) != {'channel_id','text'}
                or read_outputs.get('documents') != 'documents' or send_inputs.get('text') != 'text'
                or summary_outputs.get('text') != 'text'):
            return None
    except (ValueError,KeyError):
        return None
    missing = request['missing_conditions']
    return dict(decision='clarify' if missing else 'existing_tools', tool_ids=ids,
                missing_conditions=missing, missing_actions=[], user_quotes=[request['request']],
                reason='Gmail 본문 조회와 요약 텍스트의 Slack 전송은 등록 도구로 구성할 수 있습니다. '
                       '요청에 지정된 수량·기간·읽음 상태를 조회 설정에 반영하고 대상 채널을 확인합니다.',
                model_calls=0, allows_execution=False, allows_generation=False,
                runtime_prerequisites=['Gmail 읽기 계정 연결', 'Slack 계정 연결', '요약 모델 연결'])


def gmail_slack_plan(state):
    request = gmail_slack_request(state)
    if not request or request['missing_conditions']:
        return None
    spec = dict(kind='gmail_slack_summary', max_results=request['count'],
                period=request['period'], query=request['query'],
                channel_id=request['channel_id'], schedule=request['schedule'],
                summary_instructions=request['summary_instructions'])
    period_label = _period_label(request)
    scope = '안 읽은 Gmail 메일' if request['unread'] else 'Gmail 메일'
    description = f"{period_label} {scope} 최대 {request['count']}건 요약 후 Slack {request['channel_id']} 전송"
    return dict(tasks=[dict(id='r1', source_message=0, quote=request['request'],
                            description=description, depends_on=[], spec=spec)], unresolved=[])


def gmail_slack_reply(state):
    request = gmail_slack_request(state)
    if not request or request['missing_conditions']:
        return None
    from agent_builder.conditions.condition_readiness import input_issues
    if input_issues(state):
        return None
    from agent_builder.capabilities.capability_gate import input_fingerprint
    from .requirement_ledger import validate_plan, ledger_fingerprint, plan_digest
    count = request['count']
    arguments = {'max_results': count}
    if request['query']:
        arguments['query'] = request['query']
    defaults = NodeSettings(file_ids=[], query=None, fields=[], instructions=None, format=None,
                            question=None, condition_field=None, condition_operator=None).model_dump()
    nodes = [
        dict(id='mail', tool='email.list', label=f'{"안 읽은 " if request["unread"] else ""}Gmail 메일 최대 {count}건 읽기', bindings=[],
             settings={**defaults, 'operation': 'read_messages', 'arguments_json': json.dumps(arguments)}),
        dict(id='summary', tool='compose_answer', label='메일 내용 요약',
             bindings=[dict(input='documents', node_id='mail', output='documents')],
             settings={**defaults, 'instructions': request['summary_instructions']}),
        dict(id='slack', tool='slack.post_message', label='Slack 채널에 요약 보내기',
             bindings=[dict(input='text', node_id='summary', output='text')],
             settings={**defaults, 'operation': 'execute',
                       'arguments_json': json.dumps({'channel_id': request['channel_id']})}),
    ]
    schedule = request['schedule']
    period_label = _period_label(request)
    timing = f"매일 {schedule['hour']:02d}:{schedule['minute']:02d} 한국 시간에 " if schedule else ''
    source_label = (f'{period_label} 동안 받은 {"안 읽은 " if request["unread"] else ""}Gmail 메일' if request['recent_days']
                    else f'{period_label} 받은 안 읽은 Gmail 메일' if request['unread'] and request['period'] != 'recent'
                    else '최근 안 읽은 Gmail 메일' if request['unread'] else f'{period_label} 받은 Gmail 메일')
    connection_note = ('실행 시 연결된 Gmail 읽기 계정과 Slack 계정을 사용합니다.'
                       if state.get('gmail_connected') is True and state.get('slack_connected') is True
                       else '실행에는 Gmail 읽기 계정과 Slack 계정 연결이 필요합니다.')
    explanation = (f"{timing}{source_label}을 최대 {count}건 읽어 제목·보낸 사람·본문의 핵심을 요약하고 "
                   f"Slack 채널 {request['channel_id']}에 보냅니다. 조회 한도를 넘는 메일은 포함되지 않을 수 있습니다. "
                   f'{connection_note}')
    draft = AgentDraft(request_input_mode='saved_instructions',
                       name=f'{period_label} {"안 읽은 " if request["unread"] else ""}Gmail 메일 요약 Slack 전송', purpose=request['request'],
                       instructions=explanation, tool_ids=['email.list', 'compose_answer', 'slack.post_message'],
                       document_ids=[], requirements=[dict(description='Gmail 메일 요약을 지정 Slack 채널에 전송',
                                                            source_id='user', quote=request['request'])],
                       questions=[], unsupported=[], explanation=explanation,
                       execution_plan=dict(filters=[], sort=[], match='all', missing_policy='keep',
                                           duplicate_policy='keep', duplicate_field=None, output_format='json'),
                       workflow=dict(entry='mail', nodes=nodes,
                                     edges=[dict(source='mail', target='summary', branch='next'),
                                            dict(source='summary', target='slack', branch='next')]),
                       schedule=schedule, report_destination='reports' if schedule else None).model_dump()
    plan = validate_plan(gmail_slack_plan(state), state)
    return dict(draft=draft, confirmed=False, saved_agent_id=None, pending_questions=[],
                issues=[], semantic_issues=[], condition_enforced=False, condition_required=False,
                condition_template=False, condition_ledger=None,
                capability_enforced=True, capability_assessment=gmail_slack_assessment(state),
                capability_fingerprint=input_fingerprint(state), capability_questions=[], capability_deferred=False,
                requirement_required=True, requirement_ledger=plan, requirement_fingerprint=ledger_fingerprint(state),
                requirement_digest=plan_digest(plan), gap_condition_review=None,
                messages=state['messages'] + [dict(role='assistant', content=explanation)],
                trace=state.get('trace', []) + [dict(node='gmail_slack', label='Gmail 요약 Slack 전송 설계',
                    status='completed', detail=plan['tasks'][0]['description'], model_calls=0)])
