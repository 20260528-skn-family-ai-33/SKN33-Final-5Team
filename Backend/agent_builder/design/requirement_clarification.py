"""Turn incomplete business requirements into customer questions, never credential fields."""
from copy import deepcopy
import hashlib
import json
import re

from contracts.models import AgentDraft, ExecutionPlan, QuestionResponse


_IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9]*(?:[_.][A-Za-z0-9]+)+')
_MANAGED = re.compile(r'(?:(?:gmail|google|oauth)[_. -]*)?(?:access[_. -]*token|refresh[_. -]*token|client[_. -]*secret|api[_. -]*key)|(?:gmail|google)[_. -]*(?:account|credential)[_. -]*id', re.I)
_OPTIONAL_SUMMARY = {'summary_format_preference', 'summary.format_preference', 'summary_format', 'summary_style'}
_FREQUENCIES = {'매시간':'hourly', '매일':'daily', '매주':'weekly', '매월':'monthly'}
_BOUNDS = {'schedule.hour':(0,23,'시'), 'schedule.minute':(0,59,'분'),
           'schedule.weekday':(0,6,None), 'schedule.monthday':(1,31,'일')}


def _skipped_optional_keys(state):
    latest={};messages=state.get('messages',[])
    for event in state.get('requirement_answers',[]):
        turn=event.get('turn')
        if (type(turn) is not int or not 0<=turn<len(messages) or messages[turn].get('role')!='user'
                or event.get('message_content',event.get('answer'))!=messages[turn].get('content')):continue
        for question in event.get('questions',[]):
            key=question.get('key')
            if key and turn>=latest.get(key,(-1,False))[0]:
                latest[key]=(turn,event.get('skipped') is True)
    return {key for key,(_,skipped) in latest.items() if skipped}


def _bound_schedule_answers(event,state,source):
    """Use server-recorded question identities, never an unrelated typed value."""
    snapshots=[question for question in event.get('questions',[]) if isinstance(question,dict)]
    known={question.get('id'):question for question in [*state.get('pending_questions',[]),
           *state.get('capability_questions',[]),*snapshots] if question.get('id')}
    ids=event.get('question_ids',[])
    if source=='requirement_answers':
        if event.get('skipped'):return []
        value=event.get('value',event.get('answer',''))
        return [(question,value) for question in snapshots]
    result=[]
    for item in event.get('answers',[]):
        identifier=item.get('question_id')
        if not identifier or identifier not in ids:continue
        question=known.get(identifier)
        if source=='condition_answers' and question is None:
            key=item.get('key')
            if key in event.get('candidate_keys',[]) and isinstance(key,str) and key.startswith('schedule.'):
                question={'id':identifier,'key':key,'text':''}
        if question is not None:
            result.append((question,item.get('value',item.get('answer',''))))
    if result:return result
    # Old single-question responses can still be grounded by the original
    # pending record. A composite free-text response is not copied to all fields.
    if len(ids)==1 and ids[0] in known:return [(known[ids[0]],event.get('answer',''))]
    if source=='condition_answers' and len(event.get('candidate_keys',[]))==1 and len(ids)==1:
        key=event['candidate_keys'][0]
        if key.startswith('schedule.'):return [({'key':key,'text':''},event.get('answer',''))]
    return []


def _apply_schedule_answer(facts,question,value):
    from .question_time import schedule_question_keys,time_parts
    keys=schedule_question_keys(question)
    if not keys or type(value) is bool and 'schedule.month_end' not in keys:return
    answer=str(value).strip()
    from .question_time import execution_clock_question
    if {'schedule.hour','schedule.minute'}<=keys or execution_clock_question(question):
        try:hour,minute=time_parts(value)
        except ValueError:pass
        else:
            facts.update({'schedule.hour':hour,'schedule.minute':minute})
            return
    key=question.get('key')
    if key in _BOUNDS and re.fullmatch(r'\d{1,2}',answer):
        lower,upper,_=_BOUNDS[key]
        if lower<=int(answer)<=upper:facts[key]=int(answer)
    elif key=='schedule.frequency':
        frequency=_FREQUENCIES.get(answer,answer)
        if frequency in _FREQUENCIES.values():facts[key]=frequency
    elif key=='schedule.weekday' and answer in ('월요일','화요일','수요일','목요일','금요일','토요일','일요일'):
        facts[key]=('월요일','화요일','수요일','목요일','금요일','토요일','일요일').index(answer)
    elif key=='schedule.month_end':
        if type(value) is bool:facts[key]=value
        elif answer in ('말일','매월 말일'):facts[key]=True


def _schedule_follow_up(content,facts):
    if not facts:return False
    # A later query window or calendar event is not an execution-time edit.
    if re.search(r'(?:조회|검색|수신|집계|분석).{0,12}(?:기간|날짜|시간\s*범위)|(?:회의|약속|행사|배송|제출|마감).{0,12}(?:시간|시각|시작|종료)',content):return False
    ending=r'\s*(?:(?:로|으로|에)?\s*(?:바꿔|변경해|설정해|해)?\s*(?:줘|주세요|요|할게|할게요)?)?[.!?\s]*'
    if facts.get('schedule.frequency')=='weekly' and re.fullmatch(r'\s*[월화수목금토일]요일'+ending,content):return True
    if facts.get('schedule.frequency')=='monthly' and re.fullmatch(r'\s*(?:\d{1,2}\s*일|말일|마지막\s*날)'+ending,content):return True
    return bool(re.search(r'(?:예약|실행|반복|주기|스케줄).{0,20}(?:시각|시간|요일|일자|변경|바꿔|정해|설정)',content) or
        re.fullmatch(r'\s*(?:(?:오전|오후|아침|저녁|밤|새벽)\s*)?\d{1,2}\s*(?:시(?:\s*\d{1,2}\s*분|\s*반)?|:[0-5]\d)\s*(?:(?:로|으로|에)?\s*(?:바꿔|변경해|설정해|실행해)?\s*(?:줘|주세요|요|할게|할게요)?)?[.!?\s]*',content))


def schedule_facts(state):
    """Only explicit recurrence literals and answers tied to real questions."""
    from agent_builder.conditions.condition_readiness import schedule_literals
    facts={};events={}
    for source in ('requirement_answers','condition_answers','capability_answers'):
        for event in state.get(source,[]):
            turn=event.get('turn')
            if type(turn) is int:events.setdefault(turn,[]).append((source,event))
    for turn,message in enumerate(state.get('messages',[])):
        if message.get('role')!='user':continue
        content=message['content']
        cancelled=[match for match in re.finditer(r'(?:예약|자동\s*실행|반복\s*실행|정기\s*실행)(?:은|는|을|를)?\s*(?:취소|해제|중지|하지\s*마|필요\s*없|끄)',content)
                   if not re.match(r'\s*(?:하지\s*마|하지\s*말|안\s*해|않|금지)',content[match.end():match.end()+12])]
        if cancelled:facts={}
        for key,value,_ in schedule_literals(content,follow_up=_schedule_follow_up(content,facts)):
            facts[key]=value
        for source,event in events.get(turn,[]):
            if event.get('message_content',event.get('answer'))!=content:continue
            for question,value in _bound_schedule_answers(event,state,source):
                _apply_schedule_answer(facts,question,value)
    from workflow_engine.scheduling.policy import WORKFLOW_TIMEZONE
    facts['schedule.timezone']=WORKFLOW_TIMEZONE
    return facts


def missing_schedule_questions(state):
    from .execution_mode import effective_execution_mode
    mode=effective_execution_mode(state)
    if mode=='manual':return []
    from agent_builder.conditions.condition_readiness import QUESTIONS, choice_options
    facts=schedule_facts(state)
    if not any(key!='schedule.timezone' for key in facts) and mode!='scheduled':return []
    frequency=facts.get('schedule.frequency')
    required=['schedule.frequency'] if frequency not in _FREQUENCIES.values() else []
    if frequency in ('daily','weekly','monthly'):required.append('schedule.time')
    if frequency=='hourly':required.append('schedule.minute')
    if frequency=='weekly':required.append('schedule.weekday')
    if frequency=='monthly' and facts.get('schedule.month_end') is not True:required.append('schedule.monthday')
    reasons={'schedule.frequency':'자동 실행을 반복할 주기를 정해야 예약을 등록할 수 있습니다.',
             'schedule.time':'자동 실행을 시작할 시각을 시와 분까지 정해야 예약을 등록할 수 있습니다.',
             'schedule.hour':'자동 실행을 시작할 시각이 필요합니다.',
             'schedule.minute':'예약 시각을 정확히 정하려면 몇 분에 실행할지 필요합니다.',
             'schedule.timezone':'같은 시각도 지역마다 다르므로 예약의 기준 시간대가 필요합니다.',
             'schedule.weekday':'주간 예약을 실행할 요일이 필요합니다.',
             'schedule.monthday':'월간 예약을 실행할 날짜나 말일 여부가 필요합니다.'}
    result=[]
    for key in required:
        if key=='schedule.time' and all(part in facts and facts[part]!='conflict' for part in ('schedule.hour','schedule.minute')):continue
        if key in facts and facts[key]!='conflict':continue
        text=QUESTIONS.get(key,'몇 시에 실행할까요? 09:00처럼 시와 분을 입력해주세요.')
        if key=='schedule.hour':text='몇 시에 실행할까요? 24시간 기준으로 입력해주세요.'
        if key=='schedule.timezone':text='어느 시간대를 기준으로 실행할까요? 예: 한국 시간 또는 Asia/Seoul'
        options=choice_options(key,state)
        response={'type':'single_choice' if options else 'text'}
        if key=='schedule.time':response={'type':'time'}
        if key in _BOUNDS:
            lower,upper,unit=_BOUNDS[key]
            response={'type':'number','minimum':lower,'maximum':upper,'step':1}
            if unit:response['unit']=unit
        result.append(dict(key=key,text=text,options=options,response=response,reason=reasons[key]))
    return result


def business_questions(unresolved,state):
    """Return questions plus a flag asking the extractor to replace unknown field IDs."""
    from agent_builder.conditions.condition_readiness import QUESTIONS, choice_options
    questions=[];unknown=False;skipped=_skipped_optional_keys(state)
    users='\n'.join(m['content'] for m in state.get('messages',[]) if m.get('role')=='user')
    for raw in unresolved:
        from workflow_engine.scheduling.policy import timezone_question
        if timezone_question(raw if isinstance(raw,dict) else {'key':raw,'text':raw}):continue
        if isinstance(raw,dict):
            if raw.get('required',True) is False and raw.get('key') in skipped:continue
            # New extractors declare the customer's control directly. Legacy
            # string compatibility below must not rewrite their business labels.
            question={key:deepcopy(raw[key]) for key in ('key','text','options','group','reason','required') if key in raw}
            question['response']=QuestionResponse.model_validate(raw['response']).model_dump(exclude_none=True)
            questions.append(question)
            continue
        text=raw.strip()
        if not text or _MANAGED.search(text):continue
        if text.casefold() in _OPTIONAL_SUMMARY:
            if re.search(r'요약.{0,12}(?:형식|스타일)|(?:형식|스타일).{0,12}요약',users):
                questions.append(dict(key='summary.format',text='요약을 어떤 방식으로 정리할까요?',
                    options=['메일별 핵심 요약','전체 메일을 묶어 요약'],response={'type':'single_choice'}))
            continue
        if text in QUESTIONS:
            options=choice_options(text,state)
            questions.append(dict(key=text,text=QUESTIONS[text],options=options))
        elif _IDENTIFIER.fullmatch(text):unknown=True
        else:questions.append(dict(key='requirements.detail',text=text,options=[]))
    schedule=[] if any(isinstance(item,dict) for item in unresolved) else missing_schedule_questions(state)
    # Explicit native schedule gaps take precedence over a model's generic time label.
    questions=schedule+questions
    seen=set();result=[]
    for question in questions:
        identity=(question['key'],question['text'])
        if identity in seen:continue
        seen.add(identity);result.append(question)
    return result,unknown


def clarification_reply(state,questions,calls,*,unknown=False):
    if unknown and not questions:
        questions=[dict(key='requirements.detail',text='작업에 필요한 입력이나 처리 기준을 조금 더 구체적으로 알려주세요.',options=[],response={'type':'text'})]
    if not questions:return None
    from .question_controls import complete_questions
    questions=complete_questions(questions,state)
    if not questions:return None
    records=[]
    identity={'session':state.get('session_id'),'users':[m['content'] for m in state.get('messages',[]) if m.get('role')=='user']}
    for question in questions:
        record={**question,'source':'requirements'}
        record['id']=hashlib.sha256(json.dumps([identity,question],ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]
        records.append(record)
    existing=state.get('draft')
    if isinstance(existing,dict):draft=deepcopy(existing)
    else:
        draft=AgentDraft(name='업무 조건 확인',purpose='필요한 조건을 확인한 뒤 업무 흐름을 구성합니다.',
            instructions='사용자 요청과 확인한 답변을 보존합니다.',tool_ids=[],document_ids=[r['id'] for r in state.get('resources',[])],
            requirements=[],questions=[],unsupported=[],explanation='업무에 필요한 조건을 확인합니다.',
            execution_plan=ExecutionPlan(filters=[],sort=[],match='all',missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='json')).model_dump()
    draft.update(questions=[q['text'] for q in records[:3]],question_options=[q.get('options',[]) for q in records[:3]],
                 question_responses=[q.get('response') for q in records[:3]])
    detail='업무를 구성하기 전에 필요한 조건을 확인할게요.\n'+'\n'.join(q['text'] for q in records)
    return dict(requirement_route='ask',requirement_required=True,requirement_ledger=None,requirement_failure=None,
        requirement_fingerprint=None,requirement_digest=None,status='needs_clarification',confirmed=False,saved_agent_id=None,
        draft=draft,pending_questions=records,issues=[],semantic_issues=[],
        messages=state.get('messages',[])+[dict(role='assistant',content=detail)],
        trace=state.get('trace',[])+[dict(node='requirements',label='업무 조건 확인',status='needs_input',detail='필요한 업무 조건을 질문으로 안내했습니다.',model_calls=calls)])
