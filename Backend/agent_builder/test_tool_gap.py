import pytest
from agent_builder.worker import handle
from agent_builder.storage import Storage,uid
from agent_builder.tool_gap import describe_gap
from agent_builder.test_builder import initial,draft

REQUEST='전화번호 열의 공백과 하이픈을 제거하는 변환이 필요해.'
def unsupported(state):
    return {'draft':{**draft(),'unsupported':['전화번호 정규화 기능이 필요합니다.']}}
def session_gap(tmp_path):
    s=handle({'directory':str(tmp_path),'action':'new_session','model':'test','credential_id':uid()})
    return handle({'directory':str(tmp_path),'action':'message','id':s['id'],'revision':0,'content':REQUEST},conversation=unsupported)

def test_unsupported_persists_gap_without_saving_or_executing(tmp_path):
    s=session_gap(tmp_path)
    assert s['status']=='unsupported'
    gap=s['tool_gap'];assert gap['protocol']=='a4b.tool-gap.v1'
    assert gap['capability_status']=='unverified'
    assert gap['user_messages'][0]['content']==REQUEST
    assert not gap['automatic_registration'] and not gap['automatic_generation'] and not gap['workflow_execution_allowed']
    assert len(gap['catalog'])==16
    assert any(x['node']=='tool_gap' for x in s['trace'])
    stored=handle({'directory':str(tmp_path),'action':'tool_gap','id':s['id'],'revision':s['revision']})['tool_gap']
    assert stored==gap
    store=Storage(tmp_path)
    assert not store.agents()
    assert store.db.execute('select count(*) from agent_tests').fetchone()[0]==0
    store.close()

@pytest.mark.parametrize('revision',[0,None,True,'1',-1])
def test_gap_export_rejects_stale_or_invalid_revision(tmp_path,revision):
    s=session_gap(tmp_path)
    with pytest.raises(ValueError,match='버전'):
        handle({'directory':str(tmp_path),'action':'tool_gap','id':s['id'],'revision':revision})

def test_ready_request_does_not_keep_old_gap(tmp_path):
    s=session_gap(tmp_path)
    s=handle({'directory':str(tmp_path),'action':'message','id':s['id'],'revision':s['revision'],'content':'기존 표 읽기와 JSON 저장으로 바꿀게.'},conversation=lambda state:{'draft':{**draft(),'requirements':[{'description':'요청 근거','source_id':'user','quote':REQUEST}]}})
    assert s['status']=='ready' and s['tool_gap'] is None
    with pytest.raises(ValueError,match='미지원'):
        handle({'directory':str(tmp_path),'action':'tool_gap','id':s['id'],'revision':s['revision']})

def test_resource_change_invalidates_gap_export(tmp_path):
    s=session_gap(tmp_path)
    s=handle({'directory':str(tmp_path),'action':'upload','id':s['id'],'revision':s['revision'],
       'asset':{'name':'a.csv','kind':'table','columns':['phone'],'profile':{}},'rows':[{'phone':'010-1234-5678'}]})
    assert s['tool_gap'] is None
    with pytest.raises(ValueError):handle({'directory':str(tmp_path),'action':'tool_gap','id':s['id'],'revision':s['revision']})

@pytest.mark.parametrize('decision',['system_error','clarify','deferred','manual_review','ready'])
def test_input_or_model_errors_are_not_capability_gaps(decision):
    s={**initial(),'status':'unsupported','condition_enforced':True,'condition_ledger':{'decision':decision,'unsupported_actions':['임의 미지원']},'draft':{**draft(),'unsupported':['임의 미지원']}}
    assert describe_gap(s) is None

def test_gap_fingerprint_changes_with_conversation_resource_and_catalog(monkeypatch):
    import agent_builder.tool_gap as module
    s={**initial(),'status':'unsupported','draft':{**draft(),'unsupported':['표 변환 필요']}}
    a=describe_gap(s)
    assert describe_gap({**s,'resources':[{'id':'a','name':'a','kind':'table','version':2}]})['gap_id']!=a['gap_id']
    assert describe_gap({**s,'messages':s['messages']+[{'role':'user','content':'새 조건'}]})['gap_id']!=a['gap_id']
    monkeypatch.setattr(module,'TOOLS',[{**t,'version':2} for t in module.TOOLS])
    assert describe_gap(s)['gap_id']!=a['gap_id']

@pytest.mark.parametrize('change',[
    {'resources':[{'id':'broken','kind':'document','text':'부분 본문','parser_status':'partial'}]},
    {'source_health':{'db_status':'timeout','messages_loaded':False}},
])
def test_input_failure_is_not_a_tool_gap_even_when_condition_mode_off(change):
    s={**initial(),'status':'unsupported','draft':{**draft(),'unsupported':['미지원 판정']},**change}
    assert describe_gap(s) is None
