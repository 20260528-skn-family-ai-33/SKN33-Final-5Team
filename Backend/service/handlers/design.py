"""A1 design-session jobs: conversation, uploads, confirm/save and tool-gap choices.

Every action here loads the session's LangGraph conversation (build_graph) first.
"""
from infrastructure.persistence.ids import uid, now
from agent_builder.design.graph import build_graph
from agent_builder.design.receipt_intake import current_receipt_questions, legacy_receipt_intake, receipt_upload_prompt
from documents.resources.file_inspection import inspect_file
from agent_builder.conditions.input_requirements import design_requirements
from agent_builder.tool_gap.gap import describe_gap, export_gap
from agent_builder.tool_gap.choice import present_choice, choose, redesign_context
from agent_builder.tool_gap import jobs as tool_gap_jobs, redesign as tool_gap_redesign
from langgraph.types import Command

# Actions that only read or update session state: the model conversation is never invoked.
STATE_ONLY={'session','upload','attach_asset','tool_gap','tool_gap_choice','tool_gap_job_claim','tool_gap_job_finish','tool_gap_job_recover','tool_gap_replan_propose','tool_gap_replan_decide'}


def public_state(storage,s,graph):
    snapshot=graph.get_state({'configurable':{'thread_id':s['id']}})
    state=snapshot.values or {}
    if not state and s.get('agent_id'):
        base=storage.agent(s['agent_id']);old=(graph.get_state({'configurable':{'thread_id':base['session_id']}}).values or {})
        state={'draft':base['config'],'messages':old.get('messages',[]),'status':'editing'}
    if state.get('status') != 'saved' and any(r['id'] not in s['file_ids'] for r in state.get('resources', [])):
        state = {'messages':state.get('messages', []), 'status':'validation_failed',
                 'issues':['연결 자료가 삭제됐습니다. 업무 설명을 보내 남은 자료로 설정을 다시 확인하세요.']}
    legacy = legacy_receipt_intake(state)
    if legacy:
        # Keep the recorded transcript; only adapt the controls for the interrupted turn.
        state = {**state, 'draft':legacy['draft'], 'pending_questions':legacy['pending_questions']}
    from agent_builder.capabilities.capability_gate import blocking_issue, capability_enforced
    issue=blocking_issue(state) if state.get('status')=='ready' else None
    resources=[storage.asset(id) for id in s['file_ids']]
    inspections=[inspect_file(asset) for asset in resources]
    return {**s,'messages':state.get('messages',[]),'draft':state.get('draft'),'status':'validation_failed' if issue else state.get('status','empty'),'issues':list(dict.fromkeys(state.get('issues',[])+([issue] if issue else []))),'trace':state.get('trace',[])[-40:],'verification':state.get('verification'),'saved_agent_id':state.get('saved_agent_id'),'resources':resources,
            'file_inspection':inspections,
            'condition_ledger':state.get('condition_ledger'),'pending_questions':current_receipt_questions(state,resources,inspections),
            'gap_condition_review':state.get('gap_condition_review'),
            'input_requirements':design_requirements(state) if state.get('condition_enforced') else [],
            'tool_gap':(export_gap(state,s) if describe_gap(state,s['id']) else None),
            'tool_gap_choice':present_choice(state,s), 'redesign_context':redesign_context(state,s),
            'tool_gap_job':tool_gap_jobs.current_job(storage,state,s),
            'capability_assessment':state.get('capability_assessment'),
            'capability_enforced':capability_enforced(state),
            'capability_deferred':state.get('capability_deferred',False),
            'redesign_proposal':tool_gap_redesign.view(state,s),
            'redesign_generation':tool_gap_redesign.generation_handoff(state,s)}


def new_session(c):
    data=c.data
    return c.storage.new_session(data['model'],data['credential_id'],data.get('agent_id'),data.get('node_refs'))


def _install_receipt_question(graph, config, prompt, resources):
    graph.update_state(config, {**prompt, 'resources':resources, 'attachment_route':'ask'}, as_node='attachment_gate')
    graph.invoke(None, config)


def session_action(c):
    if c.action not in ('message','confirm'):
        return _session_action(c)
    from infrastructure.persistence.generation_history import GenerationHistory
    audit = GenerationHistory.start(c.storage,{**c.data,'action':c.action})
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
    try:
        s=storage.session(data['id']);graph,connection=build_graph(storage,api_key,s['model'],conversation=conversation if conversation else (lambda state:{}) if action in STATE_ONLY else None)
        if action in {'message','confirm'}:
            snapshot=graph.get_state({'configurable':{'thread_id':s['id']}})
            fresh=[storage.asset(id) for id in s['file_ids']]
            versions=lambda resources:[(r['id'],str(r.get('version') or r.get('hash') or '1')) for r in resources]
            if snapshot.values and versions(snapshot.values.get('resources',[]))!=versions(fresh):
                if action=='confirm':raise ValueError('자료 검토 결과가 변경됐습니다. 업무 설명을 다시 보내 설정과 근거를 확인하세요.')
                graph.update_state({'configurable':{'thread_id':s['id']}},{'resources':fresh,'confirmed':False,'verification':None,'verification_last_candidate':None})
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
            state=graph.get_state(config).values or {}
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
                graph.update_state(config,{'resources':resources,'status':'needs_clarification','issues':['자료 연결이 변경됐습니다. 업무 설명을 보내 새 자료와 설정을 확인하세요.']})
            return public_state(storage,s,graph)
        if action not in ['message','confirm']:raise ValueError('지원하지 않는 요청입니다.')
        if s['revision']!=data['revision']:raise ValueError('다른 요청에서 대화가 변경됐습니다. 새로고침 후 다시 시도하세요.')
        config={'configurable':{'thread_id':s['id']},'recursion_limit':30}
        before=graph.get_state(config);state=before.values or {};has_interrupt=any(task.interrupts for task in before.tasks)
        if action=='confirm':
            if state.get('status')=='saved':return public_state(storage,s,graph)
            from agent_builder.capabilities.capability_gate import require_ready
            require_ready(state)
            if not has_interrupt or state.get('status')!='ready':raise ValueError('필수 조건과 근거 검증을 완료한 뒤 저장할 수 있습니다.')
            graph.invoke(Command(resume={'type':'confirm'}),config)
        else:
            content=data['content'].strip()
            if not content or len(content)>4000:raise ValueError('업무 설명은 1~4,000자로 입력하세요.')
            if state.get('status')=='saved':raise ValueError('저장한 설정을 바꾸려면 설정 수정 대화를 시작하세요.')
            # Older uploads could leave await_input scheduled but not yet interrupted.
            # Install the projected control before deciding how to resume the graph.
            legacy = legacy_receipt_intake(state) if data.get('question_id') else None
            if legacy and data['question_id'] == legacy['pending_questions'][0]['id']:
                _install_receipt_question(graph,config,legacy,state.get('resources',[]))
                before=graph.get_state(config)
                state=before.values
                has_interrupt=any(task.interrupts for task in before.tasks)
            elif legacy and not any(q['id']==data['question_id'] for q in state.get('pending_questions',[])):
                raise ValueError('현재 대화의 확인 질문에 답변해주세요.')
            if has_interrupt:
                if data.get('question_id') and data['question_id'] not in [q['id'] for q in state.get('pending_questions',[])]:
                    raise ValueError('현재 대화의 확인 질문에 답변해주세요.')
                # A migrated receipt checkpoint moved from await_capability to await_input.
                # An unqualified update here can restore the old node and its obsolete question IDs.
                if not any(q.get('source')=='receipt' for q in state.get('pending_questions',[])):
                    graph.update_state(config,{'repair_count':0})
                graph.invoke(Command(resume={'type':'message','content':content,**({'question_id':data['question_id']} if data.get('question_id') else {})}),config)
            elif before.next:
                # A provider failure can leave conversation pending. Retry its exact input.
                from agent_builder.capabilities.capability_gate import blocking_issue
                same=bool(state.get('messages',[]) and state['messages'][-1]['content']==content)
                if same and not blocking_issue(state):graph.invoke(None,config)
                else:
                    graph.update_state(config,{'messages':state.get('messages',[]) if same else state.get('messages',[])+[{'role':'user','content':content}],
                                               'confirmed':False,**({'repair_count':0} if not same else {})},as_node='resources')
                    graph.invoke(None,config)
            else:
                base=storage.agent(s['agent_id']) if s.get('agent_id') else None
                base_messages=(graph.get_state({'configurable':{'thread_id':base['session_id']}}).values or {}).get('messages',[]) if base else []
                initial={**state,'session_id':s['id'],'messages':(state.get('messages',[]) or base_messages)+[{'role':'user','content':content}],'resources':[storage.asset(id) for id in s['file_ids']],'draft':state.get('draft') or ({k:v for k,v in base['config'].items() if k!='node_snapshots'} if base else None),'trace':state.get('trace',[]),'repair_count':0,'confirmed':False,'saved_agent_id':None,'verification_last_candidate':state.get('verification_last_candidate') or ({k:v for k,v in base['config'].items() if k!='node_snapshots'} if base else None),'base_agent_id':s.get('agent_id'),'base_version':s['base_version'],'model':s['model'],'credential_id':s['credential_id'],'node_library':s.get('node_library',[]),'required_node_refs':s.get('required_node_refs',[])}
                graph.invoke(initial,config)
        after=graph.get_state(config).values
        if after.get('saved_agent_id'):
            s['agent_id']=after['saved_agent_id'];s['base_version']=storage.agent(s['agent_id'])['version']
        s=storage.update_session(s,data['revision'],after)
        if getattr(storage,'generation_audit',None):storage.generation_audit.finish(after,graph.get_state(config).next)
        return public_state(storage,s,graph)
    finally:
        if connection:connection.close()
