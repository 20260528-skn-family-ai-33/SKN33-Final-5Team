"""Graph/SQLite handoff and conflict recovery with controlled model responses."""
from copy import deepcopy
import json
import pytest
from langgraph.types import Command
from agent_builder.graph import build_graph
from agent_builder.storage import Storage,uid
from agent_builder.worker import handle
from agent_builder.tool_gap_choice import choose,redesign_context
from agent_builder.tool_gap import review_gap_conditions
from agent_builder.test_condition_readiness import state,proposal,checked,Fake,workflow_draft
from agent_builder.test_capability_gate import assessment,complete

PROMPT='비용 큰 거래만 골라서 매주 이메일로 보내줘.'

@pytest.mark.parametrize('choice',['create_tool','redesign_workflow','defer'])
def test_gap_and_unresolved_candidates_persist_across_each_choice_and_restart(tmp_path,choice):
    store=Storage(tmp_path);session=store.new_session('test',uid());s=state(PROMPT);s['session_id']=session['id'];calls=[]
    model=Fake([assessment(s,'missing_capability'),assessment(s,'missing_capability')])
    graph,connection=build_graph(store,conversation=complete(calls),capability_mode='enforce',capability_transport=model,condition_mode='enforce',verifier_mode='off')
    cfg={'configurable':{'thread_id':s['session_id']}};first=graph.invoke(s,cfg)
    assert first['status']=='unsupported' and not calls and not store.agents()
    review=first['gap_condition_review'];assert not review['complete'] and review['requires_condition_recheck']
    assert {x['key'] for x in review['candidate_conditions']}=={'filter.threshold','schedule.timing','delivery.recipient'}
    assert first['tool_gap']['condition_review']==review
    session=store.update_session(session,session['revision']);before=graph.get_state(cfg).values
    gap=first['tool_gap'];data={'revision':session['revision'],'choice':choice,'gap_id':gap['gap_id'],
        'state_fingerprint':gap['state_fingerprint'],'request_id':uid()}
    chosen=choose(store,before,session,data);assert len(chosen['tool_gap_choice_events'])==1
    connection.close();store.close()
    store=Storage(tmp_path);graph,connection=build_graph(store,conversation=complete(calls),capability_mode='off',condition_mode='off',verifier_mode='off')
    reopened=graph.get_state(cfg).values;current=store.session(session['id'])
    from agent_builder.tool_gap_choice import present_choice,creation_intent
    selection=present_choice(reopened,current)
    assert selection['condition_review']==review and selection['selection']['choice']==choice
    assert not selection['workflow_execution_allowed'] and not selection['requirement_changes_approved']
    if choice=='redesign_workflow':assert redesign_context(reopened,current)['condition_review']==review
    if choice=='create_tool':
        intent=creation_intent(reopened,current)
        assert intent['scope']=='code_generation_and_lab_tests_only'
        assert selection['condition_review']==review  # Paired gap packet, not strict intent schema.
    assert not store.agents() and store.db.execute('select count(*) from agent_tests').fetchone()[0]==0
    connection.close();store.close()


def test_previous_condition_ledger_is_copied_and_not_a_ready_claim():
    s=state(PROMPT);s['condition_ledger']={'decision':'clarify','slots':{'filter.value':{'status':'missing','value':None}}}
    review=review_gap_conditions(s);s['condition_ledger']['slots']['filter.value']['value']=100
    assert review['previous_condition_ledger']['slots']['filter.value']['value'] is None
    assert not review['allows_generation'] and not review['allows_execution']


def test_review_change_invalidates_gap_fingerprint_and_does_not_assert_missing():
    from agent_builder.tool_gap import describe_gap
    s=state(PROMPT);s.update(status='unsupported',draft={'unsupported':['외부 이메일 전송']})
    s['gap_condition_review']=review_gap_conditions(s)
    first=describe_gap(s)
    s['gap_condition_review']['candidate_conditions'][0]['description']='새 검토 내용'
    assert describe_gap(s)['gap_id']!=first['gap_id']
    assert all(c['status']=='review_required' for c in s['gap_condition_review']['candidate_conditions'])
    assert not s['gap_condition_review']['complete']


def test_correction_to_conflicting_policy_and_approved_save(tmp_path):
    text='비용 100만원 초과만 JSON으로 저장해. 빈 값은 오류. 중복 거래ID는 첫 행만 남겨. 중복 거래ID는 마지막 행만 남겨.'
    s=state(text);store=Storage(tmp_path);cfg={'configurable':{'thread_id':s['session_id']}};calls=[]
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000','duplicate.field':'거래ID','duplicate.policy':'last','missing.policy':'error','output.format':'json'})
    raw['operation']='mixed';raw.pop('operations')
    later=deepcopy(s);later['messages'] += [{'role':'assistant','content':'첫 행과 마지막 행 중 무엇을 남길까요?'},{'role':'user','content':'마지막 행으로 해줘.'}]
    next_raw=proposal(later,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000','duplicate.field':'거래ID','duplicate.policy':'last','missing.policy':'error','output.format':'json'},['deduplicate'])
    # Simulate the real model's erroneous conflict on an untouched amount.
    next(x for x in next_raw['conditions'] if x['key']=='filter.value').update(status='conflict',value=None)
    # Value source is the first turn; duplicate correction is independently bound to the latest.
    for item in next_raw['conditions']:
        item['evidence_ids']=['user:0:0']
    def draft_for(ready):
        d=workflow_draft(ready);plan=d['execution_plan'];plan.update(duplicate_field='거래ID',duplicate_policy='last')
        d['workflow']['nodes'][1]['settings']['execution_plan']=deepcopy(plan)
        return {'draft':d,'confirmed':False,'saved_agent_id':None}
    caps=Fake([assessment(s),assessment(s)])
    graph,conn=build_graph(store,conversation=draft_for,capability_mode='enforce',capability_transport=caps,condition_mode='enforce',condition_transport=Fake([raw,next_raw]),verifier_mode='off')
    first=graph.invoke(s,cfg);assert first['status']=='needs_clarification' and first['condition_ledger']['slots']['duplicate.policy']['status']=='conflict'
    assert not store.agents()
    conn.close();store.close();store=Storage(tmp_path)
    caps=Fake([assessment(s)]);graph,conn=build_graph(store,conversation=draft_for,capability_mode='enforce',capability_transport=caps,condition_mode='enforce',condition_transport=Fake([next_raw]),verifier_mode='off')
    snap=graph.get_state(cfg).values
    current=graph.invoke(Command(resume={'type':'message','content':'마지막 행으로 해줘.'}),cfg)
    assert current['condition_ledger']['slots']['duplicate.policy']['value']=='last'
    assert 'filter' in current['condition_ledger']['operations']
    assert current['status']=='ready' and not store.agents()
    saved=graph.invoke(Command(resume={'type':'confirm'}),cfg)
    assert saved['status']=='saved' and len(store.agents())==1
    conn.close();store.close()


@pytest.mark.parametrize('reply',['모르겠어','첫 행이나 마지막 행','마지막 행은 아니야','일단 보류'])
def test_ambiguous_or_negative_conflict_reply_cannot_be_confirmed(reply):
    s=state('중복 거래ID는 첫 행만 남겨. 중복 거래ID는 마지막 행만 남겨.')
    raw=proposal(s,{'duplicate.field':'거래ID','duplicate.policy':'last'},['deduplicate'])
    initial=checked(s,raw);assert initial['slots']['duplicate.policy']['status']=='conflict'
    s['condition_ledger']=initial;s['messages']+=[{'role':'user','content':reply}]
    candidate=proposal(s,{'duplicate.field':'거래ID','duplicate.policy':'last'},['deduplicate'])
    candidate['conditions'][0]['evidence_ids']=['user:0:0']
    result=checked(s,candidate)
    assert result['slots']['duplicate.policy']['status']!='specified'


def test_new_amount_disagreement_is_not_hidden_by_preservation():
    s=state('비용 100만원 초과만 JSON으로 저장해.')
    initial=checked(s,proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000'}))
    s['condition_ledger']=initial;s['messages']+=[{'role':'user','content':'비용 기준을 200만원 초과로 변경해줘.'}]
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':None})
    item=next(x for x in raw['conditions'] if x['key']=='filter.value')
    item.update(status='conflict',value=None,evidence_ids=['user:0:0'])
    result=checked(s,raw)
    assert result['slots']['filter.value']['status']=='conflict'
    assert result['decision']=='clarify'
