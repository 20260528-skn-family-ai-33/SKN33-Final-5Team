"""The schedule screen reads real persisted state without changing reservations."""
import pytest
from types import SimpleNamespace

from contracts.models import AgentDraft
from infrastructure.persistence.storage import Storage
from infrastructure.persistence.ids import uid
from service.handlers.library import schedules as scheduled_workflows
from tests.workflow_engine.test_gmail_scheduling import initial,draft,node,SPEC
from workflow_engine.scheduling.schedules import Schedules


@pytest.fixture(autouse=True)
def local_storage(monkeypatch):
    monkeypatch.setenv('A4B_STORAGE','sqlite')
    monkeypatch.setenv('EASYFLOW_PUBLIC_URL','http://localhost')
    monkeypatch.delenv('A4B_BUSINESS_RECORDS',raising=False)


def listing(directory):
    store=Storage(directory)
    try:return scheduled_workflows(SimpleNamespace(storage=store))
    finally:store.close()


def seed(directory):
    store=Storage(directory)
    step=node('calculate','scalar_calculate',operation='evaluate',arguments_json='{"formula":"2+3","variables":{}}')
    config={**draft(),'tool_ids':['scalar_calculate'],'schedule':SPEC,'report_destination':'reports',
            'workflow':{'entry':'calculate','nodes':[step],'edges':[]}}
    id=store.save_agent({**initial(),'confirmed':True,'draft':AgentDraft.model_validate(config).model_dump()})
    return store,id


def test_empty_schedule_listing(tmp_path):
    assert listing(tmp_path)=={'workflows':[]}


def test_listing_uses_persisted_reservations_independent_of_workflow_config(tmp_path):
    store,active_id=seed(tmp_path)
    try:
        config=store.agent(active_id)['config']
        save=lambda name,schedule:store.save_agent({**initial(),'confirmed':True,
            'draft':{**config,'name':name,'schedule':schedule}})
        paused_id=save('중지된 예약',config['schedule'])
        missing_id=save('예약 상태 확인 필요',config['schedule'])
        ordinary_id=save('수동 실행',None)
        attached_id=save('나중에 예약 추가',None)
        removed_id=save('예약 제거',config['schedule'])
        schedules=Schedules(store)
        schedules.configure(attached_id,spec=config['schedule'])
        schedules.remove(removed_id)
        schedules.activate(active_id,True)
        store.db.execute('DELETE FROM builder_schedules WHERE {ws} AND agent_id=?',(missing_id,))
        store.db.commit()
        expected=[{'agent':agent,'schedule':schedules.get(agent['id'])}
                  for agent in store.agents() if agent['id'] in {active_id,paused_id,attached_id}]
        before={agent['id']:schedules.get(agent['id']) for agent in store.agents()}
        result=listing(tmp_path)
        assert result=={'workflows':expected}
        by_id={item['agent']['id']:item for item in result['workflows']}
        assert by_id[active_id]['schedule']['active'] is True
        assert by_id[paused_id]['schedule']['active'] is False
        assert missing_id not in by_id
        assert by_id[attached_id]['agent']['config']['schedule'] is None
        assert ordinary_id not in by_id and removed_id not in by_id
        assert before=={agent['id']:schedules.get(agent['id']) for agent in store.agents()}
        assert all(schedules.jobs(agent_id)==[] for agent_id in before)
    finally:
        store.close()


def test_schedule_listing_uses_only_its_workspace(tmp_path):
    alice=tmp_path/'users'/uid()
    bob=tmp_path/'users'/uid()
    store,alice_id=seed(alice)
    store.close()
    store,bob_id=seed(bob)
    store.close()
    assert [item['agent']['id'] for item in listing(alice)['workflows']]==[alice_id]
    assert [item['agent']['id'] for item in listing(bob)['workflows']]==[bob_id]
