"""Infer an optional workflow trigger from the request; keep business times separate."""
import re


_RECURRENCE = re.compile(r'매\s*시간|매\s*정시|정시\s*마다|매\s*시(?![가-힣])|(?:한|1)\s*시간마다|매일|날마다|하루\s*한\s*번|매주|주마다|[월화수목금토일]요일\s*마다|매\s*[월화수목금토일]요일|매월|매달|월마다|매년|연간\s*(?:예약|실행|반복)|hourly|daily|weekly|monthly|yearly|cron|크론',re.I)
_CANCEL = re.compile(r'(?:예약|자동\s*실행|반복\s*실행)(?:은|는|을|를)?\s*(?:취소|해제|삭제|빼|없이|하지\s*마|안\s*해|추가하지\s*않|필요\s*없)|(?:직접|수동으로?)\s*실행(?:만|할|해|하|으로)?|한\s*번만\s*(?:실행|처리)|필요할\s*때만',re.I)
_CANCEL_NEGATED = re.compile(r'\s*(?:하지\s*마|하지\s*말|안\s*해|않|금지)')
_REQUEST_SCHEDULE = re.compile(r'(?:예약|정기적(?:으로)?|자동으로|반복해서)\s*(?:실행|돌려|처리|해줘|해주세요|해\s*줘)|예약\s*(?:추가|설정)|(?:실행|처리).{0,5}예약|자동\s*실행|반복\s*실행|정기적으로|주기적으로|정해진\s*(?:시간|시각)|일정한\s*(?:주기|간격)',re.I)


def _business_recurrence(text):
    # A recurring calendar event or a property of source rows is task data,
    # whereas a recurring instruction to run this workflow is an app trigger.
    if re.search(r'(?:자동|반복).{0,8}(?:실행|처리)|(?:실행|돌려).{0,8}(?:예약|매일|매주|매월)',text):return False
    return bool(re.search(
        r'(?:매일|매주|매월|매달)\s*(?:(?:오전|오후)?\s*\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?(?:까지|에)?\s*)?(?:수신된|발신된|생성된|기록된|받은|들어온).{0,12}(?:메일|자료|데이터|행|기록)'
        r'|(?:캘린더|달력).{0,25}(?:회의|약속|행사|일정).{0,15}(?:등록|추가|잡아)'
        r'|(?:회의|약속|행사).{0,30}(?:캘린더|달력).{0,10}(?:등록|추가)',text))


def infer_execution_intent(state):
    """Use ordered user evidence, never a model-written schedule as intent.

    Old creation choices remain a compatibility fallback. A later explicit
    request or cancellation wins, and the final save choice wins over both.
    """
    from agent_builder.conditions.condition_readiness import schedule_literals,unsupported_schedule_requested
    legacy=state.get('execution_mode')
    mode='scheduled' if legacy=='scheduled' else 'manual'
    source='legacy' if legacy in ('manual','scheduled') else 'default'
    unsupported=False;reset_turn=None;cancelled=False
    messages=state.get('messages',[])
    for turn,message in enumerate(messages):
        if message.get('role')!='user':continue
        text=message.get('content','')
        cancels=[match for match in _CANCEL.finditer(text)
                 if not _CANCEL_NEGATED.match(text[match.end():match.end()+12])]
        business=_business_recurrence(text)
        literal=schedule_literals(text,follow_up=mode=='scheduled') if not business else []
        has_frequency=any(key=='schedule.frequency' for key,_,_ in literal)
        unsupported_here=not business and unsupported_schedule_requested(text)
        schedule_requested=not business and bool(has_frequency or unsupported_here or _REQUEST_SCHEDULE.search(text))
        recurrence=list(_RECURRENCE.finditer(text)) if schedule_requested else []
        request_match=_REQUEST_SCHEDULE.search(text) if schedule_requested else None
        schedule_position=max([match.start() for match in recurrence]+([request_match.start()] if request_match else [-1]))
        cancel_position=cancels[-1].start() if cancels else -1
        if schedule_requested and schedule_position>cancel_position:
            if cancelled:reset_turn=turn
            mode='scheduled';source='request';unsupported=unsupported_here;cancelled=False
        elif cancels:
            mode='manual';source='request';unsupported=False;cancelled=True
    evidence_state=state
    if reset_turn is not None:
        evidence_state={**state,'messages':[message if turn>=reset_turn else {'role':'assistant','content':''}
                                            for turn,message in enumerate(messages)]}
    from .requirement_clarification import schedule_facts
    facts=schedule_facts(evidence_state)
    override=state.get('include_schedule')
    if type(override) is bool:
        mode='scheduled' if override else 'manual';source='confirmation'
    return {'mode':mode,'source':source,'facts':facts,'unsupported':unsupported}


def effective_execution_mode(state):
    return infer_execution_intent(state)['mode']


def requested_schedule(state):
    """Build a complete native spec only from explicit facts and fixed timezone."""
    intent=infer_execution_intent(state)
    if intent['mode']!='scheduled' or intent['unsupported']:return None
    facts=intent['facts'];frequency=facts.get('schedule.frequency')
    if frequency not in ('hourly','daily','weekly','monthly'):return None
    spec={key:facts.get('schedule.'+key) for key in ('frequency','hour','minute','weekday','monthday')}
    spec.update(timezone='Asia/Seoul',month_end=facts.get('schedule.month_end') is True)
    if frequency=='hourly':spec['hour']=None
    if frequency!='weekly':spec['weekday']=None
    if frequency!='monthly':spec.update(monthday=None,month_end=False)
    elif spec['month_end']:spec['monthday']=None
    from workflow_engine.scheduling.recurrence import schedule_errors
    if schedule_errors(spec):return None
    from contracts.models import ScheduleSpec
    return ScheduleSpec.model_validate(spec).model_dump()


def execution_mode_context(state):
    intent=infer_execution_intent(state)
    mode=intent['mode']
    if mode == 'manual':
        return {'mode': mode, 'source':intent['source'],'known_schedule':intent['facts'],'instruction':
                '현재 사용자 요청에 자동 반복 실행 예약이 없거나 예약을 취소했습니다. 이번에는 schedule=null로 설계합니다. '
                '요청과 자료는 실행할 때 받을 수 있습니다. 예약은 저장 후 별도로 추가할 수 있습니다. '
                '예약 주기·실행 시각·요일·일자를 필수 질문으로 묻지 않습니다. '
                '작업 내용만 설계하고 예약은 저장 후 예약 관리에서 추가할 수 있다고 안내합니다. '
                '조회 기간·업무 마감·회의 시작 시각처럼 업무 자체에 필요한 시간 조건은 보존합니다.'}
    if mode == 'scheduled':
        return {'mode': mode, 'source':intent['source'],'known_schedule':intent['facts'],'instruction':
                '사용자 요청에 자동 반복 실행 예약 의도가 있습니다. 업무 흐름과 예약 초안을 함께 설계하고 저장 전에 확인합니다. '
                'known_schedule에 이미 명시된 주기·시각·요일·일자는 그대로 반영하고 다시 질문하지 않습니다. '
                '주기·시각과 필요한 요일·일자가 미정이면 질문하고 추정하지 않습니다. '
                '시간대는 앱 고정 설정인 서울 표준시(Asia/Seoul)입니다. 시간대를 묻거나 다른 값으로 바꾸지 않습니다. '
                '실행 때 매번 요청·파일을 입력하거나 사람이 답변해야 하는 단계는 사용할 수 없습니다. '
                '필요한 입력을 저장한 설정이나 연결 자료·앞 단계 결과로 준비하고, 비파일 작업에 파일을 요구하지 않습니다. '
                '예약 추가·해제는 저장 후에도 가능하며 워크플로우의 영구적인 종류가 아닙니다.'}
    return None


def execution_schedule_question(question):
    """Identify workflow triggers, retaining times that are business inputs."""
    question={'text':question} if isinstance(question,str) else question
    key=str(question.get('key','')).casefold().replace('_','.')
    text=str(question.get('text',''))
    # A time on a data record, a query window or a calendar event is part of
    # the task even when the workflow itself is started by hand.
    business=(r'(?:조회|검색|수신|발신|집계|분석|대상).{0,12}(?:기간|날짜|시간\s*범위|시각\s*기준)'
              r'|(?:회의|약속|행사|배송|제출|마감).{0,12}(?:시작|종료|시간|시각|일시|언제)'
              r'|(?:시간|시각|날짜).{0,8}(?:열|컬럼|필드|형식|변환)|(?:열|컬럼|필드).{0,8}(?:값|형식|변환)')
    if re.search(business,text):return False
    if key=='schedule' or key.startswith('schedule.'):return True
    if re.fullmatch(r'(?:execution|run|start)\.time(?:\.setting)?|time\.of\.day',key):return True
    return bool(re.search(
        r'(?:실행|구동|자동화).{0,8}(?:시각|시간|주기|요일|일자)|언제.{0,10}(?:반복|실행)'
        r'|(?:매일|매주|매월|매시간|반복\s*주기|예약\s*주기).{0,70}(?:시간|시각|몇\s*시|요일|언제|알려|선택|정해)'
        r'|(?:예약\s*실행|반복\s*실행).{0,20}(?:주기|시각|시간|요일|일자)',text))


def filter_execution_questions(questions,state):
    if ('execution_mode' not in state and 'include_schedule' not in state
        and not any(message.get('role')=='user' for message in state.get('messages',[]))):return list(questions)
    intent=infer_execution_intent(state)
    if intent['mode']=='manual':
        return [question for question in questions if not execution_schedule_question(question)]
    from .question_time import schedule_question_keys
    facts=intent['facts'];confirmed={key for key,value in facts.items() if value is not None and value!='conflict'}
    frequency=facts.get('schedule.frequency')
    unused=set()
    if frequency in ('hourly','daily','weekly','monthly'):
        if frequency=='hourly':unused.add('schedule.hour')
        if frequency!='weekly':unused.add('schedule.weekday')
        if frequency!='monthly':unused.update(('schedule.monthday','schedule.month_end'))
        elif facts.get('schedule.month_end') is True:unused.add('schedule.monthday')
    result=[]
    for question in questions:
        keys=schedule_question_keys({'text':question} if isinstance(question,str) else question)
        if keys and keys-unused<=confirmed:continue
        result.append(question)
    return result


def filter_draft_execution_questions(draft,state):
    if not isinstance(draft,dict):return draft
    questions=draft.get('questions',[])
    by_text={question['text']:question for question in state.get('pending_questions',[])}
    keep=[index for index,text in enumerate(questions)
          if filter_execution_questions([by_text.get(text,{'text':text})],state)]
    options=draft.get('question_options') or [];responses=draft.get('question_responses') or []
    return {**draft,'questions':[questions[index] for index in keep],
        'question_options':[options[index] if index<len(options) else [] for index in keep],
        'question_responses':[responses[index] if index<len(responses) else None for index in keep]}


def project_execution_questions(state):
    draft=filter_draft_execution_questions(state.get('draft'),state)
    pending=filter_execution_questions(state.get('pending_questions',[]),state)
    result={**state,'draft':draft,'pending_questions':pending}
    removed=bool(len(pending)!=len(state.get('pending_questions',[])) or
        isinstance(draft,dict) and len(draft.get('questions',[]))!=len((state.get('draft') or {}).get('questions',[])))
    if removed and not pending and not (draft or {}).get('questions') and state.get('status')=='needs_clarification':
        # An old graph is still interrupted at its original questions. Offer a
        # retry with the existing request, rather than exposing stale controls.
        result['requirement_failure']={'kind':'execution_mode_questions','retryable':True}
    return result


def reconcile_manual_capability(result,state):
    if result.get('decision')!='clarify':return result
    original=result.get('missing_conditions',[])
    remaining=filter_execution_questions(original,state)
    if remaining==original:return result
    result={**result,'missing_conditions':remaining,'model_missing_conditions':list(original),
        'execution_mode_questions_removed':True}
    if not remaining:
        # This only removes native trigger requirements. Generation, workflow
        # validation and save checks still run, and no tool is added here.
        result['decision']='existing_tools' if result.get('tool_ids') and not result.get('missing_actions') else 'manual_review'
        result['reason']='요청에서 이미 확인했거나 현재 필요하지 않은 예약 조건은 다시 묻지 않고, 처리 기능과 업무 조건을 다음 단계에서 검증합니다.'
    return result


def validate_execution_mode(state, draft, questions):
    """Apply a grounded optional trigger and ask only for missing fields."""
    if draft['unsupported']:
        return []
    intent=infer_execution_intent(state)
    mode=intent['mode']
    if ('execution_mode' not in state and 'include_schedule' not in state
            and intent['source']=='default' and draft.get('schedule')):
        # Saved configurations and source replacement predate creation-intent
        # metadata. Their already persisted reservation is not a new model claim.
        # New design sessions always include execution_mode, including None.
        return []
    if mode == 'manual' and draft.get('schedule'):
        return ['현재 요청에는 실행 예약이 없습니다. schedule을 비우세요. 예약은 저장 후 예약 관리에서 추가할 수 있습니다.']
    if mode=='scheduled':
        if intent['unsupported']:return ['요청한 예약 주기는 현재 지원하는 시·일·주·월 예약 범위 밖입니다. 지원 주기로 임의 변경하지 않습니다.']
        known=requested_schedule(state)
        if known:
            # Literal user evidence owns the optional trigger, even when the
            # generator omitted it or proposed a different default clock.
            draft['schedule']=known
        else:
            # A generator's guessed clock is never a preview of a confirmed
            # schedule. Keep the workflow while collecting missing facts.
            draft['schedule']=None
            from .requirement_clarification import missing_schedule_questions
            missing=missing_schedule_questions(state)
            questions.extend(question['text'] for question in missing if question['text'] not in questions)
    return []
