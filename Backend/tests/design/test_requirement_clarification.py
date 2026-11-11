"""Unresolved business inputs wait for a user; malformed extraction still fails closed."""
from copy import deepcopy
import json

import pytest
from langgraph.types import Command

from agent_builder.design.graph import build_graph
from agent_builder.design.nodes.save import make_save_node
from agent_builder.design.requirement_ledger import make_requirement_check
from contracts.models import AgentDraft
from infrastructure.persistence.storage import Storage
from tests.design.test_requirement_coverage import Extraction, seed


def fresh():
    state=seed()
    for key in list(state):
        if key.startswith('requirement_'):state.pop(key)
    return state


def uncertain_plan(questions=None):
    plan=deepcopy(seed()['requirement_ledger'])
    plan['unresolved']=questions or ['추가로 적용할 처리 기준이 있나요?']
    return plan


def test_unresolved_inputs_are_questions_not_generation_errors():
    state=fresh();state.update(issues=['이전 오류'],confirmed=True,saved_agent_id='old')
    result=make_requirement_check('fixture',Extraction(uncertain_plan()),mode='enforce')(state)
    assert result['requirement_route']=='clarify'
    assert result['status']=='needs_clarification' and result['issues']==[]
    assert not result['confirmed'] and result['saved_agent_id'] is None
    assert result['draft']['workflow'] is None
    assert result['draft']['tool_ids']==[]
    assert AgentDraft.model_validate(result['draft']).questions==result['requirement_ledger']['unresolved']
    assert result['pending_questions'][0]['source']=='requirements'
    assert result['messages'][-1]['content'].endswith(result['draft']['questions'][0])


def test_question_limit_does_not_drop_unresolved_conditions():
    questions=[f'조건 {i}을 알려주세요.' for i in range(5)]
    result=make_requirement_check('fixture',Extraction(uncertain_plan(questions)),mode='enforce')(fresh())
    assert result['draft']['questions']==questions[:3]
    assert len(result['pending_questions'])==3
    assert result['requirement_ledger']['unresolved']==questions
    assert len({q['id'] for q in result['pending_questions']})==3


def test_cached_unresolved_ledger_cannot_skip_question_gate():
    check=make_requirement_check('fixture',Extraction(uncertain_plan()),mode='enforce')
    state=fresh();first=check(state)
    again=check({**state,**first})
    assert again['requirement_route']=='clarify'
    assert again['pending_questions']==first['pending_questions']


def test_unresolved_checkpoint_remains_unsavable_when_environment_is_off(tmp_path):
    state=fresh();state['requirement_required']=True
    result=make_requirement_check('fixture',Extraction(uncertain_plan()),mode='off')(state)
    assert result['requirement_route']=='clarify'
    store=Storage(tmp_path)
    try:
        with pytest.raises(ValueError,match='요구사항'):
            make_save_node(store)({**state,**result,'draft':seed()['draft'],'confirmed':True})
        assert not store.agents()
    finally:store.close()


def test_blank_unresolved_response_still_fails_as_a_contract_error():
    result=make_requirement_check('fixture',Extraction(uncertain_plan(['  '])),mode='enforce')(fresh())
    assert result['requirement_route']=='stop'
    assert result['status']=='validation_failed'


@pytest.mark.parametrize('capability_mode',['off','enforce'])
def test_graph_restart_answer_recheck_and_explicit_save(tmp_path,capability_mode):
    state=fresh();correct=deepcopy(state['draft']);state['draft']=None
    unresolved=Extraction(uncertain_plan());resolved=Extraction(seed()['requirement_ledger'])
    planned=[]

    class Extractor:
        def invoke(self,messages):
            return (unresolved if not unresolved.calls else resolved).invoke(messages)

    def planner(current):
        planned.append(deepcopy(current))
        return dict(draft=deepcopy(correct),confirmed=False)

    class Capability:
        def invoke(self,messages):
            return dict(content=json.dumps(dict(decision='existing_tools',tool_ids=correct['tool_ids'],
                missing_conditions=[],missing_actions=[],reason='제어된 지원 판단',
                user_quotes=[state['messages'][0]['content']]),ensure_ascii=False))

    def open_graph(store):
        return build_graph(store,conversation=planner,capability_mode=capability_mode,
            capability_transport=Capability(),condition_mode='off',
            verifier_mode='off',requirement_mode='enforce',requirement_transport=Extractor())

    config={'configurable':{'thread_id':state['session_id']}}
    store=Storage(tmp_path);graph,connection=open_graph(store)
    try:
        first=graph.invoke(state,config)
        assert first['status']=='needs_clarification' and not planned
        interrupt=first['__interrupt__'][0].value
        assert interrupt['kind']=='clarification'
        question=interrupt['question_records'][0]
        assert question['text']==first['draft']['questions'][0]
        assert unresolved.calls==1 and not store.agents()
    finally:connection.close();store.close()
    # Resume after reopening the persisted graph, including stale-control rejection.
    store=Storage(tmp_path);graph,connection=open_graph(store)
    try:
        stale=graph.invoke(Command(resume=dict(type='message',content='추가 조건 없음',question_id='stale')),config)
        assert stale['__interrupt__'][0].value['error'] and not planned
        assert resolved.calls==0
        ready=graph.invoke(Command(resume=dict(type='message',content='추가 조건 없음',question_id=question['id'])),config)
        assert ready['status']=='ready'
        assert ready['__interrupt__'][0].value['kind']=='review'
        assert len(planned)==1 and resolved.calls==1
        assert planned[0]['messages'][0]==state['messages'][0]
        assert planned[0]['messages'][-1]['content']=='추가 조건 없음'
        assert not ready['pending_questions']
        assert ready.get('condition_answers',[])==[]
        assert not store.agents()
        saved=graph.invoke(Command(resume={'type':'confirm'}),config)
        assert saved['status']=='saved' and len(store.agents())==1
    finally:connection.close();store.close()


def test_requirement_answer_is_not_recorded_as_a_condition_slot(monkeypatch):
    from agent_builder.design.nodes import await_input
    state=fresh()
    state.update(make_requirement_check('fixture',Extraction(uncertain_plan()),mode='enforce')(state))
    state['condition_enforced']=True
    question=state['pending_questions'][0]
    monkeypatch.setattr(await_input,'interrupt',lambda payload:dict(type='message',content='추가 없음',question_id=question['id']))
    result=await_input.await_input_node(state)
    assert 'condition_answers' not in result
    assert result['messages'][-1]['content']=='추가 없음'
