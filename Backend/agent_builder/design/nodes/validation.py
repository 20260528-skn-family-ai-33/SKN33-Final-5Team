import json
from contracts.models import AgentDraft

from workflow_engine.workflow import workflow_errors

def validate_node(state):
    draft=AgentDraft.model_validate({k:v for k,v in state['draft'].items() if k!='node_snapshots'}).model_dump();issues=list(state.get('semantic_issues',[]))
    # Shared final guard for every planner/template: a question the user already settled
    # is not shown. Removing it never makes a question-only draft ready.
    from agent_builder.conditions.question_contract import drop_settled
    draft,settled=drop_settled(draft,state)
    if settled and not draft.get('workflow') and not draft['questions'] and not draft['unsupported']:
        issues.append('이미 사용자가 정한 조건만 다시 물은 응답이라 초안을 다시 설계해야 합니다.')
    from agent_builder.design.execution_mode import filter_draft_execution_questions
    draft=filter_draft_execution_questions(draft,state)
    from agent_builder.design.question_language import validate_draft_question_language
    try:draft=validate_draft_question_language(draft,state)
    except ValueError as error:issues.append(str(error))
    from agent_builder.conditions.condition_readiness import literal_condition_issues, written_comparison_issues
    issues.extend(literal_condition_issues(state))
    issues.extend(written_comparison_issues(state, draft))
    from agent_builder.design.explicit_table_criteria import explicit_draft_issues
    issues.extend(explicit_draft_issues(state,draft))
    resources={r['id']:r for r in state['resources']};texts=[m['content'] for m in state['messages'] if m['role']=='user']
    if not draft['name'].strip(): issues.append('에이전트 이름이 필요합니다.')
    from agent_builder.design.name_policy import invalid_workflow_name
    if draft.get('workflow') and invalid_workflow_name(draft['name']):
        issues.append('도구 식별자만으로는 저장할 수 없습니다. 사용자 업무를 설명하는 이름으로 바꿔주세요.')
    if not draft['purpose'].strip(): issues.append('어떤 업무를 맡길지 알려주세요.')
    if not draft['instructions'].strip(): issues.append('에이전트의 지시사항이 필요합니다.')
    if not draft['tool_ids'] and not draft['questions'] and not draft['unsupported']: issues.append('처리할 업무에 맞는 도구를 연결해야 합니다.')
    if len(set(draft['tool_ids']))!=len(draft['tool_ids']): issues.append('도구 연결이 중복되었습니다.')
    for id in draft['document_ids']:
        if id not in resources: issues.append('초안에 이번 대화에 연결되지 않은 자료가 포함됐습니다.')
    if not draft['requirements'] and not draft['questions'] and not draft['unsupported']: issues.append('업무 조건의 사용자 근거가 필요합니다.')
    for req in draft['requirements']:
        if not req['quote'].strip():issues.append('업무 조건에 근거 원문이 비어 있습니다.');continue
        if req['source_id']=='user': valid=any(req['quote'] in t for t in texts)
        else:
            source=resources.get(req['source_id'])
            evidence=(source.get('text','') or '\n'.join(['파일명: '+source['name'],'열: '+', '.join(source.get('columns',[]))]+[k+': '+json.dumps(v,ensure_ascii=False,sort_keys=True) for k,v in source.get('profile',{}).items()])) if source else ''
            valid=bool(source and (req['quote'] in evidence or req['quote'] in source.get('columns',[])))
        if not valid: issues.append('업무 조건의 인용 원문을 확인하지 못했습니다: '+req['description'])
    if draft.get('workflow'):
        from workflow_engine.library.node_library import apply_refs
        try:draft['workflow']=apply_refs(draft['workflow'],state.get('node_library',[]),state.get('required_node_refs',[]))
        except ValueError as error:issues.append(str(error))
        if 'saved_workflows' in state:
            for n in draft['workflow']['nodes']:
                if n['tool']=='saved_workflow':
                    settings=n['settings'];entry=next((a for a in state['saved_workflows'] if a['id']==settings['agent_id'] and a['version']==settings['agent_version']),None)
                    if not entry or not any(o['node_id']==settings['output_node'] and o['port']==settings['output_port'] for o in entry['outputs']):issues.append('선택할 수 있는 저장된 워크플로우·출력이 아닙니다: '+n['id'])
        issues.extend(workflow_errors(draft['workflow'],draft['tool_ids'],state['resources'],draft['execution_plan']))
    elif state.get('required_node_refs'):
        issues.append('선택한 노드를 연결할 워크플로우가 필요합니다.')
    from agent_builder.design.request_guards import request_guard_issues
    issues.extend(request_guard_issues(state, draft))
    if draft['execution_plan']['output_format']=='xlsx' and not draft['workflow'] and not (draft['questions'] or draft['unsupported']):
        issues.append('엑셀 출력에는 실제 XLSX 파일을 만드는 워크플로우가 필요합니다.')
    if not draft.get('workflow') and any(id in draft['tool_ids'] for id in ['filter_sort','write_result']) and 'read_table' not in draft['tool_ids']:issues.append('자료 처리·결과 생성 전에 표 파일 읽기 도구가 필요합니다.')
    filters=[n for n in (draft.get('workflow') or {}).get('nodes',[]) if n['tool']=='filter_sort' and not n['settings'].get('operation')]
    plans=[n['settings'].get('execution_plan') or draft['execution_plan'] for n in filters]
    if not draft.get('workflow'):plans=[draft['execution_plan']]
    elif not filters:
        gp=draft['execution_plan']
        if gp['filters'] or gp['sort'] or gp['duplicate_policy'] not in ['keep','ask'] or gp['missing_policy']=='exclude':
            issues.append('실행 조건을 적용할 filter_sort 노드가 워크플로우에 필요합니다.')
    tables=[r for r in state['resources'] if r.get('kind')=='table' and r['id'] in draft['document_ids']]
    questions=list(draft['questions'])
    from agent_builder.design.execution_mode import validate_execution_mode
    issues.extend(validate_execution_mode(state,draft,questions))
    if draft.get('schedule') and not draft.get('report_destination'):
        draft['report_destination']='reports'
    for plan in plans:
        transform=bool(plan['filters'] or plan['sort'] or plan['duplicate_policy'] not in ['keep','ask'] or plan['missing_policy']=='exclude')
        if transform and 'filter_sort' not in draft['tool_ids']:issues.append('실행 조건을 적용할 조건 처리 도구가 필요합니다.')
        touched=set(f['field'] for f in plan['filters'] if f['operator'] not in ['is_empty','not_empty'])|set(f['compare_field'] for f in plan['filters'] if f['compare_field'])|set(s['field'] for s in plan['sort'])
        if not draft.get('workflow'):
            fields=list(touched)+([plan['duplicate_field']] if plan['duplicate_field'] else [])
            if tables and fields and not any(all(f in t['columns'] for f in fields) for t in tables):issues.append('실행 조건에 실제 자료에 없는 열이 포함됐습니다.')
        if plan['missing_policy']=='ask' and any(t.get('profile',{}).get(f,{}).get('missing',0) for t in tables for f in touched):
            if not questions:issues.append('조건 열의 빈 값 처리 정책이 미정입니다. 사용자에게 확인해야 합니다.')
        if plan['duplicate_policy'] in ['first','last','error','ask'] and not plan['duplicate_field']:issues.append('중복 처리를 판단할 기준 열이 필요합니다.')
        if plan['duplicate_policy']=='ask' and not questions:issues.append('요청한 중복 처리 정책이 미정입니다. 사용자에게 확인해야 합니다.')
        for f in plan['filters']:
            if f['compare_field'] and f['value'] is not None:issues.append('열 비교에는 상수 값을 함께 넣을 수 없습니다.')
            if not f['compare_field'] and f['operator'] not in ['is_empty','not_empty'] and f['value'] is None:issues.append('조건 비교에 필요한 기준 값이 없습니다.')
    if draft.get('schedule'):
        from workflow_engine.scheduling.schedules import schedule_errors
        from agent_builder.conditions.input_requirements import execution_requirements, execution_request_required
        if not questions:issues+=schedule_errors(draft['schedule'])
        class DesignResources:
            def asset(self, identifier):
                if identifier not in resources:raise ValueError('예약 실행에 사용할 자료를 연결하세요.')
                return resources[identifier]
        dependencies = state.get('workflow_dependencies') or {}
        for required in execution_requirements(draft, DesignResources(), dependencies=dependencies):
            if required['required_before_start'] and not required['ready']:
                issues += required['issues']
        # Pinned children are resolved at save/activation. A design catalogue alone
        # cannot establish their request inputs, so their final runtime gate owns it.
        has_unresolved_child = any(n['tool']=='saved_workflow' and n['id'] not in dependencies
                                   for n in (draft.get('workflow') or {}).get('nodes',[]))
        if not has_unresolved_child and execution_request_required(draft, dependencies):
            issues.append('예약 실행에 매번 입력해야 하는 사용자 요청이 필요합니다. 저장된 입력이나 앞 단계 결과를 연결하세요.')
        if draft.get('report_destination')!='reports':issues.append('예약 결과의 저장 위치가 미정입니다.')
        if draft.get('workflow') and any(n['tool']=='ask_user' for n in draft['workflow']['nodes']):issues.append('예약 실행에는 사용자 응답 대기 노드를 연결할 수 없습니다.')
    draft['questions']=list(dict.fromkeys(questions))[:3]
    from agent_builder.capabilities.capability_gate import capability_enforced, blocking_issue, draft_delivery_issue
    support_conflict=bool(draft['unsupported'] and capability_enforced(state) and blocking_issue(state) is None)
    if support_conflict:
        issues.append('생성기의 미지원 판정이 현재 요청의 도구 지원 판단과 충돌합니다. 초안을 다시 생성하거나 검토해야 합니다.')
    delivery_issue = None if draft['questions'] or draft['unsupported'] else draft_delivery_issue(state, draft)
    if delivery_issue:
        issues.append(delivery_issue)
    from agent_builder.design.processing_guard import processing_issues
    issues.extend(processing_issues(state, draft))
    from agent_builder.design.requirement_coverage import coverage_report
    coverage=(coverage_report(state,draft) if not draft['questions'] and not draft['unsupported']
              else dict(enabled=state.get('requirement_required',False),tasks=[],issues=[]))
    issues.extend(coverage['issues'])
    status=('validation_failed' if support_conflict or delivery_issue else 'unsupported' if draft['unsupported'] else
            'needs_clarification' if draft['questions'] or issues else 'ready')
    trace=state.get('trace',[])+[{'node':'validate','label':'설정·근거 검증',
        'status':'completed' if not issues else 'needs_input',
        'detail':'필수 설정·등록 도구·자료 범위·인용 원문을 검사했습니다.' if not issues else ' / '.join(issues)}]
    if coverage['enabled']:
        trace.append({'node':'requirement_coverage','label':'요청 업무 반영 검사',
            'status':'completed' if not coverage['issues'] else 'needs_input',
            'detail':f"{len(coverage['tasks'])}개 요구사항의 실행 설정·데이터 연결을 대조했습니다.",
            'tasks':coverage['tasks']})
    return {'draft':draft,'requirement_coverage':coverage,'issues':list(dict.fromkeys(issues)),'status':status,'trace':trace}

