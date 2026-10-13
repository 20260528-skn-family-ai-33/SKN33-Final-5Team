"""Regression coverage for the five reproduced review failures."""
import copy,json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from agent_builder.storage import Storage,uid
from agent_builder.scheduling import Schedules
from agent_builder.run_lifecycle import RunLifecycle
from agent_builder.test_reply_review import seeded,output
from agent_builder.test_builder import initial
from agent_builder.test_workflow import node,bind,edge
from agent_builder.models import AgentDraft
from agent_builder.nodes.save import make_save_node
from agent_builder.nodes.validation import validate_node
from agent_builder.worker import handle

def mixed_policy_state(st,id):
    draft=copy.deepcopy(st.agent(id)['config']);policy=draft['document_ids'][1]
    draft['workflow']['nodes'].insert(0,node('policy','read_document',file_ids=[policy]));draft['workflow']['nodes'][-1]['bindings'].append(bind('documents','policy','documents'))
    draft['workflow']['edges'].insert(0,edge('policy','read'));draft['workflow']['entry']='policy';draft['tool_ids'].insert(0,'read_document')
    draft.update(schedule={'frequency':'daily','timezone':'Asia/Seoul','hour':9,'minute':0,'weekday':None},report_destination='reports')
    return {**initial(),'base_agent_id':id,'base_version':1,'draft':AgentDraft.model_validate(draft).model_dump(),'confirmed':True,'resources':[st.asset(i) for i in draft['document_ids']]}

def test_policy_and_table_schedule_saves_and_replaces_source(tmp_path,monkeypatch):
    st,id=seeded(tmp_path,table=True);state=mixed_policy_state(st,id);assert validate_node(state)['status']=='ready'
    make_save_node(st)(state);assert st.agent(id)['version']==2
    s=Schedules(st);source=s.source(id);assert source
    asset={**source['asset'],'id':uid()};st.add_asset(asset,st.rows(source['asset_id']));s.replace_source(id,asset['id'])
    assert s.source(id)['version']==2
    monkeypatch.setattr('agent_builder.reply_review.run_review',lambda *a:output());st.close()
    run=handle({'directory':str(tmp_path),'action':'test','id':id,'content':'検証','api_key':'unused'})
    assert run['status']=='succeeded',run.get('error');assert run['reviews'][0]['sources'][0]['file_name']=='정책.txt'

def test_initialization_failure_rolls_back_all_records(tmp_path,monkeypatch):
    st,id=seeded(tmp_path,table=True);state=mixed_policy_state(st,id)
    def fail(*a,**kw):raise ValueError('injected source initialization failure')
    monkeypatch.setattr(Schedules,'replace_source',fail)
    with pytest.raises(ValueError):make_save_node(st)(state)
    assert st.agent(id)['version']==1;assert Schedules(st).get(id) is None;assert Schedules(st).source(id) is None;st.close()

def test_failed_new_save_leaves_no_agent_or_schedule(tmp_path,monkeypatch):
    st,id=seeded(tmp_path,table=True);state=mixed_policy_state(st,id);state.update(base_agent_id=None,base_version=0)
    monkeypatch.setattr(Schedules,'replace_source',lambda *a,**kw:(_ for _ in ()).throw(ValueError('fail')))
    with pytest.raises(ValueError):st.save_agent(state)
    assert len(st.agents())==1;assert st.db.execute('SELECT count(*) FROM builder_schedules').fetchone()[0]==0;st.close()

def test_replay_repairs_legacy_partial_save_without_new_version(tmp_path):
    st,id=seeded(tmp_path,table=True);state=mixed_policy_state(st,id);st.save_agent(state)
    st.db.execute('DELETE FROM builder_data_sources WHERE agent_id=?',(id,));st.db.commit()
    assert st.save_agent(state)==id;assert st.agent(id)['version']==2;assert Schedules(st).source(id);st.close()

def test_healthy_idempotent_save_does_not_pause_schedule(tmp_path):
    st,id=seeded(tmp_path,table=True);state=mixed_policy_state(st,id);st.save_agent(state);s=Schedules(st);s.activate(id,True)
    assert st.save_agent(state)==id;assert s.get(id)['active'];st.close()

def run_fixture(st,id):return {'id':uid(),'agent_id':id,'status':'running','created_at':datetime.now(timezone.utc).isoformat(),'steps':[],'error':None}

def test_live_run_is_not_recovered_and_second_owner_rejected(tmp_path):
    st,id=seeded(tmp_path,table=True);l=RunLifecycle(st);run=run_fixture(st,id);l.start(run,'owner')
    assert l.recover()['recovered']==[]
    with pytest.raises(ValueError,match='다른 작업자'):l.start(copy.deepcopy(run),'other')
    assert l.heartbeat(run['id'],'owner')['status']=='renewed';st.close()

def test_recover_expired_run_is_failed_and_retryable(tmp_path,monkeypatch):
    st,id=seeded(tmp_path,table=True);l=RunLifecycle(st);run=run_fixture(st,id);l.start(run,'owner')
    st.db.execute('UPDATE builder_run_leases SET expires_at=? WHERE run_id=?',('2000-01-01T00:00:00+00:00',run['id']));st.db.commit()
    assert l.recover()['recovered']==[run['id']];assert st.test(run['id'])['status']=='failed'
    with pytest.raises(ValueError):l.assert_owner(run['id'],'owner')
    assert l.recover()['recovered']==[];st.close()

def test_recover_dead_owner_without_waiting_for_lease_expiry(tmp_path,monkeypatch):
    st,id=seeded(tmp_path,table=True);l=RunLifecycle(st);run=run_fixture(st,id);l.start(run,'owner');monkeypatch.setattr('agent_builder.run_lifecycle.alive',lambda pid:False)
    assert l.recover()['recovered']==[run['id']];assert st.test(run['id'])['error_type']=='InterruptedExecution';st.close()

def test_old_parent_cannot_interrupt_new_owner(tmp_path):
    st,id=seeded(tmp_path,table=True);l=RunLifecycle(st);run=run_fixture(st,id);l.start(run,'old');l.interrupt(run['id'],'old');l.start(run,'new')
    assert l.interrupt(run['id'],'old')['status']=='unchanged';assert st.test(run['id'])['status']=='running'
    with pytest.raises(ValueError):l.heartbeat(run['id'],'old')
    run['status']='succeeded'
    with pytest.raises(ValueError):l.finish(run,'old')
    l.finish(run,'new');assert st.test(run['id'])['status']=='succeeded';st.close()

def test_schedule_heartbeat_blocks_reclaim_until_worker_stops(tmp_path):
    st,id=seeded(tmp_path,table=True,scheduled=True);s=Schedules(st);s.enqueue_test(id,0);stamp=datetime.now(timezone.utc);a=s.claim_due(stamp)[0]
    s.heartbeat(a['id'],a['lease_token'],stamp+timedelta(seconds=290));assert not s.claim_due(stamp+timedelta(seconds=301))
    b=s.claim_due(stamp+timedelta(seconds=591))[0];assert a['id']==b['id'];assert a['lease_token']!=b['lease_token']
    with pytest.raises(ValueError):s.heartbeat(a['id'],a['lease_token']);st.close()

def test_claim_only_one_immediate_job(tmp_path):
    st,id=seeded(tmp_path,table=True,scheduled=True);s=Schedules(st)
    for i in range(3):s.enqueue_test(id,0)
    assert len(s.claim_due())==1;assert sum(j['status']=='queued' for j in s.jobs(id))==2;st.close()

def test_schedule_retry_syncs_job_and_preserves_failure(tmp_path,monkeypatch):
    st,id=seeded(tmp_path,table=True,scheduled=True);s=Schedules(st);s.enqueue_test(id,0);job=s.claim_due()[0];st.close()
    def fail(*a):raise ValueError('temporary review failure')
    monkeypatch.setattr('agent_builder.reply_review.run_review',fail)
    first=handle({'directory':str(tmp_path),'action':'scheduled_run','id':job['id'],'lease_token':job['lease_token'],'api_key':'unused'});assert first['status']=='failed'
    monkeypatch.setattr('agent_builder.reply_review.run_review',lambda *a:output())
    second=handle({'directory':str(tmp_path),'action':'retry_run','id':first['id'],'api_key':'unused'});assert second['status']=='succeeded'
    assert [a['status'] for a in second['attempts']]==['failed','succeeded'];assert second['attempts'][0]['error']
    st=Storage(tmp_path);j=Schedules(st).jobs(id)[0];assert j['status']=='succeeded';assert j['attempt_history'][0]['status']=='failed';assert j['retry_count']==1;st.close()

def test_killed_process_recovers_then_retries_same_checkpoint(tmp_path):
    import subprocess
    child_code='''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from agent_builder.test_scheduling import seed
from agent_builder.workflow_runtime import run_workflow
st,id=seed(Path(sys.argv[1]))
def slow(*args):
 print('node_started',flush=True);time.sleep(30)
run_workflow(st,id,'execute','',executor=slow)
'''
    child=subprocess.Popen([sys.executable,'-c',child_code,str(tmp_path),str(Path(__file__).resolve().parents[1])],stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='node_started'
        child.kill();child.wait(timeout=10)
        recovered=handle({'directory':str(tmp_path),'action':'recover_runs'});assert len(recovered['recovered'])==1
        id=recovered['recovered'][0];st=Storage(tmp_path);assert st.test(id)['status']=='failed';st.close()
        result=handle({'directory':str(tmp_path),'action':'retry_run','id':id,'api_key':''})
        assert result['status']=='succeeded';assert result['id']==id;assert len(result['attempts'])==2;assert len(result['rows'])==2
    finally:
        if child.poll() is None:child.kill();child.wait()

def test_heartbeat_after_normal_completion_does_not_report_false_failure(tmp_path):
    st,id=seeded(tmp_path,table=True);l=RunLifecycle(st);run=run_fixture(st,id);l.start(run,'owner');run['status']='succeeded';l.finish(run,'owner')
    assert l.heartbeat(run['id'],'owner')['status']=='finished';st.close()

def test_old_owner_cannot_overwrite_published_report(tmp_path):
    st,id=seeded(tmp_path,table=True);l=RunLifecycle(st);run=run_fixture(st,id);l.start(run,'old');l.interrupt(run['id'],'old');l.start(run,'new')
    target=tmp_path/'reports'/'result.csv';l.publish(run['id'],'new',target,'new result')
    with pytest.raises(ValueError):l.publish(run['id'],'old',target,'stale result')
    assert target.read_text()=='new result';st.close()
