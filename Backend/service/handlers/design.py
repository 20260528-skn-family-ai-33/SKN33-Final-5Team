"""A1 design-session jobs: conversation, uploads, confirm/save and tool-gap choices.

Every action here loads the session's LangGraph conversation (build_graph) first.
"""
from infrastructure.persistence.ids import uid, now
from agent_builder.design.graph import build_graph
from agent_builder.design.receipt_intake import current_receipt_questions, legacy_receipt_intake, receipt_upload_prompt
from agent_builder.design.question_controls import project_question_controls
from documents.resources.file_inspection import inspect_file
from agent_builder.conditions.input_requirements import design_requirements
from agent_builder.tool_gap.gap import describe_gap, export_gap
from agent_builder.tool_gap.choice import present_choice, choose, redesign_context
from agent_builder.tool_gap import jobs as tool_gap_jobs, redesign as tool_gap_redesign
from langgraph.types import Command
from agent_builder.design.node_edit_copy import public_request, public_error, is_node_edit_error

# Actions that only read or update session state: the model conversation is never invoked.
STATE_ONLY={'session','upload','attach_asset','tool_gap','tool_gap_choice','tool_gap_job_claim','tool_gap_job_finish','tool_gap_job_recover','tool_gap_replan_propose','tool_gap_replan_decide'}

_QUESTION_WAIT_ROUTES = {
    ('await_input',): ('attachment_gate', 'attachment_route', 'ask'),
    ('await_capability',): ('capability_check', 'capability_route', 'await_capability'),
}


def _update_session_checkpoint(graph, config, update, next_nodes):
    route = _QUESTION_WAIT_ROUTES.get(next_nodes)
    if route and 'pending_questions' not in update:
        # A state-only update clears the interrupt. Keep the same question node scheduled.
        predecessor, key, value = route
        graph.update_state(config, {**update, key:value}, as_node=predecessor)
    else:
        graph.update_state(config, update)


def _recover_question_wait(state, next_nodes, answers):
    # Older attachment checkpoints could point back to the entry node with a valid question still open.
    if state.get('status') != 'needs_clarification' or next_nodes != ('resources',):
        return ()
    answered = {item['question_id'] for item in answers}
    capability_ids = {q['id'] for q in state.get('capability_questions', [])}
    if state.get('capability_route') == 'await_capability' and answered <= capability_ids:
        return ('await_capability',)
    from agent_builder.design.nodes.await_input import input_question_records
    if isinstance(state.get('draft'), dict) and answered <= {q['id'] for q in input_question_records(state)}:
        return ('await_input',)
    return ()


def _last_user(state):
    return next((m.get('content') for m in reversed(state.get('messages',[]) or []) if m.get('role')=='user'),None)


_FAILURE_KEYS=('system_failure','requirement_failure','issues','status')


def _rerun_from_gates(graph,config,state,restored):
    """Rerun the same request from the gates after a retryable failure.

    Both failure markers are cleared so a rerun that stops before the failed stage cannot
    leave a stale retry banner; the prior failure moves to previous_system_failure for
    attempt counting. If the rerun raises, the markers are put back so the session keeps
    offering a retry instead of sitting in 'processing'."""
    messages=state.get('messages',[])
    last=max((i for i,m in enumerate(messages) if m.get('role')=='user'),default=None)
    graph.update_state(config,{**restored,'messages':messages if last is None else messages[:last+1],
        'repair_count':0,'confirmed':False,
        'previous_system_failure':state.get('system_failure') or state.get('requirement_failure'),
        'system_failure':None,'requirement_failure':None,'issues':[],'status':'processing'},as_node='resources')
    try:
        graph.invoke(None,config)
    except Exception:
        graph.update_state(config,{**{key:state.get(key) for key in _FAILURE_KEYS},
                                   'status':state.get('status') or 'validation_failed'},as_node='resources')
        raise


def _work_messages(storage,s,graph):
    state=graph.get_state({'configurable':{'thread_id':s['id']}}).values or {}
    if not state and s.get('agent_id'):
        base=storage.agent(s['agent_id'])
        return (graph.get_state({'configurable':{'thread_id':base['session_id']}}).values or {}).get('messages',[])
    return state.get('messages',[])


def _waiting_for_answers(state,before):
    """A clarification question is open: a typed "저장" may be its answer (e.g. a column name)."""
    reviewing=any(isinstance(item.value,dict) and item.value.get('kind')=='review'
                  for task in before.tasks for item in task.interrupts)
    return not reviewing and bool(state.get('pending_questions') or (state.get('draft') or {}).get('questions'))


def _chat_save_message(data, snapshot):
    """Route save guidance with the same rules before and after a resource refresh."""
    from agent_builder.design.save_intent import is_save_request

    if any(data.get(key) is not None for key in ('question_id', 'question_answers', 'node_edit')):
        return False
    content = data.get('content')
    if not is_save_request(content):
        return False
    state = snapshot.values or {}
    if not _waiting_for_answers(state, snapshot):
        return True
    projected = project_question_controls(state)
    questions = projected['pending_questions']
    if not questions and projected['draft']:
        draft = projected['draft']
        questions = [{'options': options, 'response': response}
                     for options, response in zip(draft['question_options'], draft['question_responses'])]
    literal = content.strip()
    for question in questions:
        if literal in (question.get('options') or []):
            return False
        if literal == '저장' and (question.get('response') or {}).get('type', 'text') == 'text':
            return False
    return True


def _chat_save_reply(state,before):
    """What a typed save request gets: where to save, or why not yet. It never saves.

    Saving stays on the save button, which carries the approval checks, the schedule
    choice, the resource-version check and crash recovery (PR #142 review 5).
    """
    from agent_builder.design.save_intent import blocked_save_reply
    reviewing=any(isinstance(item.value,dict) and item.value.get('kind')=='review'
                  for task in before.tasks for item in task.interrupts)
    if not reviewing or state.get('status')!='ready':
        return blocked_save_reply(state)
    from agent_builder.capabilities.capability_gate import require_ready
    from agent_builder.conditions.clarification_core import open_required
    try:require_ready(state)
    except ValueError as error:return '아직 저장할 수 없습니다. '+str(error)
    if open_required(state):return blocked_save_reply({**state,'pending_questions':[True]})
    if (state.get('draft') or {}).get('schedule'):
        return '오른쪽 위 저장 버튼을 눌러 예약을 함께 저장할지 선택해 주세요.'
    return '초안을 확인했다면 오른쪽 위 저장 버튼을 눌러 주세요.'


def _with_side_messages(messages,s):
    """Display-only merge. Side remarks never enter graph messages, answers or fingerprints."""
    side=s.get('side_messages') or []
    if not side:return messages
    merged=[]
    for index in range(len(messages)+1):
        merged+=[{k:v for k,v in m.items() if k!='after'} for m in side if min(m['after'],len(messages))==index]
        if index<len(messages):merged.append(messages[index])
    return merged


def _public_node_edit_messages(messages):
    """Adapt saved popup transcripts for display without changing graph inputs."""
    result=[]
    legacy_request=None
    for message in messages:
        content=message.get('content')
        projected=content
        if message.get('role')=='user':
            projected=public_request(content)
            legacy_request=(content,projected) if projected!=content else None
        elif message.get('role')=='assistant':
            if is_node_edit_error(content):
                projected=public_error(content)
            elif (legacy_request and isinstance(content,str)
                  and ' 설정을 변경했습니다: ' in content
                  and content.endswith('다른 작업의 실행 설정은 유지했습니다. 저장 전에 변경된 값을 확인해 주세요.')):
                raw,friendly=legacy_request
                node_id=raw.split('\n',1)[0].rsplit(' (노드 ID: ',1)[1].split(')',1)[0]
                if content.startswith(node_id+' 설정을 변경했습니다: '):
                    lines=friendly.split('\n')
                    projected=(lines[0].removesuffix('의 설정을 다음과 같이 바꿔주세요.')+
                               '의 설정을 변경했습니다.\n'+'\n'.join(lines[1:-1])+
                               '\n저장 전에 변경된 값을 확인해 주세요.')
        result.append({**message,'content':projected} if projected!=content else message)
    return result


def _requirement_state(state):
    """The single requirement state, shown when orchestration is on (shadow/enforce).

    The projection itself never feeds a decision; with the default 'off' the response is unchanged.
    """
    from agent_builder.orchestration.criteria import mode as orchestration_mode
    if orchestration_mode()=='off':return {}
    try:
        from agent_builder.orchestration.state import shadow_view
        return {'requirement_state':shadow_view(state)}
    except Exception as error:  # a projection bug must not break the conversation
        return {'requirement_state':{'error':type(error).__name__,'detail':str(error)[:300]}}


def public_state(storage,s,graph):
    snapshot=graph.get_state({'configurable':{'thread_id':s['id']}})
    state=snapshot.values or {}
    if not state and s.get('agent_id'):
        base=storage.agent(s['agent_id']);old=(graph.get_state({'configurable':{'thread_id':base['session_id']}}).values or {})
        state={'draft':base['config'],'messages':old.get('messages',[]),'status':'editing'}
    if state.get('status') != 'saved' and any(r['id'] not in s['file_ids'] for r in state.get('resources', [])):
        state = {'messages':state.get('messages', []), 'status':'validation_failed',
                 'issues':['연결 자료가 삭제됐습니다. 업무 설명을 보내 남은 자료로 설정을 다시 확인하세요.']}
    from workflow_engine.scheduling.policy import project_fixed_timezone
    state=project_fixed_timezone(state)
    from agent_builder.design.failure_recovery import project_recovery_questions, normalize_active_failures
    state=project_recovery_questions(state)
    legacy = legacy_receipt_intake(state)
    if legacy:
        # Keep the recorded transcript; only adapt the controls for the interrupted turn.
        state = {**state, 'draft':legacy['draft'], 'pending_questions':legacy['pending_questions']}
    state=normalize_active_failures(state)
    from agent_builder.design.presentation import public_requirement_errors
    state=public_requirement_errors(state)
    from agent_builder.design.failed_design import retained_design
    state=retained_design(state,graph,{'configurable':{'thread_id':s['id']}},
                          (snapshot.config or {}).get('configurable',{}).get('checkpoint_id'))
    from agent_builder.capabilities.capability_gate import blocking_issue, capability_enforced
    issue=blocking_issue(state) if state.get('status')=='ready' else None
    resources=[storage.asset(id) for id in s['file_ids']]
    inspections=[inspect_file(asset) for asset in resources]
    controls=project_question_controls(state,current_receipt_questions(state,resources,inspections))
    visible_issues=[public_error(value) if is_node_edit_error(value) else value
                    for value in state.get('issues',[])+([issue] if issue else [])]
    visible_trace=[{**entry,'detail':public_error(entry.get('detail')) if is_node_edit_error(entry.get('detail'))
                    else '작업 설정을 변경했습니다.' if entry.get('status')=='completed' else entry.get('detail')}
                   if entry.get('node')=='node_edit' else entry for entry in state.get('trace',[])[-40:]]
    return {**s,'messages':_public_node_edit_messages(_with_side_messages(state.get('messages',[]),s)),'draft':controls['draft'],'status':'validation_failed' if issue else state.get('status','empty'),'issues':list(dict.fromkeys(visible_issues)),'trace':visible_trace,'verification':state.get('verification'),'saved_agent_id':state.get('saved_agent_id'),'resources':resources,
            'file_inspection':inspections,
            'condition_ledger':state.get('condition_ledger'),'pending_questions':controls['pending_questions'],
            'requirement_failure':state.get('requirement_failure'),
            'gap_condition_review':state.get('gap_condition_review'),
            'input_requirements':design_requirements(state) if state.get('condition_enforced') else [],
            'tool_gap':(export_gap(state,s) if describe_gap(state,s['id']) else None),
            'tool_gap_choice':present_choice(state,s), 'redesign_context':redesign_context(state,s),
            'tool_gap_job':tool_gap_jobs.current_job(storage,state,s),
            'capability_assessment':state.get('capability_assessment'),
            'capability_enforced':capability_enforced(state),
            'capability_deferred':state.get('capability_deferred',False),
            'system_failure':state.get('system_failure'),
            **_requirement_state(state),
            'redesign_proposal':tool_gap_redesign.view(state,s),
            'redesign_generation':tool_gap_redesign.generation_handoff(state,s)}


def new_session(c):
    data=c.data
    return c.storage.new_session(data['model'],data['credential_id'],data.get('agent_id'),data.get('node_refs'),data.get('execution_mode'))


def _install_receipt_question(graph, config, prompt, resources):
    graph.update_state(config, {**prompt, 'resources':resources, 'attachment_route':'ask',
        'system_failure':None,'requirement_failure':None}, as_node='attachment_gate')
    graph.invoke(None, config)


def session_action(c):
    mutations={'message','confirm','upload','attach_asset','tool_gap_choice',
               'tool_gap_replan_propose','tool_gap_replan_decide','tool_gap_job_recover',
               'tool_gap_job_claim','tool_gap_job_finish'}
    if c.action not in mutations:
        return _session_action(c)
    from infrastructure.persistence.generation_lock import generation_session_lock
    with generation_session_lock(c.storage, c.data['id']) as acquired:
        if not acquired:raise ValueError('이 대화의 이전 요청을 처리 중입니다. 완료 후 다시 시도하세요.')
        return _generation_action(c) if c.action in ('message','confirm') else _session_action(c)


def _generation_action(c):
    from infrastructure.persistence.generation_history import GenerationHistory
    audit = GenerationHistory.start(c.storage,{**c.data,'action':c.action})
    from copy import copy
    c = copy(c)
    c.data = {**c.data, 'generation_request_id': audit.id}
    c.storage.generation_audit = audit
    try:
        result = _session_action(c)
        audit.finish(result)
        return {**result,'generation_request_id':audit.id}
    except Exception as error:
        audit.fail(error)
        raise
    finally:
        c.storage.generation_audit = None


def _session_action(c):
    storage=c.storage;data=c.data;action=c.action;api_key=c.api_key;conversation=c.conversation;connection=None
    storage.gmail_connected=data.get('gmail_connected') if type(data.get('gmail_connected')) is bool else None
    storage.slack_connected=data.get('slack_connected') if type(data.get('slack_connected')) is bool else None
    if action == 'message' and 'user_timezone' in data:
        from agent_builder.design.temporal_context import validate_user_timezone
        storage.request_timezone = validate_user_timezone(data['user_timezone'])
    else:
        storage.request_timezone = None
    try:
        s=storage.session(data['id']);graph,connection=build_graph(storage,api_key,s['model'],conversation=conversation if conversation else (lambda state:{}) if action in STATE_ONLY else None)
        chat_save = False
        resource_update = None
        if action in {'message','confirm'}:
            if s['revision']!=data['revision']:raise ValueError('다른 요청에서 대화가 변경됐습니다. 새로고침 후 다시 시도하세요.')
            config={'configurable':{'thread_id':s['id']}}
            snapshot=graph.get_state(config)
            chat_save = action == 'message' and _chat_save_message(data, snapshot)
            fresh=[storage.asset(id) for id in s['file_ids']]
            versions=lambda resources:[(r['id'],str(r.get('version') or r.get('hash') or '1')) for r in resources]
            if snapshot.values and versions(snapshot.values.get('resources',[]))!=versions(fresh):
                if action=='confirm' or chat_save:raise ValueError('자료 검토 결과가 변경됐습니다. 업무 설명을 다시 보내 설정과 근거를 확인하세요.')
                resource_update = {'resources': fresh, 'confirmed': False, 'verification': None,
                                   'verification_last_candidate': None}
        if action=='session':return public_state(storage,s,graph)
        if action in ('tool_gap_replan_propose','tool_gap_replan_decide'):
            state=graph.get_state({'configurable':{'thread_id':s['id']}}).values or {}
            s=(tool_gap_redesign.propose if action=='tool_gap_replan_propose' else tool_gap_redesign.decide)(storage,state,s,data)
            return public_state(storage,s,graph)
        if action=='tool_gap_job_recover':return tool_gap_jobs.recover(storage,s)
        if action in ('tool_gap_job_claim','tool_gap_job_finish'):
            state=graph.get_state({'configurable':{'thread_id':s['id']}}).values or {}
            if action=='tool_gap_job_claim':
                if type(data.get('revision')) is not int or data['revision']!=s['revision']:
                    raise ValueError('대화 버전이 변경됐습니다. 제작 선택을 다시 확인하세요.')
                return tool_gap_jobs.claim(storage,state,s,data.get('input_sha256'))
            return {'job':tool_gap_jobs.finish(storage,state,s,data.get('job_id'),data.get('result'),data.get('lease_token'))}
        if action=='tool_gap_choice':
            state=graph.get_state({'configurable':{'thread_id':s['id']}}).values or {}
            s=choose(storage,state,s,data)
            return public_state(storage,s,graph)
        if action=='tool_gap':
            if isinstance(data.get('revision'),bool) or not isinstance(data.get('revision'),int) or s['revision']!=data['revision']:
                raise ValueError('대화 버전이 변경됐습니다. 현재 미지원 요청을 다시 확인하세요.')
            state=graph.get_state({'configurable':{'thread_id':s['id']}}).values or {}
            return {'tool_gap':export_gap(state,s)}
        if action in ('upload','attach_asset'):
            if s['revision']!=data['revision']:raise ValueError('대화가 변경됐습니다. 새로고침 후 다시 시도하세요.')
            if len(s['file_ids'])>=10:raise ValueError('자료는 최대 10개 연결할 수 있습니다.')
            config={'configurable':{'thread_id':s['id']}}
            snapshot=graph.get_state(config)
            state=snapshot.values or {}
            if state.get('status')=='saved':raise ValueError('저장된 에이전트의 자료를 바꾸려면 설정 수정 대화를 시작하세요.')
            storage.begin_write()
            try:
                resources=[storage.asset(id) for id in s['file_ids']]
                if action=='attach_asset':
                    a=storage.asset(data['asset_id'])
                    if a['id'] in s['file_ids']:raise ValueError('이미 이 설계에 연결된 자료입니다.')
                else:
                    a={**data['asset'],'id':uid(),'created_at':now()}
                prompt=receipt_upload_prompt({**state,'resources':resources},a)
                if action=='upload':storage.add_asset(a,data['rows'],commit=False)
                s['file_ids'].append(a['id'])
                if state.get('draft'):s['draft_name']=state['draft'].get('name')
                resources.append(a)
                s=storage.update_session(s,data['revision'],prompt)
            except Exception:
                storage.db.rollback()
                raise
            if prompt:
                _install_receipt_question(graph,config,prompt,resources)
            elif state:
                # Attaching a file is not a request: the assistant says nothing until the user's next
                # message, which re-reads every attached resource. A verdict reached without this file
                # (ready, unsupported/tool gap, failure) no longer applies, so it is withdrawn silently,
                # together with the validation errors it produced (PR #99 review). A retryable
                # requirement failure stays so the user can retry with the new file at once.
                update={'resources':resources,'issues':[],'semantic_issues':[]}
                if state.get('status') not in (None,'empty','needs_clarification'):update['status']='needs_clarification'
                from agent_builder.design.failure_recovery import current_failure, normalize_active_failures
                diagnosed=normalize_active_failures(state)
                failure=current_failure(diagnosed)
                if failure and failure.get('retryable') is True:
                    # This recovery is explicit: an old failure carried into a
                    # genuine later question must not activate the retry UI.
                    for key in ('system_failure','requirement_failure'):
                        update[key]={**failure,'recovery_pending':True} if diagnosed.get(key) is failure else None
                    update['pending_questions']=[]
                    if isinstance(state.get('draft'),dict):
                        update['draft']={**state['draft'],'questions':[],'question_options':[],'question_responses':[]}
                _update_session_checkpoint(graph,config,update,snapshot.next)
            return public_state(storage,s,graph)
        if action not in ['message','confirm']:raise ValueError('지원하지 않는 요청입니다.')
        config={'configurable':{'thread_id':s['id']},'recursion_limit':30}
        before=graph.get_state(config);state=before.values or {};has_interrupt=any(task.interrupts for task in before.tasks)
        question_wait = before.next if before.next in _QUESTION_WAIT_ROUTES else ()
        from agent_builder.design.failed_design import retained_design, MEANING_FIELDS
        state=retained_design(state,graph,config,(before.config or {}).get('configurable',{}).get('checkpoint_id'))
        restored={key:state.get(key) for key in ('draft', *MEANING_FIELDS)
                  if state.get(key) != (before.values or {}).get(key)}
        if chat_save:
            reply=_chat_save_reply(state,before)
            shown=len(state.get('messages') or _work_messages(storage,s,graph))
            s={**s,'side_messages':s.get('side_messages',[])+[
                   {'after':shown,'role':'user','content':data['content'].strip(),'scope':'side'},
                   {'after':shown,'role':'assistant','content':reply,'scope':'side'}],
               'last_generation_request_id':data.get('generation_request_id')}
            s=storage.update_session(s,data['revision'])
            return public_state(storage,s,graph)
        if action=='confirm':
            if state.get('status')=='saved':return public_state(storage,s,graph)
            from agent_builder.capabilities.capability_gate import require_ready
            require_ready(state)
            from agent_builder.conditions.clarification_core import open_required
            if open_required(state):raise ValueError('답하지 않은 확인 질문이 남아 있어 저장할 수 없습니다.')
            if not has_interrupt or state.get('status')!='ready':raise ValueError('필수 조건과 근거 검증을 완료한 뒤 저장할 수 있습니다.')
            if data.get('include_schedule') is not None and type(data['include_schedule']) is not bool:
                raise ValueError('예약을 함께 저장할지 선택해주세요.')
            graph.invoke(Command(resume={'type':'confirm',**({'include_schedule':data['include_schedule']} if 'include_schedule' in data else {})}),config)
        else:
            content=data['content'].strip()
            if not content or len(content)>4000:raise ValueError('업무 설명은 1~4,000자로 입력하세요.')
            if state.get('status')=='saved':raise ValueError('저장한 설정을 바꾸려면 설정 수정 대화를 시작하세요.')
            node_edit=data.get('node_edit')
            if node_edit is not None:
                if (not isinstance(node_edit,dict) or node_edit.get('request')!=content
                        or data.get('question_id') or data.get('question_answers')):
                    raise ValueError('노드 변경 요청과 전송된 내용을 확인해 주세요.')
                if state.get('pending_questions'):
                    raise ValueError('진행 중인 확인 질문을 해결한 뒤 노드 설정을 수정해 주세요.')
            # Classify the turn before resuming: a greeting or one-off side question is not an
            # answer, task change or fingerprint input, and must not resume the pending checkpoint.
            from agent_builder.design.turn_intent import split_turn, side_reply
            # A structured answer from the question controls already names its question; it is
            # never re-read as a side remark or a question about the question.
            structured=bool(data.get('question_answers')) or node_edit is not None
            pending=state.get('pending_questions') or []
            draft=state.get('draft') or {}
            # Planner questions live only in the draft (with their declared answer controls).
            planner=[{'text':text,'options':(draft.get('question_options') or [[]]*len(draft.get('questions') or []))[i] if i<len(draft.get('question_options') or []) else [],
                      'response':(draft.get('question_responses') or [])[i] if i<len(draft.get('question_responses') or []) else {}}
                      for i,text in enumerate(draft.get('questions') or [])] if has_interrupt or question_wait else []
            waiting=pending or planner
            pending_texts=[q['text'] for q in waiting if q.get('text')]
            # A current failure or explicit file-recovery marker is guidance;
            # old metadata attached to a real later question is inactive.
            from agent_builder.design.failure_recovery import current_failure
            failure=current_failure(state)
            turn=split_turn(content,state,waiting) if not structured else {'work':content,'side':[],'kinds':[]}
            if turn['side']:
                shown=len(state.get('messages') or _work_messages(storage,s,graph))
                reply=side_reply(turn,pending_texts if has_interrupt or pending else [],waiting if has_interrupt or pending else [],failure=failure)
                s={**s,'side_messages':s.get('side_messages',[])+[
                    {'after':shown,'role':'user','content':' '.join(turn['side']),'scope':'side'},
                    *([{'after':shown,'role':'assistant','content':reply,'scope':'side'}] if reply else [])]}
                if not turn['work']:
                    # Recorded like any other request so a lost response can be recovered by its id.
                    s={**s,'last_generation_request_id':data.get('generation_request_id')}
                    s=storage.update_session(s,data['revision'])
                    return public_state(storage,s,graph)
                content=turn['work']
            if resource_update:
                # A work turn can revalidate against the new version. Guidance
                # leaves the checkpoint untouched, and open answers keep their context.
                if not _waiting_for_answers(state, before):
                    resource_update['condition_ledger'] = None
                _update_session_checkpoint(graph, config, resource_update, before.next)
                before = graph.get_state(config)
                has_interrupt = any(task.interrupts for task in before.tasks)
                question_wait = before.next if before.next in _QUESTION_WAIT_ROUTES else ()
                state = {**state, **resource_update}
            from agent_builder.design.question_answers import question_answers
            answers=question_answers(data.get('question_answers'),state)
            if answers and not (has_interrupt or question_wait):
                question_wait = _recover_question_wait(state, before.next, answers)
                if not question_wait:
                    raise ValueError('질문이 변경되었습니다. 현재 질문을 확인한 뒤 다시 답변해주세요.')
            # Older uploads could leave await_input scheduled but not yet interrupted.
            # Install the projected control before deciding how to resume the graph.
            legacy = legacy_receipt_intake(state) if data.get('question_id') else None
            if legacy and data['question_id'] == legacy['pending_questions'][0]['id']:
                _install_receipt_question(graph,config,legacy,state.get('resources',[]))
                before=graph.get_state(config)
                state=before.values
                has_interrupt=any(task.interrupts for task in before.tasks)
                question_wait = before.next if before.next in _QUESTION_WAIT_ROUTES else ()
            elif legacy and not any(q['id']==data['question_id'] for q in state.get('pending_questions',[])):
                raise ValueError('현재 대화의 확인 질문에 답변해주세요.')
            from agent_builder.design.retry_request import is_generation_retry
            from agent_builder.design.failure_recovery import projected_recovery_failure
            retrying = (not answers and not data.get('question_id')
                        and is_generation_retry(content)
                        and (failure or state.get('status') == 'validation_failed'
                             or projected_recovery_failure(state)
                             or before.next and not (has_interrupt or question_wait)))
            cleared_failures={'system_failure':None,'requirement_failure':None,
                'previous_system_failure':state.get('system_failure') or state.get('requirement_failure')
                    or state.get('previous_system_failure')}
            if retrying:
                # Retry is a control action, not a new business requirement or a
                # response to a pending field. Keep exact source turns and IDs.
                _rerun_from_gates(graph,config,state,restored)
            elif has_interrupt or question_wait:
                if data.get('question_id') and data['question_id'] not in [q['id'] for q in state.get('pending_questions',[])]:
                    raise ValueError('현재 대화의 확인 질문에 답변해주세요.')
                # A migrated receipt checkpoint moved from await_capability to await_input.
                # An unqualified update here can restore the old node and its obsolete question IDs.
                if not any(q.get('source')=='receipt' for q in state.get('pending_questions',[])):
                    _update_session_checkpoint(graph,config,{'repair_count':0,**cleared_failures},question_wait)
                elif question_wait and question_wait!=before.next:
                    _update_session_checkpoint(graph,config,{},question_wait)
                graph.invoke(Command(resume={'type':'message','content':content,
                    **({'node_edit':node_edit} if node_edit is not None else {}),
                    **({'question_id':data['question_id']} if data.get('question_id') else {}),
                    **({'question_answers':answers} if answers else {})}),config)
            elif failure and _last_user(state)==content:
                # Resending the request of a current processing failure reruns it from the gates
                # instead of appending a duplicate turn. This holds whether or not the failure was
                # diagnosed as retryable, so the same fingerprint keeps counting attempts.
                _rerun_from_gates(graph,config,state,restored)
            elif before.next:
                # A provider failure can leave conversation pending. Retry its exact input.
                from agent_builder.capabilities.capability_gate import blocking_issue
                same=bool(state.get('messages',[]) and state['messages'][-1]['content']==content)
                if same and not blocking_issue(state):graph.invoke(None,config)
                else:
                    graph.update_state(config,{'messages':state.get('messages',[]) if same else state.get('messages',[])+[{'role':'user','content':content}],
                                               'node_edit':node_edit,'confirmed':False,**cleared_failures,
                                               **({'repair_count':0} if not same else {})},as_node='resources')
                    graph.invoke(None,config)
            else:
                base=storage.agent(s['agent_id']) if s.get('agent_id') else None
                base_state=(graph.get_state({'configurable':{'thread_id':base['session_id']}}).values or {}) if base else {}
                base_messages=base_state.get('messages',[])
                saved_proof=None
                if (node_edit is not None and base and not state and base['version']==s['base_version']
                        and base_state.get('status')=='saved'
                        and base_state.get('saved_agent_id')==base['id']):
                    proof=base_state.get('node_edit_proof')
                    source_turn=proof.get('source_turn') if isinstance(proof,dict) else None
                    if type(source_turn) is int and 0 <= source_turn < len(base_messages):
                        from agent_builder.design.node_edit import proof_issue
                        saved_draft={k:v for k,v in base['config'].items() if k!='node_snapshots'}
                        # Save choices may add later user turns; verify against the edit's own request.
                        prior={**base_state,'messages':base_messages[:source_turn+1]}
                        if proof_issue(prior,saved_draft) is None:
                            saved_proof=proof
                initial={**state,**cleared_failures,'session_id':s['id'],'messages':(state.get('messages',[]) or base_messages)+[{'role':'user','content':content}],'resources':[storage.asset(id) for id in s['file_ids']],'draft':state.get('draft') or ({k:v for k,v in base['config'].items() if k!='node_snapshots'} if base else None),'trace':state.get('trace',[]),'repair_count':0,'confirmed':False,'saved_agent_id':None,'verification_last_candidate':state.get('verification_last_candidate') or ({k:v for k,v in base['config'].items() if k!='node_snapshots'} if base else None),'base_agent_id':s.get('agent_id'),'base_version':s['base_version'],'model':s['model'],'credential_id':s['credential_id'],'node_library':s.get('node_library',[]),'required_node_refs':s.get('required_node_refs',[]),'node_edit':node_edit}
                if saved_proof:initial['node_edit_proof']=saved_proof
                initial['execution_mode']=s.get('execution_mode')
                graph.invoke(initial,config)
        after=graph.get_state(config).values
        if after.get('saved_agent_id'):
            s['agent_id']=after['saved_agent_id'];s['base_version']=storage.agent(s['agent_id'])['version']
        s={**s,'last_generation_request_id':data['generation_request_id']}
        if getattr(storage,'generation_audit',None):storage.generation_audit.result_next_nodes=graph.get_state(config).next
        s=storage.update_session(s,data['revision'],after)
        if getattr(storage,'generation_audit',None):storage.generation_audit.finish(after,graph.get_state(config).next,result_revision=s['revision'])
        return public_state(storage,s,graph)
    finally:
        if connection:connection.close()
