"""Proposed redesign boundary. Does not silently alter source requests or execute a plan."""
from pydantic import BaseModel,ConfigDict,Field
from .tool_gap_choice import present_choice
from .registry import TOOLS
from .storage import uid,now

class Change(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    original_quote:str=Field(min_length=1,max_length=4000)
    proposed_change:str=Field(min_length=1,max_length=2000)

class Alternative(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    summary:str=Field(min_length=1,max_length=1000)
    proposed_request:str=Field(min_length=1,max_length=4000)
    tool_ids:list[str]=Field(min_length=1,max_length=16)
    changes:list[Change]=Field(min_length=1,max_length=10)
    limitations:list[str]=Field(min_length=1,max_length=10)

def current(state,session):
    selection=present_choice(state,session)
    proposal=session.get('tool_gap_redesign')
    if (not selection or selection['status']!='redesign_requested' or not proposal or
        proposal['gap_id']!=selection['gap_id'] or proposal['decision_id']!=selection['selection']['id']):return None
    return proposal


def view(state,session):
    proposal=current(state,session)
    if not proposal:return None
    return {**proposal,'revision':session['revision'],'next_action':{
        'awaiting_confirmation':'confirm_requirement_changes','accepted':'generate_revised_draft',
        'rejected':'propose_another_alternative','deferred':'wait_for_user'}[proposal['status']],
        'allows_save':False,'allows_execution':False}


def _update(storage,session,selection,fields):
    return storage.update_session({**session,**fields,
        'tool_gap_interaction_revision':session['revision']+1,
        'tool_gap_interaction_decision_id':selection['selection']['id']},session['revision'])


def propose(storage,state,session,data):
    if type(data.get('revision')) is not int or data['revision']!=session['revision']:
        raise ValueError('현재 대화 버전으로 재설계 대안을 제시하세요.')
    selection=present_choice(state,session)
    if not selection or selection['status']!='redesign_requested':raise ValueError('사용자의 워크플로우 재설계 선택이 필요합니다.')
    alternative=Alternative.model_validate(data.get('alternative')).model_dump()
    tools=alternative['tool_ids']
    if len(set(tools))!=len(tools) or not set(tools)<={t['id'] for t in TOOLS}:raise ValueError('재설계는 현재 등록 도구로 구성하세요.')
    messages=[m['content'] for m in state.get('messages',[]) if m['role']=='user']
    if any(not any(c['original_quote'] in m for m in messages) for c in alternative['changes']):raise ValueError('변경하려는 원래 요구의 인용을 확인하지 못했습니다.')
    if not alternative['proposed_request'].strip():raise ValueError('재설계할 요청이 필요합니다.')
    proposal={'protocol':'a4b.redesign-proposal.v1','id':uid(),'gap_id':selection['gap_id'],
        'decision_id':selection['selection']['id'],'created_at':now(),'status':'awaiting_confirmation',
        'alternative':alternative,'requirement_changes_approved':False}
    return _update(storage,session,selection,{'tool_gap_redesign':proposal})


def decide(storage,state,session,data):
    import uuid
    request_id=data.get('request_id')
    try:
        if not isinstance(request_id,str) or str(uuid.UUID(request_id))!=request_id:raise ValueError()
    except (ValueError,AttributeError):raise ValueError('재설계 확인 요청 ID가 필요합니다.')
    proposal=current(state,session);selection=present_choice(state,session)
    if not proposal or data.get('proposal_id')!=proposal['id']:raise ValueError('현재 재설계 대안을 다시 확인하세요.')
    decision=data.get('decision')
    if decision not in ('accept','reject','defer'):raise ValueError('재설계 대안 확인·거절·보류 중 선택하세요.')
    revision=data.get('revision')
    if type(revision) is not int:raise ValueError('현재 대화 버전이 필요합니다.')
    events=session.get('redesign_decision_events',[])
    previous=next((e for e in events if e['request_id']==request_id),None)
    if previous:
        if previous['proposal_id']==proposal['id'] and previous['decision']==decision and previous['input_revision']==revision and previous['result_revision']==session['revision']:return session
        raise ValueError('이미 처리되거나 변경된 재설계 확인 요청입니다.')
    if revision!=session['revision']:raise ValueError('대화가 변경됐습니다. 재설계 대안을 다시 확인하세요.')
    status={'accept':'accepted','reject':'rejected','defer':'deferred'}[decision]
    event={'request_id':request_id,'proposal_id':proposal['id'],'decision':decision,
           'input_revision':revision,'result_revision':revision+1,'created_at':now()}
    return _update(storage,session,selection,{'tool_gap_redesign':{**proposal,'status':status,
        'requirement_changes_approved':decision=='accept'},'redesign_decision_events':events+[event]})


def generation_handoff(state,session):
    proposal=current(state,session)
    if not proposal or proposal['status']!='accepted':return None
    from copy import deepcopy
    return {'protocol':'a4b.redesign-generation.v1','session_id':session['id'],'revision':session['revision'],
        'proposal_id':proposal['id'],'original_messages':deepcopy(state.get('messages',[])),
        'original_conditions':deepcopy(state.get('condition_ledger')),
        'condition_review':deepcopy(state.get('gap_condition_review')),
        'effective_request':proposal['alternative']['proposed_request'],
        'approved_changes':proposal['alternative']['changes'],'tool_ids':proposal['alternative']['tool_ids'],
        'requires_condition_recheck':True,'requires_draft_validation':True,'requires_user_approval':True,
        'allows_save':False,'allows_execution':False}
