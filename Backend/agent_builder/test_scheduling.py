import sys,json
from pathlib import Path
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from agent_builder.models import AgentDraft
from agent_builder.storage import Storage,uid
from agent_builder.scheduling import Schedules,next_occurrence
from agent_builder.workflow import legacy_workflow
from agent_builder.test_builder import draft,initial
from agent_builder.worker import handle

SPEC={'frequency':'daily','timezone':'Asia/Seoul','hour':9,'minute':0,'weekday':None}
def seed(tmp_path):
    store=Storage(tmp_path);asset={'id':uid(),'name':'stock.csv','kind':'table','format':'csv','columns':['code','stock','reorder_level'],'profile':{},'row_count':3,'preview':[],'text':''}
    store.add_asset(asset,[{'code':'A','stock':'20','reorder_level':'30'},{'code':'B','stock':'3','reorder_level':'10'},{'code':'C','stock':'10','reorder_level':'10'}])
    d=draft();d['document_ids']=[asset['id']];d['execution_plan'].update(filters=[{'field':'stock','operator':'lt','value':None,'compare_field':'reorder_level','value_type':'number'}],missing_policy='exclude',duplicate_policy='keep');d.update(schedule=SPEC,report_destination='reports');d['workflow']=legacy_workflow(d)
    state={**initial(),'draft':AgentDraft.model_validate(d).model_dump(),'confirmed':True};id=store.save_agent(state);return store,id

def test_timezone_daily_weekly_and_dst():
    current=datetime(2026,10,12,0,0,tzinfo=timezone.utc)
    assert next_occurrence(SPEC,current)=='2026-10-13T00:00:00+00:00'
    weekly={**SPEC,'frequency':'weekly','weekday':0};assert next_occurrence(weekly,current)=='2026-10-19T00:00:00+00:00'
    before=datetime(2026,10,11,23,59,tzinfo=timezone.utc);assert next_occurrence(SPEC,before)=='2026-10-12T00:00:00+00:00'
    dst={**SPEC,'timezone':'America/New_York','hour':2,'minute':30};assert next_occurrence(dst,datetime(2026,3,8,5,tzinfo=timezone.utc))=='2026-03-08T07:30:00+00:00'

def test_claim_is_unique_across_process_connections_and_persists_restart(tmp_path):
    store,id=seed(tmp_path);s=Schedules(store);s.activate(id,True);due=datetime.now(timezone.utc)-timedelta(seconds=1)
    store.db.execute('UPDATE builder_schedules SET next_at=? WHERE agent_id=?',(due.isoformat(),id));store.db.commit();store.close()
    def claim():
        st=Storage(tmp_path)
        try:return Schedules(st).claim_due()
        finally:st.close()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:claim(),range(2)))
    assert sum(len(r) for r in results)==1
    store=Storage(tmp_path);s=Schedules(store);assert s.get(id)['active'];assert len(s.jobs(id))==1;assert not s.claim_due();store.close()

def test_dead_lease_recovery_old_owner_rejected(tmp_path):
    store,id=seed(tmp_path);s=Schedules(store);s.enqueue_test(id,0);first=s.claim_due()[0]
    future=datetime.now(timezone.utc)+timedelta(minutes=6);second=s.claim_due(future)[0];assert second['id']==first['id'];assert second['lease_token']!=first['lease_token']
    with pytest.raises(ValueError):s.finish(first['id'],first['lease_token'],'succeeded')
    s.finish(second['id'],second['lease_token'],'failed',error='테스트 오류');assert s.jobs(id)[0]['error']=='테스트 오류';store.close()

def test_now_execution_no_model_report_saved_and_source_refresh(tmp_path):
    store,id=seed(tmp_path);store.close()
    first=handle({'directory':str(tmp_path),'action':'test','id':id,'content':'지금 실행'})
    assert first['status']=='succeeded';assert [r['code'] for r in first['rows']]==['A','B'];assert first['source_version']==1;assert (tmp_path/first['report_path']).is_file()
    st=Storage(tmp_path);asset={**Schedules(st).source(id)['asset'],'id':uid(),'row_count':2};st.add_asset(asset,[{'code':'X','stock':'1','reorder_level':'3'},{'code':'Y','stock':'4','reorder_level':'3'}]);Schedules(st).replace_source(id,asset['id']);st.close()
    second=handle({'directory':str(tmp_path),'action':'test','id':id,'content':'다시 실행'});assert [r['code'] for r in second['rows']]==['X'];assert second['source_version']==2;assert first['agent_version']==second['agent_version']

def test_empty_result_and_numeric_error_are_distinct(tmp_path):
    store,id=seed(tmp_path);s=Schedules(store);a={**s.source(id)['asset'],'id':uid()};store.add_asset(a,[{'code':'A','stock':'30','reorder_level':'10'}]);s.replace_source(id,a['id']);store.close()
    run=handle({'directory':str(tmp_path),'action':'test','id':id,'content':'실행'});assert run['status']=='succeeded';assert run['rows']==[];assert run['artifact']['content'].lstrip('\ufeff').startswith('code,stock,reorder_level')
    store=Storage(tmp_path);s=Schedules(store);a={**s.source(id)['asset'],'id':uid()};store.add_asset(a,[{'code':'A','stock':'잘못된 숫자','reorder_level':'10'}]);s.replace_source(id,a['id']);store.close()
    run=handle({'directory':str(tmp_path),'action':'test','id':id,'content':'실행'});assert run['status']=='failed';assert not run['artifact'];assert run['steps'][-1]['status']=='failed'

def test_scheduled_job_run_id_pinned_and_one_report(tmp_path):
    store,id=seed(tmp_path);s=Schedules(store);s.enqueue_test(id,0);job=s.claim_due()[0];store.close()
    run=handle({'directory':str(tmp_path),'action':'scheduled_run','id':job['id'],'lease_token':job['lease_token']});assert run['status']=='succeeded';assert run['id']==job['id'];assert run['trigger']=='schedule_test';assert len(list((tmp_path/'reports').iterdir()))==1
    store=Storage(tmp_path);assert Schedules(store).jobs(id)[0]['status']=='succeeded';assert not Schedules(store).claim_due();store.close()

def test_changed_source_structure_rejected(tmp_path):
    store,id=seed(tmp_path);a={'id':uid(),'name':'wrong.csv','kind':'table','format':'csv','columns':['other'],'profile':{},'text':''};store.add_asset(a,[{'other':1}])
    with pytest.raises(ValueError):Schedules(store).replace_source(id,a['id'])
    assert Schedules(store).source(id)['version']==1;store.close()


def test_existing_clarification_does_not_duplicate_schedule_question(tmp_path):
    from agent_builder.nodes.validation import validate_node
    store,id=seed(tmp_path);agent=store.agent(id);d=agent['config'];d['schedule']['timezone']=None;d['questions']=['어느 시간대를 기준으로 실행할까요?']
    result=validate_node({'draft':d,'resources':[store.asset(f) for f in d['document_ids']],'messages':[{'role':'user','content':d['requirements'][0]['quote']}]})
    assert result['status']=='needs_clarification';assert result['draft']['questions']==['어느 시간대를 기준으로 실행할까요?'];store.close()


def test_table_instructions_come_from_actual_execution_plan(tmp_path):
    from agent_builder.presentation import canonical_table_draft
    store,id=seed(tmp_path);d=store.agent(id)['config'];d['instructions']='빈 값 keep';d['purpose']='옛 설명'
    result=canonical_table_draft(d,[{'role':'user','content':'빈 값은 제외해주세요.'}],[])
    assert '빈 값: 제외' in result['instructions'];assert '빈 값 keep' not in result['instructions'];assert result['requirements'][0]['quote']=='빈 값은 제외해주세요.';store.close()


def test_group_source_schema_is_checked_before_replacement(tmp_path):
    from agent_builder.workflow import workflow_errors
    from agent_builder.test_workflow import node,bind,edge
    workflow={'entry':'read','nodes':[node('read','read_table'),node('group','aggregate_table',[bind('table','read','table')],fields=['status'])],'edges':[edge('read','group')]}
    assert not workflow_errors(workflow,['read_table','aggregate_table'],[{'id':'a','kind':'table','columns':['status']}])
    assert any('집계' in s for s in workflow_errors(workflow,['read_table','aggregate_table'],[{'id':'a','kind':'table','columns':['other']}]))


def test_exclusion_policy_requires_a_processing_node():
    from agent_builder.nodes.validation import validate_node
    from agent_builder.test_builder import initial,draft
    d=draft();d['tool_ids']=['read_table','write_result'];d['execution_plan']['missing_policy']='exclude'
    result=validate_node({**initial(),'draft':d})
    assert result['status']=='needs_clarification';assert any('조건 처리 도구' in s for s in result['issues'])


def test_dst_fold_never_returns_an_instant_in_the_past():
    spec={**SPEC,'timezone':'America/New_York','hour':1,'minute':30}
    # 01:15 in the second fold; the first 01:30 occurrence already passed.
    current=datetime(2026,11,1,6,15,tzinfo=timezone.utc)
    result=next_occurrence(spec,current)
    assert result=='2026-11-02T06:30:00+00:00'
    assert datetime.fromisoformat(result)>current


def test_weekly_monday_kst_example_and_offline_coalescing(tmp_path):
    weekly={**SPEC,'frequency':'weekly','weekday':0}
    assert next_occurrence(weekly,datetime(2026,10,27,0,tzinfo=timezone.utc))=='2026-11-02T00:00:00+00:00'
    store,id=seed(tmp_path);s=Schedules(store);s.activate(id,True)
    due=datetime.now(timezone.utc)-timedelta(days=20)
    store.db.execute('UPDATE builder_schedules SET next_at=? WHERE agent_id=?',(due.isoformat(),id));store.db.commit()
    jobs=s.claim_due();assert len(jobs)==1
    assert jobs[0]['due_at']==due.isoformat()
    assert datetime.fromisoformat(s.get(id)['next_run_at'])>datetime.now(timezone.utc)
    assert not s.claim_due();store.close()
