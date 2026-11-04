"""Enforced capability decisions persist through generation, resume and save."""
import hashlib
import os
import re
from agent_builder.capabilities.capability_judgement import assess_capability, validate_assessment_result, AssessmentConflictError
from agent_builder.capabilities.capability_gate import capability_enforced, input_fingerprint
from agent_builder.conditions.input_resolution import answer_kind, resolution_options, latest_resolution, option_answer, value_answer, pending_nonanswer
from agent_builder.conditions.condition_check import _pending_draft


def _question_text(text):
    import re
    text=text.strip()
    if re.fullmatch(r'파일\s*ID|file[_ ]?id',text,re.I):return '처리할 파일을 자료 연결에서 업로드해 주세요.'
    if text.endswith(('?', '주세요.', '알려주세요', '주세요')):return text
    return text.rstrip('.')+'에 대해 알려주세요.'


_TASK_WORDS=re.compile(r'줘|주세요|해\s*줄|만들|정리|저장|보내|발송|공유|골라|고르|남겨|뽑|계산|비교|합계|집계|'
                       r'바꿔|변경|추가|대신|말고|필터|선별|제외|포함|파일|표|csv|json|엑셀|xlsx|예약|매일|매주',re.I)


_WITHDRAWAL=re.compile(r'필요\s*없|빼|말고|없이|안\s*해|하지\s*마|하지\s*말|제외')


def _short_reply(state):
    """A brief reply that names no new work: no digits, no request verb, at most 12 characters."""
    user=[m['content'] for m in state.get('messages',[]) if m.get('role')=='user']
    if not user:return False
    text=re.sub(r'[\s.!?~]+','',user[-1])
    if _WITHDRAWAL.search(text):
        return False  # "슬랙은 필요 없어" removes the refused action; it must be judged again (PR #28 review)
    refused=[w for a in ((state.get('capability_assessment') or {}).get('missing_actions') or []) for w in re.findall(r'[가-힣A-Za-z]{2,}',a)]
    if any(w in text for w in refused if w not in ('외부','기능','전송','발송')):
        return False  # the reply talks about the refused action itself
    return 0<len(text)<=12 and not re.search(r'\d',text) and not _TASK_WORDS.search(text)


def make_capability_check(model,transport=None,mode=None):
    setting=mode or os.getenv('A4B_CAPABILITY_MODE','off')
    if setting not in ('off','enforce'):raise ValueError('A4B_CAPABILITY_MODE는 off/enforce이어야 합니다.')
    def check(state):
        if setting=='off' and not capability_enforced(state):
            return {'capability_route':'conditions','capability_assessment':None,'capability_enforced':False,
                    'capability_fingerprint':None,'capability_questions':[],'capability_deferred':False}
        deferred=False;reuse_questions=False
        try:
            from agent_builder.conditions.condition_readiness import input_issues
            faults=input_issues(state)
            events=state.get('capability_answers',[])
            event=events[-1] if events and events[-1]['turn']==len(state.get('messages',[]))-1 else None
            previous=state.get('capability_assessment') or {}
            if faults:result=assess_capability(state,None)
            elif (previous.get('decision')=='existing_tools' and (latest_resolution(state) or option_answer(state) or value_answer(state) or pending_nonanswer(state))
                  and state.get('messages', [])[-1].get('role')=='user'
                  and state.get('capability_fingerprint')==input_fingerprint({**state,'messages':state['messages'][:-1]})):
                # A non-answer or an offered closed-vocabulary choice for a condition question
                # changes no supported-action facts. Reuse only with the original
                # request/resources/catalog intact; condition_check still validates the slots.
                validate_assessment_result(previous,state)
                result={**previous,'model_calls':0,'answer_resolution':'condition_choice' if option_answer(state) or value_answer(state) else 'condition_nonanswer'}
            elif event and previous.get('decision')=='clarify' and event['kind'] in ('unknown','defer','guidance'):
                validate_assessment_result(previous,state)
                ids=set(event['question_ids'])
                count=sum(e['kind']=='unknown' and bool(ids.intersection(e['question_ids'])) for e in events)
                deferred=event['kind']=='defer' or count>=2
                result={**previous,'model_calls':0,'answer_resolution':event['kind']}
                reuse_questions=True
            elif (previous.get('decision') in ('missing_capability','manual_review') and _short_reply(state)
                  and state.get('messages', [])[-1].get('role')=='user'
                  and state.get('capability_fingerprint')==input_fingerprint({**state,'messages':state['messages'][:-1]})):
                # A short reply with no new task ("네", "모두 유지", "그만할래") changes no supported-action
                # facts. Re-judging the whole conversation flipped the verdict on every turn (live
                # 2026-11-03: missing_capability -> existing_tools -> invented "월간 예약").
                # A reply to a clarify question is an answer and is judged again (branches above/below).
                validate_assessment_result(previous,state)
                result={**previous,'model_calls':0,'answer_resolution':'short_reply'}
            else:
                if transport is None:
                    from agent_builder.design.nodes.direct_conversation import OllamaPlanner
                    active=OllamaPlanner(model)
                else:active=transport
                result=assess_capability(state,active)
        except Exception as error:
            result={'decision':'system_error','issues':['도구 지원 판단을 완료하지 못했습니다.'], 'error_type':type(error).__name__,'model_calls':None}
            if isinstance(error,AssessmentConflictError):
                result.update(issues=['모델의 지원 판정과 누락 항목이 서로 맞지 않습니다. 요청을 보존하고 생성·저장을 중단했습니다.'],
                              assessment_conflict=error.reported,model_calls=error.model_calls)
        fingerprint=input_fingerprint(state)
        decision=result['decision']
        update={'capability_route':'conditions','capability_assessment':result,'capability_enforced':True,
            'capability_fingerprint':fingerprint,'capability_deferred':deferred,
            'trace':state.get('trace',[])+[{'node':'capability_check','label':'등록 도구 지원 검토',
                'status':'completed' if decision=='existing_tools' else 'needs_input',
                'detail':decision,'model_calls':result.get('model_calls')}]}
        if decision=='existing_tools':
            pending=[q for q in state.get('pending_questions',[]) if q.get('source')!='capability']
            update.update(capability_questions=[],pending_questions=pending,confirmed=False,saved_agent_id=None,gap_condition_review=None,
                          issues=[],semantic_issues=[])
            if result.get('native_schedule_reconciled'):
                update['condition_required']=True  # schedule slots must be grounded before any draft
        elif decision=='clarify':
            questions=(state.get('capability_questions',[]) if reuse_questions else [])
            if not questions:
                questions=[{'id':hashlib.sha256((fingerprint+':'+str(i)+':'+text).encode()).hexdigest()[:24],
                            'key':'capability.condition.'+str(i),'source':'capability','text':_question_text(text),
                            'options':resolution_options(state)} for i,text in enumerate(result['missing_conditions'])]
            # AgentDraft supports 3 visible questions; preserve every question record.
            grouped=questions[:2]+([{'text':'\n'.join(q['text'] for q in questions[2:]),'options':[]}] if len(questions)>2 else [])
            ledger={'decision':'clarify','questions':grouped,'slots':{}}
            reply=('미정 상태로 보류했습니다. 답변을 보내면 다시 확인합니다.\n' if deferred else '')+'\n'.join(q['text'] for q in questions)
            update.update(capability_route='await_capability',status='needs_clarification',confirmed=False,gap_condition_review=None,
                saved_agent_id=None,draft=_pending_draft(state,ledger),pending_questions=questions,
                capability_questions=questions,issues=[],semantic_issues=[],verification_enforced=False,
                messages=state.get('messages',[])+[{'role':'assistant','content':reply}])
        elif decision=='missing_capability':
            ledger={'decision':'unsupported','unsupported_actions':result['missing_actions'],'slots':{},'questions':[]}
            update.update(capability_route='supervisor',status='unsupported',condition_enforced=False,
                draft=_pending_draft(state,ledger),confirmed=False,saved_agent_id=None,pending_questions=[],
                capability_questions=[],issues=[],semantic_issues=[],verification_enforced=False,
                messages=state.get('messages',[])+[{'role':'assistant','content':'등록 도구에 부족한 기능이 있는지 확인했습니다. 제작·재설계·보류 중 선택할 수 있습니다.'}])
        else:
            issues=(result.get('issues') if decision=='system_error' else None) or ['지원 여부의 검토가 필요합니다. 요청을 보완한 뒤 다시 확인해주세요.']
            update.update(capability_route='stop',status='validation_failed',confirmed=False,saved_agent_id=None,gap_condition_review=None,
                pending_questions=[],capability_questions=[],issues=issues,semantic_issues=[],
                messages=state.get('messages',[])+[{'role':'assistant','content':' '.join(issues)}])
        return update
    return check


def await_capability_node(state):
    from langgraph.types import interrupt
    questions=state['capability_questions']
    payload={'kind':'clarification','questions':[q['text'] for q in questions],
             'question_records':questions,'scope':'capability','deferred':state.get('capability_deferred',False)}
    while True:
        answer=interrupt(payload)
        if answer.get('type')!='message' or not str(answer.get('content','')).strip():
            payload={**payload,'error':'지원 판단의 확인 질문에 답변해주세요. 일반 저장 확인으로 넘길 수 없습니다.'}
            continue
        if not answer.get('question_id') or answer['question_id'] in [q['id'] for q in questions]:break
        payload={**payload,'error':'현재 대화의 확인 질문에 답변해주세요.'}
    event={'turn':len(state['messages']),'answer':answer['content'],'kind':answer_kind(answer['content']),
           'question_ids':[answer['question_id']] if answer.get('question_id') else [q['id'] for q in questions]}
    return {'messages':state['messages']+[{'role':'user','content':answer['content']}],
            'capability_answers':state.get('capability_answers',[])+[event], 'confirmed':False,'saved_agent_id':None}
