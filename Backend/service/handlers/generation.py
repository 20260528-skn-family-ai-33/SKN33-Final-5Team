"""Short control calls used even when a design worker cannot start or is killed."""
from infrastructure.persistence.generation_history import GenerationHistory, history

INTERRUPT_REASONS = {
    'worker_timeout': '생성 작업의 제한 시간이 초과되어 작업자를 종료했습니다.',
    'worker_busy': '처리 중인 작업이 많아 생성 작업을 시작하지 못했습니다.',
    'worker_start_failed': '생성 작업자를 시작하지 못했습니다.',
    'worker_response_invalid': '생성 작업자의 응답을 해석하지 못했습니다.',
    'request_rejected': '모델 연결 계정 또는 생성 요청의 준비 상태를 확인하세요.',
    'worker_failed': '생성 작업자가 정상적으로 완료되지 않았습니다.',
}


def begin(c):
    action = c.data['request_action']
    if action not in ('message', 'confirm'):
        raise ValueError('생성 요청 종류를 확인하세요.')
    audit = GenerationHistory.start(c.storage, {**c.data, 'action': action})
    return {'generation_request_id': audit.id}


def interrupt(c):
    c.storage.session(c.data['id'])
    audit = GenerationHistory(c.storage, c.data['generation_request_id'])
    record = audit.read()
    if record['session_id'] != c.data['id']:
        raise ValueError('이 대화의 생성 요청이 아닙니다.')
    code = c.data.get('reason_code', 'worker_failed')
    if code not in INTERRUPT_REASONS:
        code = 'worker_failed'
    return audit.change(status='failed', reason_code=code, reason=INTERRUPT_REASONS[code], error_type=None)


def listing(c):
    return {'requests': history(c.storage, c.data['id'])}


def recover(c):
    """Close an abandoned audit only while no worker owns this session."""
    from infrastructure.persistence.generation_lock import generation_session_lock
    from uuid import UUID
    sid=c.data['id']
    c.storage.session(sid)
    request_id=str(UUID(c.data['generation_request_id']))
    with generation_session_lock(c.storage, sid) as acquired:
        if not acquired:return {'recovered':False, 'reason':'active'}
        audit=GenerationHistory(c.storage, request_id)
        record=audit.read()
        if record['session_id'] != sid:raise ValueError('이 대화의 생성 요청이 아닙니다.')
        session=c.storage.session(sid)
        if session['revision'] != c.data['revision']:
            return {'recovered':False, 'reason':'changed'}
        # A completed session may have been published just before the worker exited.
        # Preserve that success instead of marking it failed.
        from service.handlers.design import _session_action
        from copy import copy
        context=copy(c);context.action='session'
        latest=_session_action(context)
        if record['action']=='confirm' and record['status']=='succeeded' and record.get('workflow_id'):
            return _restore_saved(c, audit, record, session, latest)
        if latest.get('last_generation_request_id') == request_id and latest.get('status') in ('ready','needs_clarification','saved'):
            audit.finish(latest, result_revision=latest['revision'])
        elif record['status'] == 'running':
            audit.change(status='failed', reason_code='generation_abandoned',
                         reason='이전 작업이 종료된 것을 확인했습니다. 보존된 입력으로 같은 대화에서 다시 요청할 수 있습니다.', error_type=None)
        return {'recovered':True, 'request_id':request_id}


def _restore_saved(c, audit, record, session, latest):
    """Reconstruct only the saved version committed by this confirmation request."""
    request_id=record['request_id']
    workflow_id=record['workflow_id']
    if (latest.get('status')=='saved' and latest.get('saved_agent_id')==workflow_id
            and session.get('last_generation_request_id')==request_id):
        return {'recovered':True, 'request_id':request_id}
    progress=session.get('save_recovery') or {}
    reserved=(progress.get('request_id')==request_id and progress.get('workflow_id')==workflow_id
              and progress.get('revision')==session['revision']
              and session.get('last_generation_request_id')==request_id
              and session.get('agent_id')==workflow_id and session['base_version']==progress.get('workflow_version'))
    if session['revision'] != record['session_revision'] and not reserved:
        return {'recovered':False, 'reason':'changed'}
    # A legacy record resolves from the original base, or its reserved version
    # when retrying after metadata committed but checkpoint publication failed.
    version=record.get('workflow_version',progress.get('workflow_version') if reserved else session['base_version']+1)
    if type(version) is not int or version<1:raise ValueError('저장된 워크플로우 버전을 확인하세요.')
    saved=c.storage.agent_version(workflow_id, version)
    if saved['session_id'] != session['id']:
        raise ValueError('이 대화에서 저장한 워크플로우가 아닙니다.')
    from agent_builder.design.graph import build_graph
    graph,connection=build_graph(c.storage,model=session['model'],conversation=c.conversation)
    try:
        config={'configurable':{'thread_id':session['id']}}
        state={**(graph.get_state(config).values or {}), 'status':'saved', 'saved_agent_id':workflow_id,
               'confirmed':True,'draft':{key:value for key,value in saved['config'].items() if key!='node_snapshots'},
               'issues':[],'semantic_issues':[],'pending_questions':[]}
        if not reserved:
            previous=getattr(c.storage,'generation_audit',None)
            c.storage.generation_audit=audit
            try:
                # CAS must succeed before any checkpoint change. Persist the
                # reservation so a failed checkpoint write can be retried safely.
                c.storage.update_session({**session,'agent_id':workflow_id,'base_version':version,
                    'last_generation_request_id':request_id,'save_recovery':{
                        'request_id':request_id,'workflow_id':workflow_id,'workflow_version':version,
                        'revision':session['revision']+1}},session['revision'],state)
            finally:c.storage.generation_audit=previous
        # Every design mutation holds the same session lock through publication.
        # This completes the save checkpoint without invoking save_agent again.
        graph.update_state(config,state,as_node='save')
    finally:connection.close()
    return {'recovered':True, 'request_id':request_id}
