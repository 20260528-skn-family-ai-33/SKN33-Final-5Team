"""Recovery policy tests using controlled judgments and real graph/SQLite checkpoints."""
from copy import deepcopy

import pytest
from langgraph.types import Command

from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.save import make_save_node
from agent_builder.design.nodes.supervisor import supervisor_node
from infrastructure.persistence.storage import Storage
from tests.capabilities.test_capability_assessment import packet
from tests.conditions.test_condition_readiness import state, proposal, checked, Fake, workflow_draft
from tests.design.test_requirement_coverage import Extraction, seed


def test_manual_capability_review_waits_then_reassesses_and_saves(tmp_path):
    current=state();calls=[]
    capability=Fake([packet(current,'manual_review',conditions=['검토 담당자']),packet(current)])
    store=Storage(tmp_path)
    graph,connection=build_graph(store,conversation=lambda s:(calls.append(1) or dict(draft=workflow_draft(s))),
        capability_mode='enforce',capability_transport=capability,condition_mode='off',
        requirement_mode='off',verifier_mode='off')
    config={'configurable':{'thread_id':current['session_id']}}
    try:
        waiting=graph.invoke(current,config)
        assert waiting['status']=='needs_clarification' and not calls
        assert waiting['__interrupt__'][0].value['scope']=='capability'
        blocked=graph.invoke(Command(resume={'type':'confirm'}),config)
        assert blocked['__interrupt__'][0].value['error'] and not store.agents()
        ready=graph.invoke(Command(resume=dict(type='message',content='검토 담당자는 운영팀입니다.',
            question_id=waiting['pending_questions'][0]['id'])),config)
        assert ready['status']=='ready' and calls==[1] and capability.calls==2
        assert graph.invoke(Command(resume={'type':'confirm'}),config)['status']=='saved'
    finally:connection.close();store.close()


@pytest.mark.parametrize('route',['ask_user','manual_review','repair_plan'])
def test_business_review_creates_a_question_but_never_approves(route,tmp_path):
    current=seed()
    current.update(verification_enforced=True,verification_required=True,verification_route=route,
        verification=dict(model_output=dict(missing_conditions=[dict(detail='출력할 항목을 확인해 주세요.')]),findings=[]),
        repair_count=2,issues=['출력 항목 확인 필요'])
    paused=supervisor_node(current)
    assert paused['route']=='await_input' and paused['status']=='needs_clarification'
    assert paused['draft']['questions'] and not paused['confirmed']
    store=Storage(tmp_path)
    try:
        with pytest.raises(ValueError):store.save_agent({**current,**paused,'confirmed':True})
        assert not store.agents()
    finally:store.close()


def test_verifier_connection_failure_is_not_presented_as_a_business_question():
    current=seed()
    current.update(verification_enforced=True,verification_route='manual_review',
                   verification=dict(error_code='timeout',model_output=None))
    result=supervisor_node(current)
    assert result['route']=='invalid' and result['status']=='validation_failed'


def test_condition_manual_review_is_a_resumable_question(tmp_path,monkeypatch):
    from agent_builder.conditions import condition_check
    original=condition_check.judge_conditions
    calls=[];planned=[];current=state()
    def judge(s,transport,**kwargs):
        calls.append(1)
        if len(calls)>1:
            return original(s,Fake([proposal(s)]),**kwargs)
        ledger=checked(s,proposal(s))
        ledger.update(decision='manual_review',outside_scope=False,unresolved=['filter.value'])
        return dict(ledger=ledger,model_calls=1,metrics={})
    monkeypatch.setattr(condition_check,'judge_conditions',judge)
    store=Storage(tmp_path)
    graph,connection=build_graph(store,conversation=lambda s:(planned.append(1) or dict(draft=workflow_draft(s))),
        capability_mode='off',condition_mode='enforce',condition_transport=Fake([]),
        requirement_mode='off',verifier_mode='off')
    config={'configurable':{'thread_id':current['session_id']}}
    try:
        waiting=graph.invoke(current,config)
        assert waiting['status']=='needs_clarification' and not planned
        assert waiting['pending_questions'][0]['source']=='condition'
        assert waiting['pending_questions'][0]['key']=='filter.value'
        with pytest.raises(ValueError):make_save_node(store)({**waiting,'confirmed':True})
        ready=graph.invoke(Command(resume=dict(type='message',content='비용은 100만원 초과로 유지해 주세요.',
            question_id=waiting['pending_questions'][0]['id'])),config)
        assert ready['status']=='ready' and planned==[1]
        assert ready['condition_answers'][-1]['candidate_keys']==['filter.value']
        assert graph.invoke(Command(resume={'type':'confirm'}),config)['status']=='saved'
    finally:connection.close();store.close()


@pytest.mark.parametrize('answer,policy,expected',[('마지막 행','last',2),('첫 행','first',1)])
@pytest.mark.parametrize('with_question_id',[True,False])
def test_condition_recovery_short_answer_survives_restart_and_executes(
        tmp_path,monkeypatch,answer,policy,expected,with_question_id):
    from agent_builder.conditions import condition_check
    original=condition_check.judge_conditions
    current=state('같은 사번이면 한 행만 남겨줘. 빈 값은 오류, JSON으로 저장해줘.')
    calls=[];planned=[]
    def judge(s,transport,**kwargs):
        calls.append(1)
        if len(calls)==1:
            ledger=checked(s,proposal(s,{'duplicate.field':'사번','missing.policy':'error',
                'output.format':'json'},['deduplicate']))
            assert ledger['unresolved']==['duplicate.policy']
            ledger.update(decision='manual_review',outside_scope=False,questions=[])
            return dict(ledger=ledger,model_calls=1,metrics={})
        # The model extracts the correct value from the short answer; the real
        # validator must connect its evidence to the pending condition slot.
        return original(s,Fake([proposal(s,{'duplicate.policy':policy},['deduplicate'])]),**kwargs)
    monkeypatch.setattr(condition_check,'judge_conditions',judge)
    def planner(s):
        planned.append(deepcopy(s))
        draft=workflow_draft(s)
        for plan in (draft['execution_plan'],draft['workflow']['nodes'][1]['settings']['execution_plan']):
            plan.update(filters=[],sort=[],duplicate_field='사번',duplicate_policy=policy)
        return dict(draft=draft,confirmed=False)
    def open_graph(store):
        return build_graph(store,conversation=planner,capability_mode='off',condition_mode='enforce',
            condition_transport=Fake([]),requirement_mode='off',verifier_mode='off')
    config={'configurable':{'thread_id':current['session_id']}}
    store=Storage(tmp_path);graph,connection=open_graph(store)
    try:
        waiting=graph.invoke(current,config)
        assert waiting['status']=='needs_clarification' and not planned
        question=waiting['pending_questions'][0]
        assert '중복된 행' in question['text']
        assert question['key']=='duplicate.policy' and question['source']=='condition'
        assert question['options']==waiting['draft']['question_options'][0]
        assert '마지막 행만 남김' in question['options']
    finally:connection.close();store.close()
    store=Storage(tmp_path);graph,connection=open_graph(store)
    try:
        stale=graph.invoke(Command(resume=dict(type='message',content=answer,question_id='stale')),config)
        assert stale['__interrupt__'][0].value['error'] and len(calls)==1
        reply=dict(type='message',content=answer)
        if with_question_id:reply['question_id']=question['id']
        ready=graph.invoke(Command(resume=reply),config)
        assert ready['status']=='ready', ready.get('pending_questions')
        assert ready['condition_ledger']['slots']['duplicate.policy']['value']==policy
        assert ready['condition_answers'][-1]['candidate_keys']==['duplicate.policy']
        assert ready['condition_answers'][-1]['question_ids']==[question['id']]
        assert len(planned)==1 and len(calls)==2 and not store.agents()
        saved=graph.invoke(Command(resume={'type':'confirm'}),config)
        assert saved['status']=='saved'
        store.add_asset({'id':'employees','name':'employees.json','kind':'table','columns':['사번','순번']},
            [{'사번':'A','순번':1},{'사번':'A','순번':2}])
        from service.worker import handle
        executed=handle(dict(directory=str(tmp_path),action='test',id=saved['saved_agent_id'],
            content='',file_ids=['employees']))
        assert executed['status']=='succeeded', executed.get('error')
        assert executed['rows']==[{'사번':'A','순번':expected}]
    finally:connection.close();store.close()


def test_repair_budget_pauses_then_user_retry_preserves_requirements(tmp_path):
    current=seed();correct=deepcopy(current['draft']);planned=[]
    extraction=Extraction(current['requirement_ledger'])
    for key in list(current):
        if key.startswith('requirement_'):current.pop(key)
    current['draft']=None
    def planner(s):
        planned.append(deepcopy(s))
        candidate=deepcopy(correct)
        if len([m for m in s['messages'] if m['role']=='user'])==1:
            candidate['workflow']['nodes'][2]['settings']['execution_plan']['sort']=[]
        return dict(draft=candidate,repair_count=s.get('repair_count',0)+1)
    store=Storage(tmp_path)
    graph,connection=build_graph(store,conversation=planner,capability_mode='off',condition_mode='off',
        requirement_mode='enforce',requirement_transport=extraction,verifier_mode='off')
    config={'configurable':{'thread_id':current['session_id']}}
    try:
        waiting=graph.invoke(current,config)
        assert waiting['status']=='needs_clarification' and len(planned)==2
        assert '자동 수정' in waiting['messages'][-1]['content']
        assert waiting['__interrupt__'][0].value['kind']=='clarification'
        with pytest.raises(ValueError):make_save_node(store)({**waiting,'confirmed':True})
        ready=graph.invoke(Command(resume=dict(type='message',content='원래 요청을 유지해 다시 설계해 주세요.',
            question_id=waiting['pending_questions'][0]['id'])),config)
        assert ready['status']=='ready' and len(planned)==3
        assert planned[-1]['repair_count']==0
        assert planned[-1]['messages'][0]==current['messages'][0]
        assert graph.invoke(Command(resume={'type':'confirm'}),config)['status']=='saved'
    finally:connection.close();store.close()


@pytest.mark.parametrize('stage',['capability','condition','requirements'])
def test_technical_failures_reach_coordinator_and_never_generate(tmp_path,stage):
    class Broken:
        def invoke(self,messages):raise RuntimeError('controlled provider failure')
    planned=[];current=state();store=Storage(tmp_path)
    options=dict(capability_mode='off',condition_mode='off',requirement_mode='off',verifier_mode='off')
    key='requirement' if stage=='requirements' else stage
    options.update({key+'_mode':'enforce',key+'_transport':Broken()})
    graph,connection=build_graph(store,conversation=lambda s:planned.append(1),**options)
    try:
        failed=graph.invoke(current,{'configurable':{'thread_id':current['session_id']}})
        assert failed['status']=='validation_failed' and not planned
        assert not failed.get('__interrupt__') and not store.agents()
        assert failed['trace'][-1]['node']=='supervisor'
        assert any(t['node']=='supervisor' and t['label']=='검사 오류 처리' for t in failed['trace'])
    finally:connection.close();store.close()
