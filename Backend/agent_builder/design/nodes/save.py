from agent_builder.design.nodes.validation import validate_node

def make_save_node(storage):
    def save_node(state):
        from agent_builder.capabilities.capability_gate import require_save_ready
        require_save_ready(state)
        # The choice controls persistence of an optional reservation. Validate
        # the complete reviewed draft before applying that choice.
        checked=validate_node({key:value for key,value in state.items() if key!='include_schedule'})
        if not state.get('confirmed') or checked['status']!='ready':raise ValueError('확인·검증을 통과한 설정만 저장할 수 있습니다.')
        id=storage.save_agent({**state,'draft':checked['draft']})
        saved=storage.agent(id)['config']
        selection={}
        if state.get('include_schedule') is False and checked['draft'].get('schedule'):
            selection['messages']=state.get('messages',[])+[{'role':'user','content':'예약은 추가하지 않고 워크플로우만 저장합니다.'}]
        return {'saved_agent_id':id,'draft':saved,'status':'saved',**selection,'trace':state.get('trace',[])+[{'node':'save','label':'에이전트 저장','status':'completed','detail':'설정 버전을 DB에 저장했습니다.'}]}
    return save_node
