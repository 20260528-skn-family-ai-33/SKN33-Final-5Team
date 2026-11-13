"""Enforced capability decisions persist through generation, resume and save."""
import hashlib
import json
import os
import re
from agent_builder.capabilities.capability_judgement import assess_capability, guard_delivery_revision, validate_assessment_decision, validate_assessment_result, AssessmentConflictError
from agent_builder.capabilities.capability_gate import capability_enforced, input_fingerprint
from agent_builder.conditions.input_resolution import answer_kind, latest_resolution, option_answer, numeric_answer, pending_nonanswer
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


class _FeedbackTransport:
    """Re-ask the same assessment once with code evidence about settled conditions."""
    def __init__(self,inner,feedback):
        object.__setattr__(self,'inner',inner);object.__setattr__(self,'feedback',feedback)
    def __setattr__(self,name,value):
        setattr(self.inner,name,value)  # assess_capability sets the decoder schema
    def invoke(self,messages):
        return self.inner.invoke([*messages,{'role':'user','content':self.feedback}])


def _without_settled_conditions(state,transport,result,retried=False):
    """Never show or register a capability question the user already settled.

    Removing every question does not make the request supported: the same evidence is
    returned to the assessment once, and a repeated settled-only answer stays in review.
    """
    if result.get('decision')!='clarify':return result
    # Clarification core: settled questions and design-time file requests are not capability questions.
    from agent_builder.conditions.clarification_core import screen
    from agent_builder.conditions.question_contract import settled_question_issue
    _,settled=screen(result['missing_conditions'],state)
    if not settled:return result
    names={q for q,_ in settled}
    record=list(result.get('settled_conditions',[]))+[{'question':q,'reason':r} for q,r in settled]
    remaining=[c for c in result['missing_conditions'] if c not in names]
    if remaining:
        return {**result,'missing_conditions':remaining,'settled_conditions':record}
    if not retried and transport is not None:
        calls=result.get('model_calls') or 0
        feedback=(settled_question_issue(result['missing_conditions'],state) or '')+' '+' / '.join(f'{q} ({r})' for q,r in settled)
        again=assess_capability(state,_FeedbackTransport(transport,'다음 질문은 사용자에게 물을 조건이 아닙니다. 빼고 다시 판단하세요: '+feedback.strip()))
        again={**again,'model_calls':calls+(again.get('model_calls') or 0),'settled_conditions':record}
        return _without_settled_conditions(state,transport,again,retried=True)
    return {**result,'decision':'manual_review','missing_conditions':[],'settled_conditions':record,
            'reason':'지원 판정이 물을 필요가 없는 조건(이미 정한 조건·설계 단계 파일 요구)만 다시 물어 질문을 표시하지 않고 검토 상태로 두었습니다.'}


# A missing *action* names something the user asked the workflow to do. A request to the user
# ("…확인해 주세요") or a check with no link to the request is not a missing capability; a live
# 9B pilot returned exactly such sentences for supported requests (2026-11-11 RunPod eval).
_TO_USER = re.compile(r'(?:주세요|주십시오|바랍니다|하세요|해야\s*합니다)\s*[.!]?$')


def _ungrounded_action(action,state):
    from agent_builder.design.turn_intent import _stems
    text=action.strip()
    if _TO_USER.search(text):return True
    users=' '.join(m['content'] for m in state.get('messages',[]) if m.get('role')=='user')
    return '확인' in text and not (_stems(text) & _stems(users))


def _without_ungrounded_actions(state,transport,result,retried=False):
    """Re-assess once when every missing action is a request to the user, then keep the verdict.

    The wording of missing_actions does not decide support. A supported request re-judged
    with this evidence changes its verdict (2026-11-11 RunPod C03); a truly unsupported one
    (Teams, Notion, PostgreSQL: U02/U04/X01) keeps missing_capability, and that verdict is
    kept instead of being turned into a review state. Only the sentences are recorded.
    """
    if result.get('decision')!='missing_capability':return result
    invalid=[a for a in result.get('missing_actions',[]) if _ungrounded_action(a,state)]
    if not invalid:return result
    record={'invalid_missing_actions':invalid}
    if len(invalid)<len(result['missing_actions']):
        return {**result,'missing_actions':[a for a in result['missing_actions'] if a not in invalid],**record}
    if retried or transport is None:
        return {**result,**record}
    calls=result.get('model_calls') or 0
    feedback=('다음 missing_actions는 사용자 요청의 실제 동작이 아니라 확인 요청 문장이므로 부족 기능이 아닙니다: '
              +' / '.join(invalid)+'. 요청의 각 동작을 등록 도구와 다시 대조해 판단하세요.')
    again=assess_capability(state,_FeedbackTransport(transport,feedback))
    again={**again,'model_calls':calls+(again.get('model_calls') or 0),**record}
    return _without_ungrounded_actions(state,transport,again,retried=True)


def _generic_question(question,state):
    """A check request with no link to the request ("처리 단계와 조건을 확인해 주세요."), not a question.

    All three must hold, so real questions ("몇 시에 실행할까요?", "…조건을 알려주세요.") stay.
    """
    from agent_builder.design.turn_intent import _stems
    text=question.strip()
    users=' '.join(m['content'] for m in state.get('messages',[]) if m.get('role')=='user')
    # The sentence tells the user to check something ("확인해 주세요"); asking for information
    # ("확인 항목을 알려주세요", "기준을 알려주세요") is a question and is never matched.
    return bool(_CHECK_REQUEST.search(text)) and not (_stems(text) & _stems(users))


_CHECK_REQUEST = re.compile(r'확인(?:해|하여|하시어)?\s*(?:주세요|주십시오|바랍니다)\s*[.!]?$|확인하세요\s*[.!]?$|확인해야\s*합니다\s*[.!]?$')


def _without_generic_questions(state,transport,result,retried=False):
    """Re-assess once when every clarify question is a generic check request; then keep the verdict.

    2026-11-11 RunPod M08: two copies of "처리 단계와 조건을 확인해 주세요." were the only
    questions, while the real missing item was the new column name. Duplicates are merged.
    """
    if result.get('decision')!='clarify':return result
    questions=list(dict.fromkeys(q.strip() for q in result.get('missing_conditions',[]) if q.strip()))
    generic=[q for q in questions if _generic_question(q,state)]
    result={**result,'missing_conditions':questions}
    if not generic:return result
    record={'generic_questions':generic}
    if len(generic)<len(questions):
        return {**result,'missing_conditions':[q for q in questions if q not in generic],**record}
    if retried or transport is None:
        return {**result,**record}
    calls=result.get('model_calls') or 0
    feedback=('다음 질문은 사용자가 답할 수 있는 구체적인 질문이 아니라 요청과 무관한 확인 문장입니다: '+' / '.join(generic)+
              '. 사용자 요청에서 실제로 정해지지 않은 값만 구체적인 질문으로 적거나, 모두 정해졌으면 질문 없이 판단하세요.')
    again=assess_capability(state,_FeedbackTransport(transport,feedback))
    again={**again,'model_calls':calls+(again.get('model_calls') or 0),**record}
    return _without_generic_questions(state,transport,again,retried=True)


def _question_key(text, fallback, state=None):
    """Carry a downstream slot only when the displayed question identifies it."""
    from agent_builder.conditions.condition_readiness import CHOICES, QUESTIONS, policy_clauses
    from agent_builder.design.gmail_slack import _QUESTION as GMAIL_SLACK_CHANNEL_QUESTION
    if text == GMAIL_SLACK_CHANNEL_QUESTION:
        return 'slack.channel_id'
    if text in ('delivery.recipient', 'duplicate.policy'):
        return text
    if text == QUESTIONS['duplicate.policy']:
        return 'duplicate.policy'
    # Earlier assessments have no slot ID. Only use an unambiguous label or
    # a choice between duplicate policies; composite questions keep their ID.
    label = text.removesuffix('에 대해 알려주세요.').strip()
    if label in ('수신자', '수신자 주소', '수신자 이메일 주소', '수신자 이메일'):
        return 'delivery.recipient'
    # A displayed closed menu is also structured evidence. Require the whole
    # surrounding question to be about this policy; do not claim mixed choices.
    menu = re.fullmatch(r'(.+?)\s*\(([^()]*)\)\s*(?:[을를]\s*)?(?:정|선택|결정)해\s*주세요[.?]?', label)
    if menu:
        options = [item.strip() for item in re.split(r'[,·/]', menu[2])]
        if len(options) == len(CHOICES['duplicate.policy']) and set(options) == CHOICES['duplicate.policy'].keys():
            subject = menu[1]
            tables = [r for r in (state or {}).get('resources', []) if r.get('kind') == 'table']
            if len(tables) == 1:
                for column in tables[0].get('columns', []):
                    subject = re.sub(r'''["'“‘]''' + re.escape(column) + r'''["'”’]\s*(?:열|컬럼)\s*기준(?:으로)?''',
                                     ' ', subject)
            if re.fullmatch(r'\s*중복(?:된|되는)?\s*(?:행|키)?(?:을|를|은|는)?\s*'
                            r'(?:어떻게\s*처리할지|처리\s*(?:정책|방식)(?:을|를)?)\s*', subject):
                return 'duplicate.policy'
        return fallback
    policies = policy_clauses(label, ())
    if (len(policies) == 1 and policies[0][0] == 'duplicate.policy'
            and '중복' in label and not re.search(r'열\s*(?:이름|선택)|파일|수신자|예약|빈\s*(?:값|셀)', label)
            and (len(re.findall(r'[가-힣]+지(?=[,\s]|$)', label)) >= 2 or '정책' in policies[0][2])):
        return 'duplicate.policy'
    return fallback


def _serialized_quote_fragment(text, source_quotes):
    """An echoed assessment field is model metadata rather than a user-facing condition."""
    head = re.match(r'^\s*"?user_quotes"\s*:\s*', text)
    if not head:
        return False
    rest = text[head.end():]
    try:
        quotes, end = json.JSONDecoder().raw_decode(rest)
    except ValueError:
        return False
    remainder = rest[end:].lstrip()
    if not remainder.startswith('}'):
        return False
    tail = remainder[1:].strip()
    if tail and (not re.match(r'^(?:\*\*)?(?:이유|근거|설명|reason)\s*[:：](?:\*\*)?', tail, re.I)
                 or re.search(r'[?？]|(?:해|어|려)\s*주(?:세요|십시오)', tail)):
        return False  # An unclassified tail can contain a second user decision.
    sources = [''.join(s.split()) for s in source_quotes]
    return (isinstance(quotes, list) and bool(quotes)
            and all(isinstance(q, str) and q.strip() and any(''.join(q.split()) in s for s in sources)
                    for q in quotes))


def make_capability_check(model,transport=None,mode=None,error_observer=None,api_key=''):
    setting=mode or os.getenv('A4B_CAPABILITY_MODE','off')
    if setting not in ('off','enforce'):raise ValueError('A4B_CAPABILITY_MODE는 off/enforce이어야 합니다.')
    def check(state):
        from agent_builder.design.gmail_slack import gmail_slack_assessment, gmail_slack_candidate
        if setting=='off' and not capability_enforced(state) and not gmail_slack_candidate(state):
            return {'capability_route':'conditions','capability_assessment':None,'capability_enforced':False,
                    'capability_fingerprint':None,'capability_questions':[],'capability_deferred':False}
        deferred=False;reuse_questions=False
        try:
            from agent_builder.conditions.condition_readiness import input_issues
            from agent_builder.design.request_meaning import gmail_importance, IMPORTANCE_QUESTION
            faults=input_issues(state)
            events=state.get('capability_answers',[])
            event=events[-1] if events and events[-1]['turn']==len(state.get('messages',[]))-1 else None
            previous=state.get('capability_assessment') or {}
            defer_previous = event and previous.get('decision')=='clarify' and event['kind'] in ('unknown','defer','guidance')
            if defer_previous:
                validate_assessment_decision(previous)
            criterion, evidence = gmail_importance(state)
            if faults:result=assess_capability(state,None)
            elif (gmail_slack := gmail_slack_assessment(state)):
                result = validate_assessment_result(gmail_slack, state)
            elif criterion == 'ambiguous':
                result = {'decision':'clarify', 'tool_ids':[], 'missing_actions':[],
                          'missing_conditions':[IMPORTANCE_QUESTION], 'user_quotes':[evidence],
                          'reason':'사용자가 선택할 메일의 중요도 기준이 미정입니다.',
                          'model_calls':0, 'allows_execution':False, 'allows_generation':False}
                # Retain the question ID when a reply defers the same decision.
                if event and previous.get('missing_conditions') == [IMPORTANCE_QUESTION]:
                    reuse_questions = True
                    deferred = event['kind'] == 'defer' or sum(
                        e['kind'] == 'unknown' and set(e['question_ids']).intersection(event['question_ids'])
                        != set() for e in events) >= 2
            elif (previous.get('decision')=='existing_tools' and (latest_resolution(state) or option_answer(state) or numeric_answer(state) or pending_nonanswer(state))
                  and state.get('messages', [])[-1].get('role')=='user'
                  and state.get('capability_fingerprint')==input_fingerprint({**state,'messages':state['messages'][:-1]})):
                # A non-answer or an offered closed-vocabulary choice for a condition question
                # changes no supported-action facts. Reuse only with the original
                # request/resources/catalog intact; condition_check still validates the slots.
                validate_assessment_result(previous,state)
                resolution = 'condition_choice' if option_answer(state) or numeric_answer(state) else 'condition_nonanswer'
                result = guard_delivery_revision({**previous, 'model_calls': 0, 'answer_resolution': resolution}, state)
            elif (defer_previous
                  and state.get('capability_fingerprint')==input_fingerprint({**state,'messages':state['messages'][:-1]})):
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
                result=_without_ungrounded_actions(state,active,_without_settled_conditions(state,active,
                    _without_generic_questions(state,active,assess_capability(state,active))))
            if result['decision'] == 'clarify':
                missing = result['missing_conditions']
                source_quotes = result.get('user_quotes', [])
                rejected = [i for i, text in enumerate(missing) if _serialized_quote_fragment(text, source_quotes)]
                if rejected:
                    remaining = [text for i, text in enumerate(missing) if i not in rejected]
                    if not remaining:
                        raise ValueError('지원 판단 응답에 질문할 업무 조건이 없습니다.')
                    result = {**result, 'missing_conditions':remaining, 'rejected_metadata_condition_indices':rejected}
        except Exception as error:
            if error_observer:error_observer(error)
            result={'decision':'system_error','issues':['도구 지원 판단을 완료하지 못했습니다.'], 'error_type':type(error).__name__,'model_calls':None}
            if isinstance(error,AssessmentConflictError):
                result.update(issues=['모델의 지원 판정과 누락 항목이 서로 맞지 않습니다. 요청을 보존하고 생성·저장을 중단했습니다.'],
                              assessment_conflict=error.reported,model_calls=error.model_calls)
        from agent_builder.design.execution_mode import reconcile_manual_capability
        result=reconcile_manual_capability(result,state)
        fingerprint=input_fingerprint(state)
        decision=result['decision']
        update={'capability_route':'conditions','capability_assessment':result,'capability_enforced':True,'system_failure':None,
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
            from agent_builder.design.execution_mode import filter_execution_questions
            questions=filter_execution_questions(questions,state)
            if not questions:
                questions=[{'id':hashlib.sha256((fingerprint+':'+str(i)+':'+text).encode()).hexdigest()[:24],
                            'key':'gmail.importance_criteria' if text == IMPORTANCE_QUESTION else _question_key(text,'capability.condition.'+str(i),state),
                            'source':'capability','text':_question_text(text),
                            'options':[]} for i,text in enumerate(result['missing_conditions'])]
                from agent_builder.conditions.condition_readiness import QUESTIONS, choice_options
                for question, raw in zip(questions, result['missing_conditions']):
                    if question['key'] == 'duplicate.policy':
                        question['options'] = choice_options('duplicate.policy', state)
                        if raw == 'duplicate.policy':
                            question['text'] = QUESTIONS['duplicate.policy']
                    elif raw == 'delivery.recipient':
                        question.update(text='이메일을 받을 수신자 주소는 무엇인가요?', options=[])
            from agent_builder.design.question_controls import complete_questions
            questions=complete_questions(questions,state)
            # Keep the full missing list in the assessment; controls and answer IDs
            # describe only this turn's two questions. Reassess remaining items after the answer.
            questions=questions[:2]
            ledger={'decision':'clarify','questions':questions,'slots':{}}
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
            # A model/contract failure (error_type set) is retryable and is not a user decision.
            from agent_builder.conditions.clarification_core import system_failure, RETRY_NOTICE
            failure=system_failure('capability_check',result['error_type'],' '.join(issues)) if decision=='system_error' and result.get('error_type') else None
            update.update(capability_route='stop',status='validation_failed',confirmed=False,saved_agent_id=None,gap_condition_review=None,
                pending_questions=[],capability_questions=[],issues=issues,semantic_issues=[],system_failure=failure,
                messages=state.get('messages',[])+[{'role':'assistant','content':' '.join(issues)+(RETRY_NOTICE if failure else '')}])
        return update
    return check


def await_capability_node(state):
    from langgraph.types import interrupt
    from agent_builder.design.question_controls import complete_questions
    questions=complete_questions(state['capability_questions'],state)
    payload={'kind':'clarification','questions':[q['text'] for q in questions],
             'question_records':questions,'scope':'capability','deferred':state.get('capability_deferred',False)}
    while True:
        answer=interrupt(payload)
        if answer.get('type')!='message' or not str(answer.get('content','')).strip():
            payload={**payload,'error':'지원 판단의 확인 질문에 답변해주세요. 일반 저장 확인으로 넘길 수 없습니다.'}
            continue
        if answer.get('question_answers'):
            from agent_builder.design.question_answers import question_answers
            try:
                answer={**answer,'question_answers':question_answers(answer['question_answers'],{**state,'pending_questions':questions})}
            except ValueError as error:
                payload={**payload,'error':str(error)}
                continue
            break
        if not answer.get('question_id') or answer['question_id'] in [q['id'] for q in questions]:break
        payload={**payload,'error':'현재 대화의 확인 질문에 답변해주세요.'}
    event={'turn':len(state['messages']),'answer':answer['content'],'kind':answer_kind(answer['content']),
           'question_ids':[answer['question_id']] if answer.get('question_id') else [q['id'] for q in questions]}
    if answer.get('question_answers'):
        event['answers']=answer['question_answers']
        event['question_ids']=[item['question_id'] for item in answer['question_answers']]
    questions_by_id = {q['id']:q for q in questions}
    event['questions'] = [questions_by_id[qid] for qid in event['question_ids']]
    event['candidate_keys'] = [
        _question_key(q['text'], q['key'], state) if q['key'].startswith('capability.condition.') else q['key']
        for q in event['questions']
    ]
    return {'messages':state['messages']+[{'role':'user','content':answer['content']}],
            'capability_answers':state.get('capability_answers',[])+[event], 'confirmed':False,'saved_agent_id':None}
