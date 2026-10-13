from langgraph.types import interrupt

def await_input_node(state):
    answer=interrupt({'kind':'clarification','questions':state['draft']['questions']+state['issues']})
    if answer.get('type')!='message' or not str(answer.get('content','')).strip(): raise ValueError('추가 질문에 답변해주세요.')
    return {'messages':state['messages']+[{'role':'user','content':answer['content']}],'confirmed':False}

def await_approval_node(state):
    answer=interrupt({'kind':'review','draft':state['draft']})
    if answer.get('type')=='confirm':return {'confirmed':True}
    if answer.get('type')=='message' and str(answer.get('content','')).strip():return {'messages':state['messages']+[{'role':'user','content':answer['content']}],'confirmed':False}
    raise ValueError('설정을 확인하거나 변경할 내용을 입력해주세요.')
