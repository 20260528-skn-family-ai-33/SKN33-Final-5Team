"""List/bootstrap reads must stay bounded independently of stored workflow snapshots."""
import json
from types import SimpleNamespace
from infrastructure.persistence.storage import Storage
from service.handlers.library import agents,studio,schedules
from workflow_engine.scheduling.schedules import Schedules


def seed(store):
    config={'name':'업무 A','purpose':'목록 검증','tool_ids':['read_table','write_result'],'document_ids':[],
            'instructions':'private instructions '*20000,
            'workflow':{'nodes':[{'id':str(i),'settings':{'large':'x'*10000}} for i in range(4)]}}
    agent={'id':'A','version':1,'credential_id':'credential','model':'model','updated_at':'2026-11-16T00:00:00Z','config':config,'workflow_dependencies':{'large':'x'*10000}}
    store.db.execute('INSERT INTO agent_versions({wsc}agent_id,version,json) VALUES({wsv}?,?,?)',('A',1,json.dumps(agent)))
    store.db.commit()
    return agent


def test_summary_keeps_card_fields_and_accurate_node_count_without_full_configs(tmp_path):
    store=Storage(tmp_path)
    try:
        original=seed(store)
        full=agents(SimpleNamespace(storage=store,data={}))
        lean=agents(SimpleNamespace(storage=store,data={'summary':True}))
        assert full['agents']==[original]
        summary=lean['agents'][0]
        assert summary['id']=='A' and summary['version']==1
        assert summary['config']=={'name':'업무 A','purpose':'목록 검증','tool_ids':['read_table','write_result']}
        assert summary['node_count']==4
        assert 'workflow_dependencies' not in summary
        assert len(json.dumps(lean))<len(json.dumps(full))/100
        assert lean['organization']==full['organization']
    finally:store.close()


def test_studio_uses_summaries_and_returns_nodes_and_catalog_in_one_job(tmp_path,monkeypatch):
    store=Storage(tmp_path)
    try:
        seed(store)
        monkeypatch.setattr(store,'agents',lambda: (_ for _ in ()).throw(AssertionError('full list hydration')))
        result=studio(SimpleNamespace(storage=store,data={}))
        assert result['agents'][0]['node_count']==4
        assert isinstance(result['nodes'],list) and isinstance(result['defaults'],list)
        assert result['tools'] and result['engine']['storage']=='sqlite'
    finally:store.close()


def test_reservations_supply_unscheduled_picker_metadata_in_same_response(tmp_path):
    store=Storage(tmp_path)
    try:
        seed(store);Schedules(store)
        assert 'agents' not in schedules(SimpleNamespace(storage=store,data={}))
        result=schedules(SimpleNamespace(storage=store,data={'include_agents':True}))
        assert result['workflows']==[]
        assert result['agents']==[{'id':'A','version':1,'config':{'name':'업무 A','purpose':'목록 검증'}}]
    finally:store.close()
