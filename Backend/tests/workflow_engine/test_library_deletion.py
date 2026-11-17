import pytest
from infrastructure.persistence.library_deletion import remove
from infrastructure.persistence.workflow_folders import change, organization
from tests.workflow_engine.test_independent_schedules import seed, request
from tests.workflow_engine.test_node_library import spec
from workflow_engine.library.node_library import save, get, list_nodes, default_nodes
from workflow_engine.scheduling.schedules import Schedules
from workflow_engine.runner import run_workflow
from workflow_engine.lifecycle.run_lifecycle import RunLifecycle


def test_delete_preserves_records_cancels_schedule_and_cleans_folders(tmp_path):
    store,id,state=seed(tmp_path,scheduled=True)
    result=run_workflow(store,id,'','')
    schedules=Schedules(store);request(tmp_path,'schedule_test',id=id)
    folders=change(store,dict(operation='create',revision=0,name='업무'))
    change(store,dict(operation='move',revision=1,folder_id=folders['folders'][0]['id'],agent_ids=[id]))
    original=store.agent_version(id,1)
    assert remove(store,'agent',id,1)['deleted']==id
    assert not store.agents() and not store.agent_summaries()
    assert not schedules.get(id) and not schedules.claim_due()
    assert schedules.jobs(id)[0]['status']=='cancelled'
    assert id not in organization(store)['assignments']
    assert store.agent_version(id,1)==original and store.test(result['id'])['status']=='succeeded'
    assert store.history_agents([id])[0]['config']['name']==original['config']['name']
    with pytest.raises(ValueError,match='삭제'):store.agent(id)
    with pytest.raises(ValueError,match='삭제'):store.rename_agent(id,'변경',2)
    with pytest.raises(ValueError):store.save_agent({**state,'base_agent_id':id,'base_version':1})
    with pytest.raises(ValueError,match='삭제'):store.save_agent(state)
    with pytest.raises(ValueError,match='삭제'):run_workflow(store,id,'','')
    assert remove(store,'agent',id,1)['deleted']==id
    store.close()
    detail=request(tmp_path,'agent',id=id)
    assert detail['agent']['deleted_at'] and detail['tests'][0]['id']==result['id']


@pytest.mark.parametrize('status',['running','needs_input'])
def test_active_run_prevents_removal_without_partial_changes(tmp_path,status):
    store,id,_=seed(tmp_path,scheduled=True)
    store.save_test(dict(id='active',agent_id=id,status=status))
    with pytest.raises(ValueError,match='실행 중'):remove(store,'agent',id,1)
    assert store.agent(id)['version']==1 and Schedules(store).get(id)
    store.close()


def test_stale_version_cannot_delete_updated_workflow(tmp_path):
    store,id,_=seed(tmp_path);store.rename_agent(id,'새 이름',1)
    with pytest.raises(ValueError,match='변경'):remove(store,'agent',id,1)
    assert store.agent(id)['config']['name']=='새 이름';store.close()


def test_removal_between_run_preparation_and_start_prevents_execution(tmp_path):
    store,id,_=seed(tmp_path);run=run_workflow(store,id,'','');remove(store,'agent',id,1)
    with pytest.raises(ValueError,match='삭제'):RunLifecycle(store).start(run,'new-token')
    assert store.test(run['id'])['status']=='succeeded';store.close()


def test_template_delete_keeps_pinned_versions_and_rejects_stale_save(tmp_path):
    store,_,_=seed(tmp_path);saved=save(store,spec());remove(store,'node',saved['id'],1)
    assert not list_nodes(store) and get(store,saved['ref'])==saved
    with pytest.raises(ValueError,match='삭제'):save(store,spec(),saved['id'],2)
    with pytest.raises(ValueError,match='기본'):remove(store,'node',default_nodes()[0]['id'],1)
    store.close()


def test_database_failure_rolls_back_schedule_and_tombstone(tmp_path):
    store,id,_=seed(tmp_path,scheduled=True)
    class FailingBusiness:
        def archive_workflow(self,id):raise RuntimeError('database failure')
    store.business=FailingBusiness()
    with pytest.raises(RuntimeError,match='database failure'):remove(store,'agent',id,1)
    assert store.agent(id)['version']==1 and Schedules(store).get(id)
    store.close()


def test_delete_is_scoped_to_the_selected_database(tmp_path):
    from infrastructure.persistence.storage import Storage
    store,id,_=seed(tmp_path/'one')
    other=Storage(tmp_path/'two')
    with pytest.raises(ValueError,match='찾을 수'):remove(other,'agent',id,1)
    assert store.agent(id)['version']==1
    other.close();store.close()


def test_delete_and_run_start_cannot_both_commit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from copy import deepcopy
    from infrastructure.persistence.storage import Storage
    store,id,_=seed(tmp_path)
    run=run_workflow(store,id,'','');store.close()
    barrier=Barrier(2)
    def attempt(kind):
        local=Storage(tmp_path)
        try:
            barrier.wait(timeout=10)
            if kind=='delete':remove(local,'agent',id,1)
            else:RunLifecycle(local).start(deepcopy(run),'race-token')
            return kind
        except ValueError:return 'blocked'
        finally:local.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,['delete','run']))
    assert results.count('blocked')==1,results


def test_postgres_business_archive_checks_member_and_affected_row_count():
    from infrastructure.persistence.business_records import BusinessRecords
    class Connection:
        def __init__(self):self.calls=[];self.rowcount=1
        def execute(self,sql,params):self.calls.append((sql,params));return self
    connection=Connection();business=BusinessRecords(connection,'member')
    business.archive_workflow('workflow')
    sql,params=connection.calls[-1]
    assert params==('workflow','member') and 'created_by=%s' in sql and "status='archived'" in sql
    connection.rowcount=0
    with pytest.raises(ValueError,match='삭제 상태'):business.archive_workflow('workflow')


@pytest.mark.parametrize('kind',['agent','node'])
def test_removal_queries_keep_postgres_workspace_scope_and_parameter_binding(tmp_path,kind):
    import re
    from infrastructure.persistence.db import postgres_sql, bind
    from infrastructure.persistence.storage import Storage
    store,id,_=seed(tmp_path)
    if kind=='node':id=save(store,spec())['id']
    original=store.db;queries=[]
    class RecordingDatabase:
        def __getattr__(self,key):return getattr(original,key)
        def execute(self,sql,params=()):queries.append((sql,params));return original.execute(sql,params)
    store.db=RecordingDatabase()
    remove(store,kind,id,1)
    pg=object.__new__(Storage);pg.dialect='postgres'
    for sql,params in queries:
        sql=re.sub(r"json_extract\(([^,]+),'\$\.([^']+)'\)",lambda m:pg.json_text(m[1],m[2]),sql)
        query,slots=postgres_sql(sql)
        assert '{ws' not in query and 'json_extract' not in query
        assert len(bind(slots,params,'member-workspace'))==len(slots)
        assert 'workspace_id' in query,(kind,query)
        if query.lstrip().startswith('INSERT'):
            assert 'workspace_id,' in query
            if 'ON CONFLICT' in query:assert 'ON CONFLICT(workspace_id,' in query
    store.close()
