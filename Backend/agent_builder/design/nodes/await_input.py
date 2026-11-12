from langgraph.types import interrupt


_REQUIREMENT_SOURCES = ('requirement', 'requirements')


def await_input_node(state):
    from agent_builder.design.question_controls import project_question_controls
    pending=[q for q in project_question_controls(state)['pending_questions']
             if state.get('condition_enforced') or q.get('source') in ('receipt', *_REQUIREMENT_SOURCES)]
    payload={'kind':'clarification','questions':state['draft']['questions']+state['issues']}
    if pending:payload['question_records']=pending
    while True:
        answer=interrupt(payload)
        if answer.get('type')!='message' or not str(answer.get('content','')).strip():
            payload={**payload,'error':'추가 질문에 답변해주세요.'}
            continue
        if answer.get('question_answers'):
            from agent_builder.design.question_answers import question_answers
            try:
                parsed=question_answers(answer['question_answers'],state)
                if any(item['question_id'] not in {q['id'] for q in pending} for item in parsed):
                    raise ValueError('현재 대화의 확인 질문에 답변해주세요.')
                answer={**answer,'question_answers':parsed}
            except ValueError as error:
                payload={**payload,'error':str(error)}
                continue
            break
        if not answer.get('question_id') or answer['question_id'] in [q['id'] for q in pending]:break
        payload={**payload,'error':'현재 대화의 확인 질문에 답변해주세요.'}
    result={'messages':state['messages']+[{'role':'user','content':answer['content']}],'confirmed':False,'clarification_help':False}
    if not answer.get('question_answers') and not answer.get('question_id'):
        from agent_builder.design.question_help import question_help
        explanation = question_help(state, answer['content'])
        if explanation:
            return {**result, 'clarification_help': True,
                    'messages': result['messages'] + [{'role':'assistant','content':explanation}]}
    if answer.get('question_answers'):
        from agent_builder.design.question_answers import answer_text
        by_id={q['id']:q for q in pending}
        chosen=[(by_id[item['question_id']],item) for item in answer['question_answers']]
        events=[{'questions':[question],'question_ids':[question['id']],'candidate_keys':[question['key']],
                 'answer':'선택사항 건너뛰기' if item.get('skip') else answer_text(item['value']),'value':item['value'],
                 **({'skipped':True} if item.get('skip') else {}),
                 'message_content':answer['content'],'turn':len(state['messages'])}
                for question,item in chosen if question.get('source') in _REQUIREMENT_SOURCES]
        if events:result['requirement_answers']=state.get('requirement_answers',[])+events
        conditions=[(question,item) for question,item in chosen if question.get('source','condition')=='condition']
        if conditions and state.get('condition_enforced'):
            result['condition_answers']=state.get('condition_answers',[])+[{
                'questions':[question for question,_ in conditions],
                'question_ids':[question['id'] for question,_ in conditions],
                'candidate_keys':[question['key'] for question,_ in conditions],
                'answer':answer['content'],'turn':len(state['messages']),
                'answers':[{'question_id':question['id'],'key':question['key'],'value':item['value'],
                            'answer':answer_text(item['value'])} for question,item in conditions]}]
        return result
    selected=[q for q in pending if not answer.get('question_id') or q['id']==answer['question_id']]
    # Source-less checkpoints belong to conditions. A free-text reply spanning
    # multiple owners remains in the conversation until meaning is validated.
    owners={'requirement' if q.get('source') in _REQUIREMENT_SOURCES else q.get('source','condition') for q in selected}
    if owners=={'requirement'} or owners=={'condition'} and state.get('condition_enforced'):
        channel='requirement_answers' if owners=={'requirement'} else 'condition_answers'
        event={'questions':selected,'question_ids':[q['id'] for q in selected],
               'candidate_keys':[q['key'] for q in selected],'answer':answer['content'],
               'message_content':answer['content'],'turn':len(state['messages'])}
        result[channel]=[*state.get(channel,[]),event]
    return result


def await_approval_node(state):
    payload={'kind':'review','draft':state['draft']}
    while True:
        answer=interrupt(payload)
        if answer.get('type')=='confirm':
            from agent_builder.capabilities.capability_gate import blocking_issue
            issue=blocking_issue(state)
            if issue:
                payload={**payload,'error':issue}
                continue
            if 'include_schedule' in answer and type(answer['include_schedule']) is not bool:
                payload={**payload,'error':'예약을 함께 저장할지 선택해주세요.'}
                continue
            return {'confirmed':True,**({'include_schedule':answer['include_schedule']} if 'include_schedule' in answer else {})}
        if answer.get('type')=='message' and str(answer.get('content','')).strip():
            return {'messages':state['messages']+[{'role':'user','content':answer['content']}],'confirmed':False}
        payload={**payload,'error':'설정을 확인하거나 변경할 내용을 입력해주세요.'}
