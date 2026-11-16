"""History is a single scoped read and never hydrates saved run checkpoints."""
import json
from types import SimpleNamespace
from infrastructure.persistence.storage import Storage
from service.handlers.library import history


def seed(store,agent_id,count):
    agent={'id':agent_id,'version':1,'config':{'name':'업무 '+agent_id,'purpose':'요약','tool_ids':[]}}
    store.db.execute('INSERT INTO agent_versions({wsc}agent_id,version,json) VALUES({wsv}?,?,?)',(agent_id,1,json.dumps(agent)))
    for index in range(count):
        run={'id':f'{agent_id}-{index}','agent_version':1,'status':'succeeded',
             'created_at':f'2026-11-{index+1:02d}T00:00:00Z','outputs':{'huge':'x'*10000},'answer':'private answer'}
        store.db.execute('INSERT INTO agent_tests({wsc}id,agent_id,json) VALUES({wsv}?,?,?)',(run['id'],agent_id,json.dumps(run)))
    store.db.commit()


def test_history_window_projection_and_filter(tmp_path):
    store=Storage(tmp_path)
    try:
        seed(store,'A',25);seed(store,'B',2)
        # Fail if history regresses to expensive agent detail or run hydration.
        store.agent=lambda *_: (_ for _ in ()).throw(AssertionError('agent detail'))
        store._read_test=lambda *_: (_ for _ in ()).throw(AssertionError('run detail'))
        result=history(SimpleNamespace(storage=store,data={}))
        assert len(result['rows'])==22
        assert result['rows'][0]['run']['id']=='A-24'
        assert all('outputs' not in row['run'] and 'answer' not in row['run'] for row in result['rows'])
        assert all(isinstance(row['run']['agent_version'],int) for row in result['rows'])
        selected=history(SimpleNamespace(storage=store,data={'id':'B'}))
        assert [row['run']['id'] for row in selected['rows']]==['B-1','B-0']
        assert [agent['id'] for agent in selected['agents']]==['B']
        assert history(SimpleNamespace(storage=store,data={'id':'missing'}))=={'agents':[],'rows':[]}
    finally:store.close()


def test_history_empty_and_workspace_separation(tmp_path):
    first,other=Storage(tmp_path/'first'),Storage(tmp_path/'other')
    try:
        seed(first,'A',1)
        assert history(SimpleNamespace(storage=other,data={}))=={'agents':[],'rows':[]}
    finally:first.close();other.close()


def test_history_sorts_equivalent_timezones_and_invalid_dates(tmp_path):
    store=Storage(tmp_path)
    try:
        seed(store,'A',3)
        for run_id,created in [('A-0','2026-11-16T09:00:00+09:00'),('A-1','2026-11-16T01:00:00Z'),('A-2','unknown')]:
            row=json.loads(store.db.execute('SELECT json FROM agent_tests WHERE id=?',(run_id,)).fetchone()[0]);row['created_at']=created
            store.db.execute('UPDATE agent_tests SET json=? WHERE id=?',(json.dumps(row),run_id))
        store.db.commit()
        assert [row['run']['id'] for row in history(SimpleNamespace(storage=store,data={}))['rows']]==['A-1','A-0','A-2']
    finally:store.close()


def test_schedule_listing_reads_once_and_preserves_removed_and_paused_state(tmp_path,monkeypatch):
    from workflow_engine.scheduling.schedules import Schedules
    from service.handlers.library import schedules
    store=Storage(tmp_path)
    try:
        seed(store,'A',0);seed(store,'B',0);seed(store,'C',0)
        scheduler=Schedules(store)
        for id,active,removed in [('A',1,False),('B',0,False),('C',0,True)]:
            value={'agent_id':id,'agent_version':1,'spec':{'timezone':'Asia/Seoul'},'removed':removed}
            store.db.execute('INSERT INTO builder_schedules(agent_id,json,next_at,active) VALUES(?,?,?,?)',(id,json.dumps(value),'2026-11-17T00:00:00Z',active))
        store.db.commit()
        expected={id:scheduler.get(id) for id in ['A','B']}
        monkeypatch.setattr(Schedules,'get',lambda *_: (_ for _ in ()).throw(AssertionError('per-workflow query')))
        result=schedules(SimpleNamespace(storage=store,data={}))
        actual={row['agent']['id']:row['schedule'] for row in result['workflows']}
        assert actual==expected
        assert actual['B']['active'] is False
        assert 'C' not in actual
    finally:store.close()
