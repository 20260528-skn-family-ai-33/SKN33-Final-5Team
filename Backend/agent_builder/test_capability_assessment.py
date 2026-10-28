"""Runtime contract conflicts must never become business questions or save permits."""
from copy import deepcopy
import pytest
from langgraph.types import Command
from agent_builder.capability_judgement import assess_capability,validate_assessment_result,AssessmentConflictError
from agent_builder.nodes.capability_check import make_capability_check
from agent_builder.nodes.save import make_save_node
from agent_builder.graph import build_graph
from agent_builder.storage import Storage
from agent_builder.test_condition_readiness import state,proposal,checked,Fake,workflow_draft
from agent_builder.test_input_resolution import waiting,respond
from agent_builder.worker import handle
from agent_builder.storage import uid


def packet(s,decision='existing_tools',conditions=None,actions=None,tools=None):
    return {'decision':decision,'tool_ids':['read_table'] if tools is None else tools,
            'missing_conditions':conditions or [],'missing_actions':actions or [],
            'reason':'검증용 지원 판단','user_quotes':[s['messages'][0]['content']]}


@pytest.mark.parametrize('decision',['existing_tools','clarify','missing_capability','manual_review'])
@pytest.mark.parametrize('has_conditions',[False,True])
@pytest.mark.parametrize('has_actions',[False,True])
@pytest.mark.parametrize('has_tools',[False,True])
def test_runtime_decision_contract_matrix(decision,has_conditions,has_actions,has_tools):
    s=state();p=packet(s,decision,['금액 기준'] if has_conditions else [],
                      ['외부 이메일 전송'] if has_actions else [],['read_table'] if has_tools else [])
    valid=(decision=='manual_review' or decision=='existing_tools' and has_tools and not has_conditions and not has_actions
           or decision=='clarify' and has_conditions and not has_actions
           or decision=='missing_capability' and has_actions and not has_conditions)
    if valid:assert validate_assessment_result(p,s)==p
    else:
        with pytest.raises(AssessmentConflictError):validate_assessment_result(p,s)


@pytest.mark.parametrize('stage',['initial','review'])
def test_every_model_response_is_checked_before_routing(stage):
    s=state();bad=packet(s,'clarify',['금액 기준'],['외부 이메일 전송'])
    responses=[bad] if stage=='initial' else [packet(s,'missing_capability',actions=['외부 이메일 전송']),bad]
    transport=Fake(responses)
    with pytest.raises(AssessmentConflictError) as error:assess_capability(s,transport)
    assert transport.calls==len(responses)
    assert error.value.model_calls==len(responses)
    assert error.value.reported=={'decision':'clarify','missing_conditions':['금액 기준'],'missing_actions':['외부 이메일 전송']}


@pytest.mark.parametrize('condition_mode',['off','enforce'])
@pytest.mark.parametrize('decision',['clarify','missing_capability','existing_tools'])
def test_conflict_stops_graph_and_cannot_be_confirmed_or_directly_saved(tmp_path,condition_mode,decision):
    store=Storage(tmp_path);s=state();calls=[]
    bad=packet(s,decision,['금액 기준'],['외부 이메일 전송'])
    def conversation(current):calls.append(1);return {'draft':workflow_draft(current)}
    graph,conn=build_graph(store,conversation=conversation,capability_transport=Fake([bad]),
        capability_mode='enforce',condition_mode=condition_mode,verifier_mode='off')
    cfg={'configurable':{'thread_id':s['session_id']}}
    try:
        result=graph.invoke(s,cfg)
        assert result['status']=='validation_failed' and not result.get('__interrupt__')
        assert result['capability_assessment']['decision']=='system_error'
        assert result['capability_assessment']['assessment_conflict']['missing_conditions']==['금액 기준']
        assert result['capability_assessment']['assessment_conflict']['missing_actions']==['외부 이메일 전송']
        assert result['capability_assessment']['model_calls']==1
        assert not result['pending_questions'] and not calls and not store.agents()
        assert result['messages'][0]==s['messages'][0]
        conn.close();store.close()
        store=Storage(tmp_path)
        graph,conn=build_graph(store,conversation=conversation,capability_transport=Fake([]),
            capability_mode='off',condition_mode='off',verifier_mode='off')
        assert graph.invoke(Command(resume={'type':'confirm'}),cfg)['status']!='saved'
        with pytest.raises(ValueError):make_save_node(store)({**result,'draft':workflow_draft(s),'confirmed':True,'status':'ready'})
        assert not calls and not store.agents()
    finally:conn.close();store.close()


def test_worker_recovers_after_conflict_with_a_fresh_valid_assessment(tmp_path,monkeypatch):
    import agent_builder.worker as worker
    original=worker.build_graph;s=state();transport=Fake([
        packet(s,'clarify',['금액 기준'],['외부 이메일 전송']),packet(s)])
    calls=[]
    def wrapped(*args,**kwargs):
        kwargs.update(capability_mode='enforce',capability_transport=transport,
                      condition_mode='off',verifier_mode='off')
        return original(*args,**kwargs)
    monkeypatch.setattr(worker,'build_graph',wrapped)
    session=handle({'directory':str(tmp_path),'action':'new_session','model':'fixture','credential_id':uid()})
    def action(name,**fields):
        return handle({'directory':str(tmp_path),'id':session['id'],'revision':session['revision'],
                       'action':name,**fields},conversation=conversation)
    def conversation(current):calls.append(1);return {'draft':workflow_draft(current),'confirmed':False}
    session=action('message',content=s['messages'][0]['content'])
    assert session['status']=='validation_failed' and not calls
    with pytest.raises(ValueError):action('confirm')
    session=action('message',content='원래 요청을 다시 확인해줘.')
    assert session['status']=='ready' and len(calls)==1 and transport.calls==2
    session=action('confirm');assert session['status']=='saved'


@pytest.mark.parametrize('kind',['unknown','defer','guidance'])
def test_nonanswer_cannot_reuse_a_contradictory_old_assessment(kind):
    s=state();s['messages'].append({'role':'user','content':'모르겠어'})
    s['capability_assessment']=packet(s,'clarify',['금액 기준'],['외부 이메일 전송'])
    s['capability_answers']=[{'turn':1,'kind':kind,'question_ids':['old']}]
    original=deepcopy(s);transport=Fake([])
    update=make_capability_check('fixture',transport,mode='enforce')(s)
    assert transport.calls==0 and update['capability_route']=='stop'
    assert update['capability_assessment']['model_calls']==0
    assert update['capability_assessment']['assessment_conflict']['missing_actions']==['외부 이메일 전송']
    assert s==original


@pytest.mark.parametrize('change',[{'missing_conditions':[' ']},{'missing_actions':[' ']},{'reason':' '},
                                   {'tool_ids':['read_table','read_table']},{'tool_ids':['not_registered']},
                                   {'user_quotes':['원문에 없는 인용']}])
def test_empty_or_ungrounded_reports_are_not_accepted(change):
    s=state();p=packet(s,'manual_review');p.update(change)
    with pytest.raises(ValueError):validate_assessment_result(p,s)


def test_real_overlapping_causes_can_be_preserved_for_manual_review():
    s=state();p=packet(s,'manual_review',['금액 기준'],['외부 이메일 전송']);transport=Fake([p])
    result=assess_capability(s,transport)
    assert result['decision']=='manual_review' and result['missing_conditions']==p['missing_conditions']
    assert result['missing_actions']==p['missing_actions'] and not result['allows_generation']


def test_input_failure_takes_priority_without_model_calls():
    s=state();s['source_health']={'db_status':'timeout','messages_loaded':False};transport=Fake([])
    result=assess_capability(s,transport)
    assert result['decision']=='system_error' and transport.calls==0


@pytest.mark.parametrize('case,expected',[('unsupported','unsupported'),('fault','system_error'),('defer','deferred')])
def test_condition_overlap_priorities_keep_all_causes(case,expected):
    s=state('비용이 큰 행을 JSON으로 저장하고 이메일로 보내줘. 빈 값은 오류, 중복은 유지해.')
    if case=='fault':s['source_health']={'db_status':'timeout'}
    if case=='defer':s=respond(waiting(),'나중에 결정')
    p=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':None})
    p['unsupported_actions']=['외부 이메일 전송'];result=checked(s,p)
    assert result['decision']==expected and 'filter.value' in result['unresolved']
    assert result['unsupported_actions']==['외부 이메일 전송']
