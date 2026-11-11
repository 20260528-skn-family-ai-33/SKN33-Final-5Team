"""Reservations can be added and removed without changing saved workflow versions."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from contracts.models import AgentDraft
from infrastructure.persistence.storage import Storage
from service.dispatch import handle
from tests.workflow_engine.test_gmail_scheduling import draft, initial, node, SPEC
from workflow_engine.scheduling.schedules import Schedules


@pytest.fixture(autouse=True)
def local_storage(monkeypatch):
    monkeypatch.setenv('A4B_STORAGE','sqlite')
    monkeypatch.setenv('EASYFLOW_PUBLIC_URL','http://localhost')
    monkeypatch.delenv('A4B_BUSINESS_RECORDS',raising=False)


def seed(directory,scheduled=False):
    store=Storage(directory)
    config={**draft(),'tool_ids':['scalar_calculate'],
            'workflow':{'entry':'calculate','nodes':[node('calculate','scalar_calculate',operation='evaluate',arguments_json='{"formula":"2+3","variables":{}}')],'edges':[]}}
    if scheduled:config.update(schedule=SPEC,report_destination='reports')
    state={**initial(),'confirmed':True,'draft':AgentDraft.model_validate(config).model_dump()}
    return store,store.save_agent(state),state


def request(directory,action,**data):
    return handle({'directory':str(directory),'action':action,**data})


def test_add_run_remove_and_readd_reservation_preserves_the_workflow(tmp_path):
    store,id,_=seed(tmp_path)
    before=store.agent(id)
    store.close()
    assert request(tmp_path,'schedules')=={'workflows':[]}
    schedule=request(tmp_path,'schedule_configure',id=id,spec=SPEC)['schedule']
    assert not schedule['active'] and schedule['agent_version']==1
    public=request(tmp_path,'agent',id=id)
    assert public['agent']==before
    assert public['request_required'] and not public['schedule_request_required']
    now=request(tmp_path,'test',id=id,use_schedule=True)
    assert now['status']=='succeeded' and now['config_snapshot']['schedule']['hour']==9
    assert request(tmp_path,'schedules')['workflows'][0]['agent']['id']==id
    assert request(tmp_path,'schedule',id=id,active=True)['schedule']['active']
    request(tmp_path,'schedule',id=id,active=False)
    job=request(tmp_path,'schedule_test',id=id)['job']
    store=Storage(tmp_path)
    manager=Schedules(store)
    [claimed]=manager.claim_due(datetime.fromisoformat(job['due_at'])+timedelta(seconds=1))
    assert claimed['agent_snapshot']['config']['schedule']['timezone']=='Asia/Seoul'
    assert manager.get(id)['active'] is False
    store.close()
    result=request(tmp_path,'scheduled_run',id=claimed['id'],lease_token=claimed['lease_token'])
    assert result['status']=='succeeded',result.get('error')
    assert request(tmp_path,'schedule_remove',id=id)=={'schedule':None}
    assert request(tmp_path,'schedule_remove',id=id)=={'schedule':None}
    assert request(tmp_path,'schedules')=={'workflows':[]}
    assert request(tmp_path,'agent',id=id)['agent']==before
    manual=request(tmp_path,'test',id=id,content='지금 계산하세요.')
    assert manual['status']=='succeeded' and manual['trigger']=='manual'
    updated={**SPEC,'hour':17,'timezone':'UTC'}
    assert request(tmp_path,'schedule_configure',id=id,spec=updated)['schedule']['spec']['hour']==17
    assert request(tmp_path,'agent',id=id)['agent']==before


def test_new_file_manual_schedule_run_pins_the_independent_reservation(tmp_path):
    from tests.workflow_engine.test_library_schedule_sources import workflow,upload,spec
    from workflow_engine.scheduling.library_sources import LibrarySources,library_export
    store=Storage(tmp_path)
    asset=upload(store,'new.csv',amount=5)
    id=workflow(store)
    manager=LibrarySources(store)
    selected=manager.save(id,spec('new'),0)
    reservation={**SPEC,'hour':17,'timezone':'UTC'}
    Schedules(store).configure(id,spec=reservation)
    refs=library_export(store,selected,'table')['candidates']
    expanded=manager.resolve('test',id,refs,expected_selection=selected,use_schedule=True)
    assert expanded['status']=='expanded'
    [job]=[job for job in Schedules(store).jobs(id) if job.get('parent_job_id')]
    assert job['agent_snapshot']['config']['schedule']['timezone']=='Asia/Seoul'
    assert job['agent_snapshot']['config']['schedule']['hour']==17
    assert store.agent(id)['config']['schedule']['timezone']=='Asia/Seoul'
    assert job['library_source']['refs'][0]['id']==asset['id']
    store.close()


def test_removing_legacy_schedule_cancels_only_queued_automatic_jobs_and_does_not_resurrect(tmp_path):
    store,id,state=seed(tmp_path,scheduled=True)
    manager=Schedules(store)
    queued=manager.enqueue_test(id,60)
    running=manager.enqueue_test(id,-1)
    [claimed]=manager.claim_due()
    assert claimed['id']==running['id']
    manual=manager.enqueue_test(id,60)
    manual['kind']='manual'
    store.db.execute('UPDATE builder_schedule_jobs SET json=? WHERE {ws} AND id=?',(json.dumps(manual),manual['id']))
    store.db.commit()
    before=store.agent(id)
    manager.remove(id)
    statuses={job['id']:job['status'] for job in manager.jobs(id)}
    assert statuses[queued['id']]=='cancelled'
    assert statuses[running['id']]=='running'
    assert statuses[manual['id']]=='queued'
    assert store.agent(id)==before
    assert store.save_agent(state)==id
    assert manager.get(id) is None
    changed={**state,'session_id':initial()['session_id'],'base_agent_id':id,'base_version':1,
             'draft':{**state['draft'],'name':'수정된 업무'}}
    assert store.save_agent(changed)==id
    assert manager.get(id) is None
    store.close()


def test_missing_partial_save_is_repaired_but_explicit_removal_survives_a_restart(tmp_path):
    store,id,state=seed(tmp_path,scheduled=True)
    # An absent reservation without a removal record is an incomplete legacy save.
    store.db.execute('DELETE FROM builder_schedules WHERE {ws} AND agent_id=?',(id,));store.db.commit()
    assert store.save_agent(state)==id
    assert Schedules(store).get(id) is not None
    manager=Schedules(store)
    manager.remove(id)
    assert manager.was_removed(id) and manager.get(id) is None
    store.close()
    store=Storage(tmp_path)
    try:
        manager=Schedules(store)
        assert manager.was_removed(id) and manager.get(id) is None
        assert store.save_agent(state)==id
        assert manager.get(id) is None
        manager.configure(id,spec=SPEC)
        assert manager.get(id) is not None and not manager.was_removed(id)
    finally:store.close()


def test_workflow_edits_preserve_separate_schedule_and_require_applying_new_version(tmp_path):
    store,id,state=seed(tmp_path,scheduled=True)
    manager=Schedules(store)
    custom={**SPEC,'hour':16,'timezone':'UTC'}
    manager.configure(id,spec=custom)
    manager.activate(id,True)
    store.save_agent({**state,'session_id':initial()['session_id'],'base_agent_id':id,'base_version':1,
                      'draft':{**state['draft'],'instructions':'새 업무 조건'}})
    reservation=manager.get(id)
    assert reservation['spec']=={**custom,'timezone':'Asia/Seoul','monthday':None,'month_end':False}
    assert reservation['agent_version']==1 and not reservation['active']
    with pytest.raises(ValueError,match='버전'):manager.activate(id,True)
    with pytest.raises(ValueError,match='버전'):manager.enqueue_test(id)
    with pytest.raises(ValueError,match='버전'):request(tmp_path,'test',id=id,use_schedule=True)
    manager.configure(id,spec=custom)
    assert manager.get(id)['agent_version']==2
    assert manager.activate(id,True)['active']
    due=datetime.now(timezone.utc)-timedelta(seconds=1)
    store.db.execute('UPDATE builder_schedules SET next_at=? WHERE {ws} AND agent_id=?',(due.isoformat(),id))
    store.db.commit()
    [claimed]=manager.claim_due()
    assert claimed['agent_snapshot']['version']==2
    assert claimed['agent_snapshot']['config']['schedule']['timezone']=='Asia/Seoul'
    assert store.agent(id)['config']['schedule']['timezone']=='Asia/Seoul'
    store.close()


@pytest.mark.parametrize('spec',[{**SPEC,'frequency':'yearly'},{**SPEC,'minute':True},{**SPEC,'hour':25}])
def test_invalid_reservation_does_not_replace_existing_schedule(tmp_path,spec):
    store,id,_=seed(tmp_path,scheduled=True)
    before=Schedules(store).get(id)
    store.close()
    with pytest.raises(ValueError):request(tmp_path,'schedule_configure',id=id,spec=spec)
    assert request(tmp_path,'agent',id=id)['schedule']==before


def test_manual_upload_overrides_library_source_without_editing_the_reservation(tmp_path):
    from tests.workflow_engine.test_library_schedule_sources import workflow,upload,spec
    from workflow_engine.scheduling.library_sources import LibrarySources
    store=Storage(tmp_path)
    saved=upload(store,'saved.csv',amount=1)
    override=upload(store,'this-run.csv',amount=9)
    id=workflow(store)
    selected=LibrarySources(store).save(id,spec(asset_ids=[saved['id']]),0)
    before=Schedules(store).get(id)
    store.close()
    result=request(tmp_path,'test',id=id,content='이번 자료로 실행',file_ids=[override['id']])
    assert result['status']=='succeeded',result.get('error')
    assert result['rows']==[{'amount':9}]
    assert 'library_source' not in result
    store=Storage(tmp_path)
    assert LibrarySources(store).get(id)==selected
    assert Schedules(store).get(id)==before
    store.close()


def test_removal_and_simultaneous_test_reservation_never_leave_a_queued_job(tmp_path):
    store,id,_=seed(tmp_path,scheduled=True)
    store.close()
    def run(action):
        try:return request(tmp_path,action,id=id)
        except ValueError as error:
            assert action=='schedule_test' and '예약 설정' in str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run,['schedule_remove','schedule_test']))
    store=Storage(tmp_path)
    manager=Schedules(store)
    assert manager.get(id) is None
    assert all(job['status']=='cancelled' for job in manager.jobs(id))
    store.close()
