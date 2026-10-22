from .validation import validate_node

def make_save_node(storage):
    def save_node(state):
        if state.get('verification_enforced') and state.get('verification_route')!='continue_review':
            raise ValueError('검증 보류 상태의 초안은 저장할 수 없습니다.')
        checked=validate_node(state)
        if not state.get('confirmed') or checked['status']!='ready':raise ValueError('확인·검증을 통과한 설정만 저장할 수 있습니다.')
        id=storage.save_agent(state)
        return {'saved_agent_id':id,'status':'saved','trace':state.get('trace',[])+[{'node':'save','label':'에이전트 저장','status':'completed','detail':'설정 버전을 DB에 저장했습니다.'}]}
    return save_node
