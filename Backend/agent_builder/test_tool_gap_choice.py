import pytest
from copy import deepcopy
from agent_builder.worker import handle
from agent_builder.storage import uid,Storage
from agent_builder.test_tool_gap import session_gap


def select(directory,session,choice,**overrides):
    gap=session['tool_gap']
    return handle({'directory':str(directory),'action':'tool_gap_choice','id':session['id'],
        'revision':session['revision'],'gap_id':gap['gap_id'],'state_fingerprint':gap['state_fingerprint'],
        'request_id':uid(),'choice':choice,**overrides})

@pytest.mark.parametrize('choice,status,action',[
    ('create_tool','creation_requested','review_spec_then_generate'),
    ('redesign_workflow','redesign_requested','propose_existing_tool_alternative'),
    ('defer','deferred','wait_for_user'),
])
def test_explicit_choice_persists_restart_and_preserves_conditions(tmp_path,choice,status,action):
    initial=session_gap(tmp_path);before=deepcopy(initial)
    assert initial['tool_gap_choice']['status']=='awaiting_choice'
    result=select(tmp_path,initial,choice)
    assert result['revision']==initial['revision']+1
    assert result['tool_gap_choice']['status']==status
    assert result['tool_gap_choice']['next_action']==action
    for field in ['draft','messages','condition_ledger']:
        assert result[field]==before[field]
    assert not result['tool_gap_choice']['workflow_execution_allowed']
    intent=result['tool_gap']['creation_intent']
    assert bool(intent)==(choice=='create_tool')
    if intent:assert intent['scope']=='code_generation_and_lab_tests_only'
    if choice=='redesign_workflow':
        assert result['redesign_context']['original_user_messages'][0]==before['messages'][0]
        assert result['redesign_context']['draft']==before['draft']
        assert not result['redesign_context']['requirement_changes_approved']
    reopened=handle({'directory':str(tmp_path),'action':'session','id':result['id']})
    assert reopened['tool_gap_choice']==result['tool_gap_choice']
    store=Storage(tmp_path)
    assert not store.agents()
    assert store.db.execute('select count(*) from agent_tests').fetchone()[0]==0
    store.close()


def test_defer_resume_and_revoke_creation(tmp_path):
    s=session_gap(tmp_path)
    for choice in ['defer','create_tool','redesign_workflow','defer']:
        s=select(tmp_path,s,choice)
        assert bool(s['tool_gap']['creation_intent'])==(choice=='create_tool')
    assert len(s['tool_gap_choice_events'])==4


def test_exact_retry_idempotent_and_other_click_stale(tmp_path):
    s=session_gap(tmp_path);request=uid()
    result=select(tmp_path,s,'create_tool',request_id=request)
    retried=select(tmp_path,s,'create_tool',request_id=request)
    assert result['revision']==retried['revision']
    assert len(retried['tool_gap_choice_events'])==1
    with pytest.raises(ValueError):select(tmp_path,s,'defer',request_id=request)
    with pytest.raises(ValueError):select(tmp_path,s,'create_tool')
    later=select(tmp_path,result,'defer')
    with pytest.raises(ValueError):select(tmp_path,s,'create_tool',request_id=request)
    assert later['tool_gap']['creation_intent'] is None


@pytest.mark.parametrize('change',[
    {'revision':0},{'revision':True},{'revision':'1'},
    {'gap_id':'gap_'+'0'*24},{'state_fingerprint':'0'*64},
    {'choice':'yes'},{'choice':[]},{'request_id':'bad'},
])
def test_invalid_or_stale_selection_rejected(tmp_path,change):
    s=session_gap(tmp_path)
    with pytest.raises(ValueError):select(tmp_path,s,'create_tool',**change) if 'choice' not in change else select(tmp_path,s,change['choice'])


def test_upload_invalidates_selection(tmp_path):
    s=select(tmp_path,session_gap(tmp_path),'create_tool')
    s=handle({'directory':str(tmp_path),'action':'upload','id':s['id'],'revision':s['revision'],
        'asset':{'name':'new.csv','kind':'table','columns':['phone'],'profile':{}},'rows':[{'phone':'12345678'}]})
    assert s['tool_gap_choice'] is None and s['tool_gap'] is None


def test_catalog_change_revokes_intent(tmp_path,monkeypatch):
    import agent_builder.tool_gap as module
    s=select(tmp_path,session_gap(tmp_path),'create_tool')
    monkeypatch.setattr(module,'TOOLS',[{**t,'version':t['version']+1} for t in module.TOOLS])
    s=handle({'directory':str(tmp_path),'action':'session','id':s['id']})
    assert s['tool_gap_choice']['status']=='awaiting_choice'
    assert s['tool_gap']['creation_intent'] is None


def job_claim(tmp_path,s,hash_value='a'*64):
    return handle({'directory':str(tmp_path),'id':s['id'],'revision':s['revision'],
        'action':'tool_gap_job_claim','input_sha256':hash_value})


def job_result():
    return {'status':'passed_lab_tests','task_id':'date_normalize','model_calls':1,
            'passed_cases':4,'total_cases':4,'response_seconds':1.2}


def test_generation_claim_once_and_result_visible_without_changing_revision(tmp_path):
    s=select(tmp_path,session_gap(tmp_path),'create_tool')
    first=job_claim(tmp_path,s);assert first['claimed']
    second=job_claim(tmp_path,s);assert not second['claimed'];assert first['job']==second['job']
    with pytest.raises(ValueError):job_claim(tmp_path,s,'b'*64)
    payload={'directory':str(tmp_path),'id':s['id'],'action':'tool_gap_job_finish','job_id':first['job']['id'],'lease_token':first['lease_token'],'result':job_result()}
    done=handle(payload)['job'];assert done['status']=='passed_lab_tests'
    assert handle(payload)['job']==done
    with pytest.raises(ValueError):handle({**payload,'result':{**job_result(),'response_seconds':9}})
    reopened=handle({'directory':str(tmp_path),'id':s['id'],'action':'session'})
    assert reopened['revision']==s['revision'];assert reopened['tool_gap_job']==done
    assert not reopened['tool_gap_job']['workflow_execution_allowed']


@pytest.mark.parametrize('choice',['defer','redesign_workflow'])
def test_no_generation_claim_without_creation_choice(tmp_path,choice):
    s=select(tmp_path,session_gap(tmp_path),choice)
    with pytest.raises(ValueError):job_claim(tmp_path,s)


def test_revoke_during_generation_marks_result_stale(tmp_path):
    s=select(tmp_path,session_gap(tmp_path),'create_tool');claimed=job_claim(tmp_path,s);job=claimed['job']
    s=select(tmp_path,s,'defer')
    done=handle({'directory':str(tmp_path),'id':s['id'],'action':'tool_gap_job_finish','job_id':job['id'],'lease_token':claimed['lease_token'],'result':job_result()})['job']
    assert done['status']=='stale' and not done['current_state_revalidated']
    assert handle({'directory':str(tmp_path),'id':s['id'],'action':'session'})['tool_gap_job'] is None


@pytest.mark.parametrize('change',[
 {'passed_cases':5},{'total_cases':0},{'response_seconds':float('nan')},{'model_calls':True},
 {'status':'registered'},{'task_id':'../oops'},
])
def test_bad_job_result_rejected(tmp_path,change):
    s=select(tmp_path,session_gap(tmp_path),'create_tool');claimed=job_claim(tmp_path,s);job=claimed['job']
    with pytest.raises(ValueError):
        handle({'directory':str(tmp_path),'id':s['id'],'action':'tool_gap_job_finish','job_id':job['id'],'lease_token':claimed['lease_token'],
                'result':{**job_result(),**change}})


def test_concurrent_claims_have_one_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    s=select(tmp_path,session_gap(tmp_path),'create_tool')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:job_claim(tmp_path,s),range(2)))
    assert sum(r['claimed'] for r in results)==1
    assert results[0]['job']['id']==results[1]['job']['id']
