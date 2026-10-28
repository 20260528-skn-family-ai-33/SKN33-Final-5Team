from ..registry import TOOLS

def resources_node(state):
    return {'tool_gap':None,'trace':state.get('trace',[])+[{'node':'resources','label':'자료·도구 확인','status':'completed','detail':f"연결 자료 {len(state['resources'])}개와 허용 도구 {len(TOOLS)}개를 확인했습니다."}]}
