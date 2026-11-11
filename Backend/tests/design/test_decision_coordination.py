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
        assert waiting['pending_questions'][0]['source']=='reconciliation'
        with pytest.raises(ValueError):make_save_node(store)({**waiting,'confirmed':True})
        ready=graph.invoke(Command(resume=dict(type='message',content='비용은 100만원 초과로 유지해 주세요.',
            question_id=waiting['pending_questions'][0]['id'])),config)
        assert ready['status']=='ready' and planned==[1]
        assert ready.get('condition_answers',[])==[]
        assert graph.invoke(Command(resume={'type':'confirm'}),config)['status']=='saved'
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
