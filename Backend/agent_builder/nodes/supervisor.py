def supervisor_node(state):
    if state.get('verification_enforced'):
        decision=state.get('verification_route')
        if decision in ['manual_review','unsupported']:
            return {'route':'invalid','status':'validation_failed','trace':state.get('trace',[])+[{'node':'supervisor','label':'검증 보류','status':'needs_input','detail':'검증 결과를 확인한 후 수정 요청을 보내세요. 실행·저장은 승인되지 않았습니다.'}]}
        if decision in ['repair_plan','ask_user']:
            if decision=='ask_user' and (state.get('draft') or {}).get('questions'):
                return {'route':'await_input'}
            return {'route':'repair' if state.get('repair_count',0)<2 else 'invalid',
                    'status':'needs_clarification' if state.get('repair_count',0)<2 else 'validation_failed'}
    route='unsupported' if state['status']=='unsupported' else 'await_input' if (state.get('draft') or {}).get('questions') else 'repair' if state.get('issues') and state.get('repair_count',0)<2 else 'invalid' if state.get('issues') else 'unsupported' if state['status']=='unsupported' else 'await_input' if state['status']=='needs_clarification' else 'save' if state.get('confirmed') else 'await_approval'
    return {'route':route,**({'status':'validation_failed'} if route=='invalid' else {}),'trace':state.get('trace',[])+[{'node':'supervisor','label':'다음 단계 결정','status':'completed','detail':{'repair':'설정 검증 오류를 한 번 바로잡습니다.','invalid':'검증하지 못한 설정은 저장을 막고 오류를 표시합니다.','unsupported':'지원 범위를 안내합니다.','await_input':'필수 정보가 부족해 답변을 기다립니다.','await_approval':'검증을 통과해 사용자 확인을 기다립니다.','save':'사용자가 확인한 설정을 저장합니다.'}[route]}]}
