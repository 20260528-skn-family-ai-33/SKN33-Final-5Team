from langgraph.types import interrupt

def await_input_node(state):
    pending=state.get('pending_questions',[]) if state.get('condition_enforced') else []
    payload={'kind':'clarification','questions':state['draft']['questions']+state['issues']}
    if pending:payload['question_records']=pending
    while True:
        answer=interrupt(payload)
        if answer.get('type')!='message' or not str(answer.get('content','')).strip():
            payload={**payload,'error':'추가 질문에 답변해주세요.'}
            continue
        if not answer.get('question_id') or answer['question_id'] in [q['id'] for q in pending]:break
        payload={**payload,'error':'현재 대화의 확인 질문에 답변해주세요.'}
    result={'messages':state['messages']+[{'role':'user','content':answer['content']}],'confirmed':False}
    if pending:
        result['condition_answers']=state.get('condition_answers',[])+[{'question_ids':[answer['question_id']] if answer.get('question_id') else [q['id'] for q in pending],
            'candidate_keys':[q['key'] for q in pending if not answer.get('question_id') or q['id']==answer['question_id']],
            'answer':answer['content'],'turn':len(state['messages'])}]
    return result

def await_approval_node(state):
    payload={'kind':'review','draft':state['draft']}
    while True:
        answer=interrupt(payload)
        if answer.get('type')=='confirm':
            from ..capability_gate import blocking_issue
            issue=blocking_issue(state)
            if issue:
                payload={**payload,'error':issue}
                continue
            return {'confirmed':True}
        if answer.get('type')=='message' and str(answer.get('content','')).strip():
            return {'messages':state['messages']+[{'role':'user','content':answer['content']}],'confirmed':False}
        payload={**payload,'error':'설정을 확인하거나 변경할 내용을 입력해주세요.'}
