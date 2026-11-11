def _repair_pause(state):
    from agent_builder.design.clarification import clarification_pause
    findings=' / '.join(state.get('issues') or ['설계와 검사 결과가 일치하지 않습니다.'])[:1400]
    update=clarification_pause(state,'design_review',
        ['원래 요청을 유지해 다시 설계할까요? 변경할 내용이 있으면 알려주세요.'],
        detail='자동 수정으로 해결하지 못한 내용: '+findings,
        preserve_draft=bool(state.get('draft') and not state['draft'].get('unsupported')))
    return {**update,'route':'await_input'}


def supervisor_node(state):
    # Invalid provider/contract/source data is not a business question. Preserve
    # the diagnostic and wait for an explicit retry, never plan from that data.
    failures=[stage for stage,key in (('지원 판정','capability_route'),('조건 검사','condition_route'),
                                     ('요구사항 검사','requirement_route')) if state.get(key)=='stop']
    if failures and state.get('status')=='validation_failed':
        return {'route':'invalid','confirmed':False,'saved_agent_id':None,
                'trace':state.get('trace',[])+[dict(node='supervisor',label='검사 오류 처리',status='needs_input',
                    detail=' / '.join(failures)+'의 오류를 확인하세요. 요청은 보존되며 수정 후 다시 시도할 수 있습니다.')]}
    from agent_builder.capabilities.capability_gate import blocking_issue
    issue=blocking_issue(state)
    if issue:
        # A known missing capability may be recorded, never approved or saved.
        from agent_builder.capabilities.capability_gate import input_fingerprint
        assessment=state.get('capability_assessment') or {}
        if (assessment.get('decision')=='manual_review'
                and state.get('capability_fingerprint')==input_fingerprint(state)
                and state.get('capability_questions') and state.get('status')=='needs_clarification'):
            from agent_builder.capabilities.capability_judgement import validate_assessment_result
            from agent_builder.conditions.condition_readiness import input_issues
            try:validate_assessment_result(assessment,state)
            except ValueError:pass
            else:
                if not input_issues(state):
                    return {'route':'await_capability','confirmed':False}
        missing=((state.get('capability_assessment') or {}).get('decision')=='missing_capability'
                 and state.get('capability_fingerprint')==input_fingerprint(state)
                 and state.get('status')=='unsupported')
        return {'route':'unsupported' if missing else 'invalid',
                'status':'unsupported' if missing else 'validation_failed','confirmed':False,
                'trace':state.get('trace',[])+[{'node':'supervisor','label':'지원 판단 유지',
                    'status':'needs_input','detail':issue}]}
    if state.get('condition_template') and state.get('issues'):
        return _repair_pause(state)
    if state.get('verification_enforced'):
        decision=state.get('verification_route')
        if decision in ['manual_review','unsupported']:
            verification=state.get('verification') or {}
            output=verification.get('model_output') or {}
            if decision=='manual_review' and not verification.get('error_code') and output.get('missing_conditions'):
                from agent_builder.design.clarification import clarification_pause
                questions=[m['detail'] for m in output['missing_conditions'] if m.get('detail')]
                if questions:
                    return {**clarification_pause(state,'verification',questions,
                        detail='검토에서 확인이 필요한 업무 조건이 있습니다.',preserve_draft=True),'route':'await_input'}
            return {'route':'invalid','status':'validation_failed','trace':state.get('trace',[])+[{'node':'supervisor','label':'검증 보류','status':'needs_input','detail':'검증 결과를 확인한 후 수정 요청을 보내세요. 실행·저장은 승인되지 않았습니다.'}]}
        if decision in ['repair_plan','ask_user']:
            if decision=='ask_user' and (state.get('draft') or {}).get('questions'):
                return {'route':'await_input'}
            if decision=='ask_user':
                from agent_builder.design.clarification import clarification_pause
                output=(state.get('verification') or {}).get('model_output') or {}
                questions=[m['detail'] for m in output.get('missing_conditions',[]) if m.get('detail')]
                return {**clarification_pause(state,'verification',questions or
                    ['검토에서 조건을 확정하지 못했습니다. 적용할 기준이나 변경할 내용을 알려주세요.'],
                    detail='설계 검토에서 추가 확인을 요청했습니다.',preserve_draft=True),'route':'await_input'}
            return {'route':'repair','status':'needs_clarification'} if state.get('repair_count',0)<2 else _repair_pause(state)
    if (state.get('draft') or {}).get('unsupported') and state.get('issues') and (state.get('capability_assessment') or {}).get('decision')=='existing_tools':
        return {'route':'repair'} if state.get('repair_count',0)<2 else _repair_pause(state)
    if state.get('issues') and state.get('repair_count',0)>=2 and not (state.get('draft') or {}).get('questions'):
        return _repair_pause(state)
    route='unsupported' if state['status']=='unsupported' else 'await_input' if (state.get('draft') or {}).get('questions') else 'repair' if state.get('issues') and state.get('repair_count',0)<2 else 'invalid' if state.get('issues') else 'await_input' if state['status']=='needs_clarification' else 'save' if state.get('confirmed') else 'await_approval'
    return {'route':route,**({'status':'validation_failed'} if route=='invalid' else {}),'trace':state.get('trace',[])+[{'node':'supervisor','label':'다음 단계 결정','status':'completed','detail':{'repair':'설정 검증 오류를 한 번 바로잡습니다.','invalid':'검증하지 못한 설정은 저장을 막고 오류를 표시합니다.','unsupported':'지원 범위를 안내합니다.','await_input':'필수 정보가 부족해 답변을 기다립니다.','await_approval':'검증을 통과해 사용자 확인을 기다립니다.','save':'사용자가 확인한 설정을 저장합니다.'}[route]}]}
