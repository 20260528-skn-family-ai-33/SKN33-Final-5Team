"""Enforced capability decisions persist through generation, resume and save."""
import hashlib
import os
import re
from agent_builder.capabilities.capability_judgement import assess_capability, guard_delivery_revision, validate_assessment_result, AssessmentConflictError
from agent_builder.capabilities.capability_gate import capability_enforced, input_fingerprint
from agent_builder.conditions.input_resolution import answer_kind, resolution_options, latest_resolution, option_answer, numeric_answer, pending_nonanswer
from agent_builder.conditions.condition_check import _pending_draft


def _question_text(text):
    text=text.strip()
    # Resource identifiers belong to the attachment UI. Business columns with
    # names such as "파일 ID" remain valid user-selected fields.
    def business_column(match):
        return bool(re.match(r'''\s*["'`’”]?\s*(?:라는|란)?\s*(?:열|컬럼|칼럼|필드|column|field)''',
                             text[match.end():],re.I))
    resource=re.search(r'(?:파일|자료|문서|표)(?:\s*의)?\s*(?:(?:구체적인|고유한|실제)\s*)?'
                       r'(?:ID|아이디|식별자)|file[_ ]?id',text,re.I)
    if resource and not business_column(resource):
        guidance='처리할 파일을 자료 연결에서 업로드해 주세요.'
        # A combined question can also contain a genuinely missing recipient or
        # business choice. Do not discard that second question when rendering UI guidance.
        remaining=re.match(r'\s*(?:와|과|및|또는|나)\s*(.+)',text[resource.end():])
        return guidance+(' '+_question_text(remaining[1]) if remaining else '')
    credential=re.search(r'인증\s*정보|API\s*키|access[_ ]?token|비밀번호',text,re.I)
    if (credential and not business_column(credential) and re.search(r'Gmail|지메일',text,re.I)
            and re.search(r'알려|입력|제공|필요|보내',text[credential.end():])):
        return 'Gmail 연결에서 계정 연결 상태를 확인해 주세요.'
    if text.endswith(('?', '？')):return text
    stem=text.rstrip('.').rstrip()
    if stem.endswith('해야 합니다'):
        return stem.removesuffix('해야 합니다')+'해 주세요.'
    if stem.endswith(('니다','세요')):
        return stem+'.'
    return stem+'에 대해 알려주세요.'


# Only explicit acknowledgements/non-progress replies may reuse a verdict.
# Absence of a task verb is not evidence that the scope is unchanged:
# "메일은 취소", "요약만" and unknown short replies must be reconsidered.
_PASSIVE_REPLIES=frozenset({
    '네','넵','예','응','응좋아','좋아','좋아요','알겠어','알겠어요','알겠습니다',
    '확인','확인했어','확인했어요','확인했습니다','오케이','ok','okay',
    '모두유지','전부유지','그대로','그만할래',
})



def _short_reply(state):
    """Reuse only known passive replies; scope changes always reach assessment."""
    user=[m['content'] for m in state.get('messages',[]) if m.get('role')=='user']
    if not user:return False
    text=re.sub(r'[\s.!?~]+','',user[-1]).casefold()
    return text in _PASSIVE_REPLIES


def make_capability_check(model,transport=None,mode=None,error_observer=None,api_key=''):
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
            elif (previous.get('decision')=='existing_tools' and (latest_resolution(state) or option_answer(state) or numeric_answer(state) or pending_nonanswer(state))
                  and state.get('messages', [])[-1].get('role')=='user'
                  and state.get('capability_fingerprint')==input_fingerprint({**state,'messages':state['messages'][:-1]})):
                # A non-answer or an offered closed-vocabulary choice for a condition question
                # changes no supported-action facts. Reuse only with the original
                # request/resources/catalog intact; condition_check still validates the slots.
                validate_assessment_result(previous,state)
                resolution = 'condition_choice' if option_answer(state) or numeric_answer(state) else 'condition_nonanswer'
                result = guard_delivery_revision({**previous, 'model_calls': 0, 'answer_resolution': resolution}, state)
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
                    from agent_builder.design.requirement_ledger import requirement_transport
                    active=requirement_transport(os.getenv('A4B_CAPABILITY_MODEL') or model, api_key)
                else:active=transport
                result=assess_capability(state,active)
        except Exception as error:
            if error_observer:error_observer(error)
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
        elif decision=='clarify' or decision=='manual_review' and not result.get('request_revision_conflict'):
            questions=(state.get('capability_questions',[]) if reuse_questions else [])
            if not questions:
                texts=list(result['missing_conditions'])
                if decision=='manual_review':
                    if result.get('missing_actions'):
                        texts.append('지원 여부를 확정하지 못한 기능이 있습니다: '+' / '.join(result['missing_actions'])+
                                     '. 이 기능을 제외하고 진행할지, 변경할 요청을 알려주세요.')
                    if not texts:
                        texts=['처리할 업무와 반드시 필요한 결과를 구체적으로 알려주세요.']
                questions=[{'id':hashlib.sha256((fingerprint+':'+str(i)+':'+text).encode()).hexdigest()[:24],
                            'key':'capability.condition.'+str(i),'source':'capability','text':_question_text(text),
                            'options':resolution_options(state)} for i,text in enumerate(texts)]
            # Match main's two-question UI contract; retain the full assessment.
            questions=questions[:2]
            ledger={'decision':'clarify','questions':questions,'slots':{}}
            reply=('미정 상태로 보류했습니다. 답변을 보내면 다시 확인합니다.\n' if deferred else '')+'\n'.join(q['text'] for q in questions)
            update.update(capability_route='supervisor' if decision=='manual_review' else 'await_capability',status='needs_clarification',confirmed=False,gap_condition_review=None,
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
