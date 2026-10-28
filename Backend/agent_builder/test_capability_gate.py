"""Real graph/SQLite/save invariants with controlled inference, no model calls."""
from copy import deepcopy
import pytest
from langgraph.types import Command
from agent_builder.graph import build_graph
from agent_builder.storage import Storage,uid
from agent_builder.test_condition_readiness import state,proposal,workflow_draft,Fake
from agent_builder.capability_gate import input_fingerprint,blocking_issue,generation_gate
from agent_builder.nodes.save import make_save_node
from agent_builder.nodes.supervisor import supervisor_node
from agent_builder.registry import TOOLS
from agent_builder.worker import handle


def assessment(s,decision='existing_tools',questions=None):
    return {'decision':decision,'tool_ids':['read_table','filter_sort','write_result'],
            'missing_conditions':questions or (['기준을 검토한 담당자를 알려주세요.'] if decision=='clarify' else []),
            'missing_actions':['외부 전송'] if decision=='missing_capability' else [],
            'reason':'제어한 지원 판단','user_quotes':[s['messages'][0]['content']]}


def complete(calls):
    def conversation(s):
        calls.append((s.get('capability_assessment') or {}).get('decision','off'))
        return {'draft':workflow_draft(s),'confirmed':False,'saved_agent_id':None}
    return conversation


def setup(store,s,calls,cap,condition_mode='off',mode='enforce'):
    return build_graph(store,conversation=complete(calls),capability_mode=mode,capability_transport=cap,
                       condition_mode=condition_mode,condition_transport=Fake([proposal(s)]),verifier_mode='off')


@pytest.mark.parametrize('decision',['manual_review','clarify'])
@pytest.mark.parametrize('condition_mode',['off','enforce'])
def test_blocking_decisions_never_generate_or_save_even_after_restart(tmp_path,decision,condition_mode):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    graph,conn=setup(store,s,calls,Fake([assessment(s,decision)]),condition_mode)
    first=graph.invoke(s,cfg)
    assert not calls and not store.agents() and first['capability_enforced'] and first['status']!='ready'
    if decision=='clarify':
        value=first['__interrupt__'][0].value
        assert value['kind']=='clarification' and value['questions']==assessment(s,decision)['missing_conditions']
        assert first['pending_questions'][0]['source']=='capability'
        import re
        assert re.fullmatch(r'[a-f0-9]{24}',first['pending_questions'][0]['id'])
    else:assert first['status']=='validation_failed' and not first.get('__interrupt__')
    conn.close();store.close()
    store=Storage(tmp_path);graph,conn=setup(store,s,calls,Fake([]),mode='off')
    before=graph.get_state(cfg).values
    with pytest.raises(ValueError):make_save_node(store)({**before,'draft':workflow_draft(s),'confirmed':True,'status':'ready'})
    if decision=='clarify':
        blocked=graph.invoke(Command(resume={'type':'confirm'}),cfg)
        assert blocked['__interrupt__'][0].value['error'] and blocked['status']=='needs_clarification'
    else:assert graph.invoke(Command(resume={'type':'confirm'}),cfg)['status']!='saved'
    assert not calls and not store.agents()
    conn.close();store.close()


@pytest.mark.parametrize('condition_mode',['off','enforce'])
def test_answer_rechecks_then_allows_generation_and_save(tmp_path,condition_mode):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    cap=Fake([assessment(s,'clarify'),assessment(s)])
    graph,conn=setup(store,s,calls,cap,condition_mode)
    first=graph.invoke(s,cfg);qid=first['pending_questions'][0]['id']
    ready=graph.invoke(Command(resume={'type':'message','content':'검토 담당자는 운영팀입니다.','question_id':qid}),cfg)
    assert cap.calls==2 and calls==['existing_tools'] and ready['status']=='ready'
    assert ready['capability_answers'][-1]['question_ids']==[qid]
    assert ready['capability_answers'][-1]['answer']=='검토 담당자는 운영팀입니다.'
    assert ready['capability_fingerprint']==input_fingerprint(ready)
    assert not ready['capability_questions'] and not ready['pending_questions'] and not store.agents()
    assert graph.invoke(Command(resume={'type':'confirm'}),cfg)['status']=='saved'
    assert len(store.agents())==1
    conn.close();store.close()


@pytest.mark.parametrize('reply',['모르겠어','아직 결정 못 했어요','나중에 결정','자료 연결 후 기준 입력'])
def test_nonanswers_preserve_questions_and_conditions(tmp_path,reply):
    store=Storage(tmp_path);s=state();s['condition_ledger']={'decision':'clarify','slots':{'filter.value':{'status':'specified','value':1000000}},'questions':[]}
    original=deepcopy(s['condition_ledger']);cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    cap=Fake([assessment(s,'clarify'),assessment(s)]);graph,conn=setup(store,s,calls,cap)
    current=graph.invoke(s,cfg);qid=current['pending_questions'][0]['id']
    current=graph.invoke(Command(resume={'type':'message','content':reply,'question_id':qid}),cfg)
    assert current['status']=='needs_clarification' and current['__interrupt__'] and cap.calls==1 and not calls
    assert current['capability_assessment']['decision']=='clarify'
    assert current['pending_questions'][0]['id']==qid and current['condition_ledger']==original
    assert current['capability_deferred']==(reply=='나중에 결정')
    current=graph.invoke(Command(resume={'type':'message','content':'운영팀이 검토했습니다.','question_id':qid}),cfg)
    assert cap.calls==2 and current['status']=='ready' and not current['capability_deferred']
    assert calls==['existing_tools'] and not store.agents()
    conn.close();store.close()


def test_two_unknowns_defer_then_resume_with_default_off(tmp_path):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    cap=Fake([assessment(s,'clarify')]);graph,conn=setup(store,s,calls,cap)
    current=graph.invoke(s,cfg)
    for _ in range(2):current=graph.invoke(Command(resume={'type':'message','content':'모르겠어'}),cfg)
    assert current['capability_deferred'] and cap.calls==1
    conn.close();store.close()
    store=Storage(tmp_path);cap=Fake([assessment(s)]);graph,conn=setup(store,s,calls,cap,mode='off')
    current=graph.invoke(Command(resume={'type':'message','content':'담당자는 운영팀입니다.'}),cfg)
    assert current['capability_enforced'] and current['status']=='ready' and calls==['existing_tools']
    conn.close();store.close()


def test_manual_review_needs_new_input_and_recheck(tmp_path):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    graph,conn=setup(store,s,calls,Fake([assessment(s,'manual_review')]))
    first=graph.invoke(s,cfg);assert not calls
    conn.close();store.close()
    store=Storage(tmp_path);cap=Fake([assessment(s)]);graph,conn=setup(store,s,calls,cap,mode='off')
    first['messages']+=[{'role':'user','content':'운영팀 검토를 마쳤습니다. 원래 조건으로 다시 확인해줘.'}]
    current=graph.invoke(first,cfg)
    assert cap.calls==1 and current['status']=='ready' and calls==['existing_tools']
    conn.close();store.close()


@pytest.mark.parametrize('next_decision',['manual_review','clarify','missing_capability','existing_tools'])
def test_approval_edit_rechecks_before_generation(tmp_path,next_decision):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    cap=Fake([assessment(s),assessment(s,next_decision),assessment(s,next_decision)])
    graph,conn=setup(store,s,calls,cap);graph.invoke(s,cfg)
    current=graph.invoke(Command(resume={'type':'message','content':'결과 처리 방식도 다시 확인해줘.'}),cfg)
    assert cap.calls==(3 if next_decision=='missing_capability' else 2)
    assert len(calls)==(2 if next_decision=='existing_tools' else 1) and not store.agents()
    assert current['status']!='saved'
    if next_decision=='clarify':assert current['__interrupt__'][0].value['kind']=='clarification'
    conn.close();store.close()


@pytest.mark.parametrize('mutation',['user','resource','health','catalog'])
def test_supervisor_and_save_require_current_fingerprint(tmp_path,mutation,monkeypatch):
    s=state();s.update(draft=workflow_draft(s),status='ready',confirmed=True,capability_enforced=True,capability_assessment=assessment(s))
    s['capability_fingerprint']=input_fingerprint(s);assert blocking_issue(s) is None
    if mutation=='user':s['messages']+=[{'role':'user','content':'업무를 변경합니다.'}]
    elif mutation=='resource':s['resources']=[{'id':'new','kind':'document','text':'변경된 자료'}]
    elif mutation=='health':s['source_health']={'db_status':'failed'}
    else:monkeypatch.setitem(TOOLS[0],'version',TOOLS[0]['version']+1)
    assert blocking_issue(s) and generation_gate(s)['capability_generation_route']=='recheck'
    assert supervisor_node(s)['route']=='invalid'
    store=Storage(tmp_path)
    with pytest.raises(ValueError):make_save_node(store)(s)
    assert not store.agents();store.close()


def test_legacy_ready_cannot_directly_save(tmp_path):
    s=state();s.update(draft=workflow_draft(s),status='ready',confirmed=True,capability_assessment=assessment(s,'manual_review'))
    assert blocking_issue(s) and supervisor_node(s)['route']=='invalid'
    store=Storage(tmp_path)
    with pytest.raises(ValueError):make_save_node(store)(s)
    assert not store.agents();store.close()


def test_worker_rejects_legacy_ready_confirm(tmp_path):
    session=handle({'directory':str(tmp_path),'action':'new_session','model':'test','credential_id':uid()})
    s=state();s['session_id']=session['id'];calls=[];store=Storage(tmp_path)
    graph,conn=setup(store,s,calls,Fake([]),mode='off');cfg={'configurable':{'thread_id':s['session_id']}}
    graph.invoke(s,cfg);graph.update_state(cfg,{'capability_assessment':assessment(s,'manual_review')})
    conn.close();store.close()
    with pytest.raises(ValueError,match='지원 판단'):
        handle({'directory':str(tmp_path),'action':'confirm','id':session['id'],'revision':session['revision']},conversation=complete(calls))
    store=Storage(tmp_path);assert not store.agents();store.close()


def test_condition_question_resume_also_rechecks_capability(tmp_path):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':None,
                    'missing.policy':'error','duplicate.policy':'keep','output.format':'json'})
    cap=Fake([assessment(s),assessment(s,'manual_review')])
    graph,conn=build_graph(store,conversation=complete(calls),capability_mode='enforce',capability_transport=cap,
                          condition_mode='enforce',condition_transport=Fake([raw]),verifier_mode='off')
    current=graph.invoke(s,cfg);assert current['__interrupt__'][0].value['kind']=='clarification'
    current=graph.invoke(Command(resume={'type':'message','content':'100만원 초과로 해줘.'}),cfg)
    assert cap.calls==2 and current['status']=='validation_failed' and not calls and not store.agents()
    conn.close();store.close()


def test_partial_parsing_fault_during_question_never_resolves_unknown(tmp_path):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    cap=Fake([assessment(s,'clarify')]);graph,conn=setup(store,s,calls,cap)
    graph.invoke(s,cfg)
    graph.update_state(cfg,{'source_health':{'db_status':'timeout','messages_loaded':False}})
    current=graph.invoke(Command(resume={'type':'message','content':'모르겠어'}),cfg)
    assert cap.calls==1 and current['capability_assessment']['decision']=='system_error'
    assert current['status']=='validation_failed' and not calls and not store.agents()
    conn.close();store.close()


def test_all_five_questions_are_delivered_and_wrong_id_stays_pending(tmp_path):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    questions=['확인 항목 '+str(i) for i in range(5)]
    graph,conn=setup(store,s,calls,Fake([assessment(s,'clarify',questions)]))
    current=graph.invoke(s,cfg)
    assert current['__interrupt__'][0].value['questions']==questions
    assert len(current['draft']['questions'])==3 and len(current['pending_questions'])==5
    current=graph.invoke(Command(resume={'type':'message','content':'응답','question_id':'other-session'}),cfg)
    assert current['__interrupt__'][0].value['error'] and not calls and not store.agents()
    assert not current.get('capability_answers')
    conn.close();store.close()


def test_worker_full_clarification_unknown_answer_and_confirmation(tmp_path,monkeypatch):
    import agent_builder.worker as worker
    original=worker.build_graph;calls=[];seed=state();cap=Fake([assessment(seed,'clarify'),assessment(seed)])
    mode=['enforce']
    def wrapped(*args,**kwargs):
        kwargs.update(capability_mode=mode[0],capability_transport=cap,condition_mode='off',verifier_mode='off')
        return original(*args,**kwargs)
    monkeypatch.setattr(worker,'build_graph',wrapped)
    s=handle({'directory':str(tmp_path),'action':'new_session','model':'test','credential_id':uid()})
    def action(name,**fields):
        return handle({'directory':str(tmp_path),'id':s['id'],'revision':s['revision'],'action':name,**fields},conversation=complete(calls))
    s=action('message',content=seed['messages'][0]['content'])
    assert s['status']=='needs_clarification' and s['capability_enforced'] and not calls
    with pytest.raises(ValueError):action('confirm')
    with pytest.raises(ValueError):action('message',content='답변',question_id='wrong')
    qid=s['pending_questions'][0]['id'];mode[0]='off'
    s=action('message',content='모르겠어',question_id=qid)
    assert s['status']=='needs_clarification' and cap.calls==1
    s=action('message',content='검토 담당자는 운영팀입니다.',question_id=qid)
    assert s['status']=='ready' and cap.calls==2 and len(calls)==1
    s=action('confirm');assert s['status']=='saved'
    assert handle({'directory':str(tmp_path),'id':s['id'],'action':'session'})['saved_agent_id']==s['saved_agent_id']


@pytest.mark.parametrize('changed',[False,True])
def test_worker_pending_generation_retry_checks_new_input(tmp_path,monkeypatch,changed):
    import agent_builder.worker as worker
    original=worker.build_graph;seed=state();calls=[]
    cap=Fake([assessment(seed),assessment(seed,'manual_review')])
    def wrapped(*args,**kwargs):
        kwargs.update(capability_mode='enforce',capability_transport=cap,condition_mode='off',verifier_mode='off')
        return original(*args,**kwargs)
    monkeypatch.setattr(worker,'build_graph',wrapped)
    s=handle({'directory':str(tmp_path),'action':'new_session','model':'test','credential_id':uid()})
    def failing(current):calls.append('failed');raise RuntimeError('controlled generation failure')
    request={'directory':str(tmp_path),'id':s['id'],'revision':s['revision'],'action':'message','content':seed['messages'][0]['content']}
    with pytest.raises(RuntimeError):handle(request,conversation=failing)
    if changed:request['content']='요청 변경: 아직 검토가 필요한 작업입니다.'
    result=handle(request,conversation=complete(calls))
    if changed:
        assert result['status']=='validation_failed' and calls==['failed'] and cap.calls==2
    else:
        assert result['status']=='ready' and calls==['failed','existing_tools'] and cap.calls==1
    store=Storage(tmp_path);assert not store.agents();store.close()


def test_restored_pending_planner_cannot_skip_guard(tmp_path):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    def failing(current):raise RuntimeError('controlled planner failure')
    graph,conn=build_graph(store,conversation=failing,capability_mode='enforce',capability_transport=Fake([assessment(s)]),condition_mode='off',verifier_mode='off')
    with pytest.raises(RuntimeError):graph.invoke(s,cfg)
    assert graph.get_state(cfg).next==('conversation',)
    graph.update_state(cfg,{'messages':s['messages']+[{'role':'user','content':'변경된 요청'}]})
    conn.close();store.close()
    store=Storage(tmp_path);graph,conn=setup(store,s,calls,Fake([]),mode='off')
    with pytest.raises(ValueError):graph.invoke(None,cfg)
    assert not calls and not store.agents()
    conn.close();store.close()


@pytest.mark.parametrize('decision',['clarify','manual_review','system_error','missing_capability'])
def test_direct_storage_save_cannot_bypass_gate(tmp_path,decision):
    store=Storage(tmp_path);s=state();s.update(draft=workflow_draft(s),confirmed=True,status='ready',
                                            capability_assessment=assessment(s,decision),capability_enforced=True)
    s['capability_fingerprint']=input_fingerprint(s)
    with pytest.raises(ValueError):store.save_agent(s)
    assert not store.agents();store.close()


def test_existing_tools_without_fingerprint_and_contradictory_payload_cannot_save(tmp_path):
    store=Storage(tmp_path);s=state();s.update(draft=workflow_draft(s),confirmed=True,status='ready',capability_assessment=assessment(s))
    with pytest.raises(ValueError):store.save_agent(s)
    s['capability_fingerprint']=input_fingerprint(s);s['capability_assessment']['missing_conditions']=['남은 조건']
    with pytest.raises(ValueError):store.save_agent(s)
    assert not store.agents();store.close()


@pytest.mark.parametrize('reply',['모르겠어','나중에 결정'])
def test_condition_nonanswer_survives_capability_recheck_and_restart(tmp_path,reply):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':None,
                    'missing.policy':'error','duplicate.policy':'keep','output.format':'json'})
    cap=Fake([assessment(s),assessment(s)]);cond=Fake([raw])
    graph,conn=build_graph(store,conversation=complete(calls),capability_mode='enforce',capability_transport=cap,
                          condition_mode='enforce',condition_transport=cond,verifier_mode='off')
    current=graph.invoke(s,cfg);qid=current['pending_questions'][0]['id']
    current=graph.invoke(Command(resume={'type':'message','content':reply,'question_id':qid}),cfg)
    assert cap.calls==1 and cond.calls==1 and not calls and not store.agents()
    assert current['condition_ledger']['decision']==('deferred' if reply=='나중에 결정' else 'clarify')
    assert current['condition_ledger']['slots']['missing.policy']['value']=='error'
    later={**current,'messages':current['messages']+[{'role':'user','content':'100만원 초과로 해줘.'}]}
    conn.close();store.close();store=Storage(tmp_path)
    graph,conn=build_graph(store,conversation=complete(calls),capability_mode='off',capability_transport=Fake([assessment(s)]),
                          condition_mode='enforce',condition_transport=Fake([proposal(later,{'filter.value':'1000000'})]),verifier_mode='off')
    current=graph.invoke(Command(resume={'type':'message','content':'100만원 초과로 해줘.'}),cfg)
    assert current['status']=='ready' and calls==['existing_tools']
    assert current['condition_ledger']['slots']['missing.policy']['value']=='error'
    assert graph.invoke(Command(resume={'type':'confirm'}),cfg)['status']=='saved'
    conn.close();store.close()


def test_worker_upload_invalidates_ready_support_and_requires_recheck(tmp_path,monkeypatch):
    import agent_builder.worker as worker
    original=worker.build_graph;seed=state();calls=[];cap=Fake([assessment(seed),assessment(seed,'manual_review')])
    def wrapped(*args,**kwargs):
        kwargs.update(capability_mode='enforce',capability_transport=cap,condition_mode='off',verifier_mode='off')
        return original(*args,**kwargs)
    monkeypatch.setattr(worker,'build_graph',wrapped)
    s=handle({'directory':str(tmp_path),'action':'new_session','model':'test','credential_id':uid()})
    def act(action,**extra):return handle({'directory':str(tmp_path),'id':s['id'],'revision':s['revision'],'action':action,**extra},conversation=complete(calls))
    s=act('message',content=seed['messages'][0]['content']);assert s['status']=='ready'
    s=act('upload',asset={'name':'new.csv','kind':'table','columns':['비용'],'profile':{}},rows=[{'비용':1}])
    with pytest.raises(ValueError):act('confirm')
    s=act('message',content='새 자료를 기준으로 다시 검토해줘.')
    assert s['status']=='validation_failed' and cap.calls==2 and calls==['existing_tools']
    store=Storage(tmp_path);assert not store.agents();store.close()


def test_invalid_capability_confirm_can_be_followed_by_correct_answer(tmp_path):
    store=Storage(tmp_path);s=state();cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    graph,conn=setup(store,s,calls,Fake([assessment(s,'clarify'),assessment(s)]))
    graph.invoke(s,cfg)
    blocked=graph.invoke(Command(resume={'type':'confirm'}),cfg)
    assert blocked['__interrupt__'][0].value['error'] and not calls and not store.agents()
    ready=graph.invoke(Command(resume={'type':'message','content':'담당자는 운영팀입니다.'}),cfg)
    assert ready['status']=='ready' and calls==['existing_tools']
    conn.close();store.close()


@pytest.mark.parametrize('configured',['off','shadow'])
def test_condition_enforcement_cannot_silently_downgrade(configured):
    from agent_builder.nodes.condition_check import make_condition_check
    s=state();s['condition_enforced']=True
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':None,
                    'missing.policy':'error','duplicate.policy':'keep','output.format':'json'})
    result=make_condition_check(transport=Fake([raw]),mode=configured)(s)
    assert result['condition_enforced'] and result['condition_required']
    assert result['condition_route']=='supervisor' and result['status']=='needs_clarification'


@pytest.mark.parametrize('configured',['off','shadow'])
def test_verifier_enforcement_cannot_silently_downgrade(tmp_path,configured):
    from agent_builder.nodes.verification import make_verification_node
    from agent_builder.test_verifier_bridge import seed,fixture_verifier
    store=Storage(tmp_path);s,draft=seed(store);s.update(draft=draft,verification_enforced=True)
    result=make_verification_node(store,verifier=fixture_verifier,mode=configured)(s)
    assert result['verification_enforced'] and result['verification_required']
    store.close()


@pytest.mark.parametrize('gate',['capability','condition','verification'])
def test_direct_storage_requires_each_enforced_gate_and_confirmation(tmp_path,gate):
    store=Storage(tmp_path);s=state();s.update(draft=workflow_draft(s),confirmed=True,status='ready')
    if gate=='capability':
        s.update(capability_enforced=True,capability_assessment=assessment(s));s['capability_fingerprint']=input_fingerprint(s)
        s['confirmed']=False
    elif gate=='condition':s.update(condition_required=True,condition_ledger={'decision':'clarify'})
    else:s.update(verification_required=True,verification_route='manual_review')
    with pytest.raises(ValueError):store.save_agent(s)
    assert not store.agents();store.close()
