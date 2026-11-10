"""Business-data storage on both backends: SQLite always, PostgreSQL when A4B_TEST_DATABASE_URL is set.

A4B_TEST_DATABASE_URL must point at a disposable database whose name contains "test"
(docker compose --profile test-db up -d postgres-test). The fixture drops and recreates the
a4b_builder schema and its generation-history table in a4b, then connects as a non-superuser
app role so row-level security applies. Other a4b business tables are preserved.
"""
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from contracts.models import AgentDraft
from infrastructure.persistence import db as database
from infrastructure.persistence.checkpoints import checkpoint_connection, checkpointer
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from workflow_engine.lifecycle.run_lifecycle import RunLifecycle
from workflow_engine.scheduling.schedules import Schedules
from workflow_engine.workflow import legacy_workflow
from tests.design.test_builder import draft, initial

TEST_URL = os.environ.get('A4B_TEST_DATABASE_URL', '').strip()
APP_ROLE, APP_PASSWORD = 'a4b_test_app', 'a4b-test-app'
SPEC = {'frequency': 'daily', 'timezone': 'Asia/Seoul', 'hour': 9, 'minute': 0, 'weekday': None}
ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(not os.getenv('A4B_TEST_BUSINESS_SCHEMA'), reason='requires the restored a4b domain schema')
def test_member_design_checkpoints_and_failed_run_retry_in_existing_tables(postgres_app_conninfo, tmp_path, monkeypatch):
    import psycopg
    from types import SimpleNamespace
    from infrastructure.persistence.ids import now
    from service.handlers.design import session_action
    from tests.design.test_builder import fake_conversation, PROMPT
    member = uid()
    folder = tmp_path / 'users' / member
    monkeypatch.setenv('A4B_STORAGE','postgres')
    monkeypatch.setenv('A4B_BUSINESS_RECORDS','a4b')
    monkeypatch.setenv('A4B_S3_BUCKET','a4b-fixture')
    database.use_conninfo(postgres_app_conninfo)
    with psycopg.connect(TEST_URL, autocommit=True) as admin:
        admin.execute("INSERT INTO a4b.users (id,email,name,auth_provider,auth_subject) VALUES (%s,%s,'test','fixture',%s)", (member,member+'@example.test',member))
        admin.execute("INSERT INTO a4b.document_types(code,label) VALUES ('other','Other') ON CONFLICT DO NOTHING")
        store = Storage(folder)
        try:
            session = store.new_session('test-model','test-credential')
            sid = session['id']
            def action(kind, **fields):
                return session_action(SimpleNamespace(storage=store,action=kind,data={'id':sid,**fields},
                                                      api_key='',conversation=fake_conversation))
            result = action('message', content=PROMPT, revision=0)
            msgs, = admin.execute('SELECT messages_json FROM a4b.design_sessions WHERE id=%s AND created_by=%s',(sid,member)).fetchone()
            assert msgs == result['messages'] and msgs[0]['content']==PROMPT
            assert admin.execute('SELECT 1 FROM a4b_builder.checkpoints WHERE workspace_id=%s AND thread_id=%s',(member,sid)).fetchone()
            version = store.save_agent({**initial(),'session_id':sid,'draft':draft(),'confirmed':True})
            assert version == sid
            assert admin.execute('SELECT version_no FROM a4b.workflow_versions WHERE workflow_id=%s',(sid,)).fetchone()==(1,)
            asset_id=uid()
            store.add_asset({'id':asset_id,'name':'receipt.pdf','kind':'document','format':'pdf','hash':'a'*64,
                             'size_bytes':10,'original_path':'builder-originals/'+('a'*64)+'.pdf','created_at':now()},[])
            assert admin.execute('SELECT size_bytes FROM a4b.document_versions WHERE document_id=%s',(asset_id,)).fetchone()==(10,)
            from service.handlers import uploads
            deletion = SimpleNamespace(storage=store, data={'id': asset_id})
            cleanup = uploads.prepare_delete(deletion)
            assert cleanup['original'] == 'builder-originals/' + 'a' * 64 + '.pdf'
            assert admin.execute('SELECT archived_at IS NOT NULL FROM a4b.documents WHERE id=%s AND created_by=%s',
                                 (asset_id, member)).fetchone() == (True,)
            assert uploads.prepare_delete(deletion) == cleanup
            uploads.finish_delete(deletion)
            run={'id':uid(),'agent_id':sid,'agent_version':1,'request':'test','created_at':now(),'steps':[],
                 'config_snapshot':{'workflow':{'nodes':[{'id':'parse'}]}}}
            lifecycle = RunLifecycle(store)
            token=uid(); lifecycle.start(run,token)
            run.update(status='failed',error='parser',steps=[{'node':'parse','status':'failed','detail':'parser'}])
            lifecycle.finish(run,token)
            token=uid(); lifecycle.start(run,token)
            run.update(status='succeeded',answer='done',steps=[{'node':'parse','status':'completed'}])
            lifecycle.finish(run,token)
            assert set(admin.execute("SELECT status FROM a4b.workflow_runs WHERE requested_by=%s AND state_json->>'builder_run_id'=%s",(member,run['id'])).fetchall())=={('failed',),('succeeded',)}
            assert admin.execute("SELECT error_json->>'detail' FROM a4b.node_runs WHERE run_id=%s",(run['id'],)).fetchone()==('parser',)
            assert admin.execute("SELECT content_json->>'answer' FROM a4b.outputs WHERE created_by=%s",(member,)).fetchone()==('done',)
            with pytest.raises(ValueError):
                store.update_session(session, 0, {'messages':[]})
            assert admin.execute('SELECT messages_json FROM a4b.design_sessions WHERE id=%s',(sid,)).fetchone()==(msgs,)
        finally:
            store.close(); database.use_conninfo(None)


@pytest.fixture(scope='session')
def postgres_app_conninfo():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from infrastructure.persistence import migrations
    admin = conninfo_to_dict(TEST_URL)
    if 'test' not in admin.get('dbname', ''):
        pytest.fail('A4B_TEST_DATABASE_URL의 DB 이름에 test가 들어간 일회용 DB만 사용할 수 있습니다.')
    with psycopg.connect(TEST_URL, autocommit=True) as connection:
        connection.execute('DROP SCHEMA IF EXISTS a4b_builder CASCADE')
        connection.execute('DROP TABLE IF EXISTS a4b.workflow_generation_requests')
        connection.execute("DO $$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='a4b_app') THEN CREATE ROLE a4b_app NOLOGIN; END IF; END$$")
        connection.execute(f"DO $$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{APP_ROLE}') THEN CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'; END IF; END$$")
        connection.execute(f'GRANT a4b_app TO {APP_ROLE}')
    migrations.apply(TEST_URL, log=lambda _: None)
    return make_conninfo(**{**admin, 'user': APP_ROLE, 'password': APP_PASSWORD, 'options': '-c search_path=a4b_builder'})


@pytest.fixture(params=['sqlite', 'postgres'])
def backend(request, monkeypatch):
    if request.param == 'postgres':
        if not TEST_URL:
            pytest.skip('A4B_TEST_DATABASE_URL이 없으면 PostgreSQL 저장소 테스트를 건너뜁니다.')
        conninfo = request.getfixturevalue('postgres_app_conninfo')
        monkeypatch.setenv('A4B_STORAGE', 'postgres')
        monkeypatch.setenv('A4B_DATABASE_POOL_MAX', '6')
        database.use_conninfo(conninfo)
        yield 'postgres'
        database.close_pools()
        database.use_conninfo(None)
    else:
        monkeypatch.delenv('A4B_STORAGE', raising=False)
        yield 'sqlite'


def workspace(tmp_path):
    """A member workspace folder, as app-server.ts creates it (data/users/<member ID>)."""
    folder = tmp_path / 'users' / str(uuid.uuid4())
    folder.mkdir(parents=True)
    return folder


def test_generation_history_persists_failures_waiting_and_workspace_isolation(backend,tmp_path):
    from infrastructure.persistence.generation_history import GenerationHistory, history
    alice,bob=workspace(tmp_path),workspace(tmp_path)
    store=Storage(alice)
    other=Storage(bob)
    try:
        session=store.new_session('test-model','test-credential')
        audit=GenerationHistory.start(store,dict(id=session['id'],action='message',revision=0,content='create workflow'))
        audit.enter('conversation');audit.fail(TimeoutError('provider secret'))
        waiting=GenerationHistory.start(store,dict(id=session['id'],action='message',revision=0,content='retry workflow'))
        waiting.enter('capability_check');waiting.finish(dict(status='needs_clarification',pending_questions=[dict(source='capability',text='Which input?')]))
        rows=history(store,session['id'])
        assert [r['status'] for r in rows]==['waiting_input','failed']
        assert rows[1]['stage']=='design' and rows[1]['reason_code']=='model_timeout'
        assert rows[0]['error_type'] is None and rows[0]['stage']=='request_analysis'
        assert not other.db.execute(f'SELECT id FROM {audit.table} WHERE {{ws}} AND id=?',(audit.id,)).fetchone()
        if backend=='postgres':
            assert audit.table=='a4b.workflow_generation_requests'
            assert store.db.execute("SELECT to_regclass('a4b_builder.builder_generation_requests')").fetchone()==(None,)
            raw=store.db.execute(f'SELECT json FROM {audit.table} WHERE {{ws}} AND id=?',(audit.id,)).fetchone()[0]
            assert load_migrator().copy_rows(store.db.connection,store.workspace_id,'builder_generation_requests',
                                            ['id','session_id','json'],[(audit.id,session['id'],raw)])==0
        with pytest.raises(ValueError):history(other,session['id'])
    finally:
        store.close();other.close()
    reopened=Storage(alice)
    try:assert history(reopened,session['id'])==rows
    finally:reopened.close()


def seed(folder):
    store = Storage(folder)
    asset = {'id': uid(), 'name': 'stock.csv', 'kind': 'table', 'format': 'csv', 'columns': ['code', 'stock', 'reorder_level'],
             'profile': {}, 'row_count': 3, 'preview': [], 'text': ''}
    # Column order must survive storage: it is the table's column order downstream.
    store.add_asset(asset, [{'code': 'A', 'stock': '20', 'reorder_level': '30'}, {'code': 'B', 'stock': '3', 'reorder_level': '10'},
                            {'code': 'C', 'stock': '10', 'reorder_level': '10'}])
    d = draft()
    d['document_ids'] = [asset['id']]
    d['execution_plan'].update(filters=[{'field': 'stock', 'operator': 'lt', 'value': None, 'compare_field': 'reorder_level', 'value_type': 'number'}],
                               missing_policy='exclude', duplicate_policy='keep')
    d.update(schedule=SPEC, report_destination='reports')
    d['workflow'] = legacy_workflow(d)
    agent_id = store.save_agent({**initial(), 'draft': AgentDraft.model_validate(d).model_dump(), 'confirmed': True})
    return store, asset, agent_id


def test_dialect_follows_setting(backend, tmp_path):
    store = Storage(workspace(tmp_path))
    try:
        assert store.dialect == backend
    finally:
        store.close()


def test_upload_ownership_survives_failed_cleanup_and_retry(backend, tmp_path):
    from service.dispatch import handle

    alice, bob = workspace(tmp_path), workspace(tmp_path)
    asset_id, other_id = uid(), uid()
    # Identical legacy keys in different member folders still belong to different users.
    original = 'builder-originals/' + 'a' * 64 + '.csv'
    for folder, id, name in [(alice, asset_id, 'alice.csv'), (bob, other_id, 'bob.csv')]:
        store = Storage(folder)
        try:
            store.add_asset({'id': id, 'name': name, 'original_path': original}, [])
        finally:
            store.close()

    def call(folder, action, **data):
        return handle({'directory': str(folder), 'action': action, **data})

    assert [a['id'] for a in call(alice, 'assets')['assets']] == [asset_id]
    assert [a['id'] for a in call(bob, 'assets')['assets']] == [other_id]
    with pytest.raises(ValueError, match='찾을 수 없습니다'):
        call(bob, 'asset_preview', id=asset_id)
    assert call(bob, 'prepare_asset_delete', id=asset_id) == {}
    assert call(alice, 'asset', id=asset_id)['asset']['name'] == 'alice.csv'

    cleanup = call(alice, 'prepare_asset_delete', id=asset_id)
    assert cleanup['original'] == original
    # Failed S3 cleanup leaves the marker committed. A new worker must resume that exact cleanup.
    call(bob, 'finish_asset_delete', id=asset_id)
    [pending] = call(alice, 'assets')['assets']
    assert pending['id'] == asset_id and pending['pending_delete']
    assert call(alice, 'prepare_asset_delete', id=asset_id) == cleanup
    call(alice, 'finish_asset_delete', id=asset_id)
    assert call(alice, 'assets')['assets'] == []
    assert call(alice, 'prepare_asset_delete', id=asset_id) == {}
    assert call(bob, 'asset', id=other_id)['asset']['name'] == 'bob.csv'


def test_reuse_and_named_upload_usages(backend, tmp_path):
    from service.dispatch import handle

    alice, bob = workspace(tmp_path), workspace(tmp_path)
    store, asset, workflow_id = seed(alice)
    try:
        workflow_name = store.agent(workflow_id)['config']['name']
        store.save_test({'id': uid(), 'agent_id': workflow_id, 'file_ids': [asset['id']]})
    finally:
        store.close()

    def call(folder, action, **fields):
        return handle({'action': action, 'directory': str(folder), **fields})

    for member in (alice, bob):
        session = call(member, 'new_session', model='test', credential_id=uid())
        attachment = dict(id=session['id'], revision=0, asset_id=asset['id'])
        if member == bob:
            assert call(bob, 'assets')['assets'] == []
            with pytest.raises(ValueError, match='찾을 수 없습니다'):
                call(bob, 'attach_asset', **attachment)
            continue
        linked = call(alice, 'attach_asset', **attachment)
        assert linked['file_ids'] == [asset['id']] and linked['resources'][0]['id'] == asset['id']
        with pytest.raises(ValueError, match='대화가 변경'):
            call(alice, 'attach_asset', **attachment)
        with pytest.raises(ValueError, match='이미'):
            call(alice, 'attach_asset', **{**attachment, 'revision': linked['revision']})
        draft_id = session['id']

    [row] = call(alice, 'assets')['assets']
    assert {(u['kind'], u['name']) for u in row['usages'] if u['kind'] != 'draft'} == {
        ('workflow', workflow_name), ('schedule', workflow_name), ('run', workflow_name)}
    assert [u['id'] for u in row['usages'] if u['kind'] == 'draft'] == [draft_id]
    with pytest.raises(ValueError, match=workflow_name):
        call(alice, 'prepare_asset_delete', id=asset['id'])
    # After the managed source changes, both the saved version and the historic run
    # still list the old sample. Neither needs its original bytes to stay available.
    store = Storage(alice)
    try:
        replacement = {**asset, 'id':uid(), 'name':'next-stock.csv'}
        store.add_asset(replacement, store.rows(asset['id']))
        Schedules(store).replace_source(workflow_id, replacement['id'])
    finally:
        store.close()
    call(alice, 'prepare_asset_delete', id=asset['id'])
    call(alice, 'finish_asset_delete', id=asset['id'])
    assert [r['id'] for r in call(alice,'assets')['assets']] == [replacement['id']]
    historical = call(alice,'agent',id=workflow_id)
    assert historical['resources'] == [] and historical['tests'][0]['file_ids'] == [asset['id']]
    assert historical['source']['asset_id'] == replacement['id']
    assert call(alice,'session',id=draft_id)['resources'] == []
    assert call(alice,'new_session',model='test',credential_id=uid(),agent_id=workflow_id)['file_ids'] == [replacement['id']]
    with pytest.raises(ValueError, match='삭제'):
        call(alice,'asset_preview',id=asset['id'])
    new_run = call(alice,'test',id=workflow_id,content='교체한 자료로 부족 재고를 정리해줘')
    assert new_run['status'] == 'succeeded' and new_run['file_ids'] == [replacement['id']]


def test_session_revision_is_an_optimistic_lock(backend, tmp_path):
    folder = workspace(tmp_path)
    store = Storage(folder)
    try:
        session = store.new_session('gpt-4.1-mini', 'credential')
        updated = store.update_session({**session, 'model': 'changed'}, session['revision'])
        assert updated['revision'] == session['revision'] + 1 and updated['model'] == 'changed'
        with pytest.raises(ValueError, match='다른 요청'):
            store.update_session({**session, 'model': 'stale'}, session['revision'])
        assert store.session(session['id'])['model'] == 'changed'
    finally:
        store.close()


def test_saved_agent_versions_tests_and_order(backend, tmp_path):
    store, asset, agent_id = seed(workspace(tmp_path))
    try:
        assert list(store.rows(asset['id'])[0]) == ['code', 'stock', 'reorder_level']
        assert [a['id'] for a in store.agents()] == [agent_id] and store.agent(agent_id)['version'] == 1
        for number in range(3):
            store.save_test({'id': f'run-{number}', 'agent_id': agent_id, 'status': 'succeeded', 'outputs': {}})
        # Newest first, as SQLite rowid order did.
        assert [t['id'] for t in store.tests(agent_id)] == ['run-2', 'run-1', 'run-0']
        store.save_test({'id': 'run-0', 'agent_id': agent_id, 'status': 'failed', 'outputs': {}})
        assert store.test('run-0')['status'] == 'failed' and len(store.tests(agent_id)) == 3
    finally:
        store.close()


def test_workflow_rename_preserves_history_execution_and_schedule(backend, tmp_path):
    from service.dispatch import handle

    folder = workspace(tmp_path)
    store, _, agent_id = seed(folder)
    original = store.agent(agent_id)
    schedules = Schedules(store)
    schedules.activate(agent_id, True)
    before = schedules.get(agent_id)
    source = schedules.source(agent_id)
    queued = schedules.enqueue_test(agent_id, 300)
    store.close()

    renamed = handle({'action':'rename_agent', 'directory':str(folder), 'id':agent_id,
                      'name':'  매일 재고 확인  ', 'base_version':1})['agent']
    assert renamed['config'] == {**original['config'], 'name':'매일 재고 확인'}
    assert renamed == {**original, 'config':renamed['config'], 'version':2, 'updated_at':renamed['updated_at']}
    store = Storage(folder)
    try:
        assert store.agent(agent_id) == renamed
        assert store.agent_version(agent_id, 1) == original
        assert store.agents() == [renamed]
        schedules = Schedules(store)
        assert schedules.get(agent_id) == {**before, 'agent_version':2}
        assert schedules.source(agent_id) == source
        # Already queued work retains the exact version selected when it was queued.
        assert schedules.jobs(agent_id)[0]['agent_snapshot'] == queued['agent_snapshot'] == original
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        store.db.execute('UPDATE builder_schedules SET next_at=? WHERE {ws} AND agent_id=?', (due.isoformat(), agent_id))
        store.db.commit()
        [job] = schedules.claim_due()
        assert job['agent_snapshot'] == renamed
    finally:
        store.close()


def test_workflow_rename_rejects_invalid_stale_and_other_workspace_writes(backend, tmp_path):
    alice, bob = workspace(tmp_path), workspace(tmp_path)
    store, _, agent_id = seed(alice)
    other = Storage(bob)
    try:
        for name in ['', '  ', '가'*61, '첫 줄\n다음 줄', '이름\x00', '첫 줄\u2028다음 줄', None]:
            with pytest.raises(ValueError):
                store.rename_agent(agent_id, name, 1)
        for version in [0, -1, True, 1.5, '1', None]:
            with pytest.raises(ValueError, match='버전'):
                store.rename_agent(agent_id, '새 이름', version)
        first = store.rename_agent(agent_id, '새 이름', 1)
        assert first['version'] == 2
        # Retrying the same request does not append a duplicate revision.
        assert store.rename_agent(agent_id, '새 이름', 1) == first
        with pytest.raises(ValueError, match='다른 곳'):
            store.rename_agent(agent_id, '오래된 수정', 1)
        with pytest.raises(ValueError, match='찾을 수 없습니다'):
            other.rename_agent(agent_id, '다른 회원 수정', 2)
        assert other.agents() == []
        assert store.agent(agent_id) == first
        # A rejected write rolls back cleanly and permits the next valid revision.
        assert store.rename_agent(agent_id, '최종 이름', 2)['version'] == 3
    finally:
        store.close()
        other.close()


def test_concurrent_workflow_renames_allow_only_one_base_version(backend, tmp_path):
    folder = workspace(tmp_path)
    store, _, agent_id = seed(folder)
    store.close()

    def rename(name):
        member = Storage(folder)
        try:
            try:
                return member.rename_agent(agent_id, name, 1)
            except ValueError as error:
                assert '다른 곳' in str(error)
                return None
        finally:
            member.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(rename, ['첫 번째 변경', '두 번째 변경']))
    [winner] = [result for result in results if result]
    store = Storage(folder)
    try:
        assert store.agent(agent_id) == winner
        assert winner['version'] == 2
    finally:
        store.close()


def test_schedule_job_is_claimed_once_and_lease_is_owned(backend, tmp_path):
    folder = workspace(tmp_path)
    store, _, agent_id = seed(folder)
    schedules = Schedules(store)
    schedules.activate(agent_id, True)
    due = datetime.now(timezone.utc) - timedelta(seconds=1)
    store.db.execute('UPDATE builder_schedules SET next_at=? WHERE {ws} AND agent_id=?', (due.isoformat(), agent_id))
    store.db.commit()
    store.close()

    def claim(_):
        st = Storage(folder)
        try:
            return Schedules(st).claim_due()
        finally:
            st.close()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(claim, range(3)))
    assert sum(len(r) for r in results) == 1
    [job] = [job for r in results for job in r]
    store = Storage(folder)
    try:
        schedules = Schedules(store)
        assert schedules.heartbeat(job['id'], job['lease_token'])['status'] == 'renewed'
        with pytest.raises(ValueError, match='소유권'):
            schedules.heartbeat(job['id'], 'someone-else')
        # An expired lease can be reclaimed; the old owner is then rejected.
        later = datetime.now(timezone.utc) + timedelta(minutes=10)
        [again] = schedules.claim_due(later)
        assert again['id'] == job['id'] and again['lease_token'] != job['lease_token']
        with pytest.raises(ValueError):
            schedules.finish(job['id'], job['lease_token'], 'succeeded')
        schedules.finish(again['id'], again['lease_token'], 'succeeded')
        assert schedules.jobs(agent_id)[0]['status'] == 'succeeded'
    finally:
        store.close()


def test_run_lease_rejects_a_second_owner(backend, tmp_path):
    store, _, agent_id = seed(workspace(tmp_path))
    try:
        lifecycle = RunLifecycle(store)
        run = {'id': uid(), 'agent_id': agent_id, 'created_at': '2026-11-03T00:00:00+00:00', 'status': 'queued'}
        lifecycle.start(run, 'token-a')
        with pytest.raises(ValueError, match='다른 작업자'):
            lifecycle.start({**run}, 'token-b')
        assert lifecycle.heartbeat(run['id'], 'token-a')['status'] == 'renewed'
        run['status'] = 'succeeded'
        lifecycle.finish(run, 'token-a')
        assert store.test(run['id'])['status'] == 'succeeded'
        with pytest.raises(ValueError):
            lifecycle.assert_owner(run['id'], 'token-a')
    finally:
        store.close()


def test_manual_library_enqueue_rechecks_the_current_binding(backend,tmp_path,monkeypatch):
    from workflow_engine.scheduling.library_sources import LibrarySources,library_export
    from tests.workflow_engine.test_library_schedule_sources import workflow,upload,spec
    folder=workspace(tmp_path);store=Storage(folder)
    try:
        upload(store,'old.csv');replacement=upload(store,'replacement.csv',amount=2)
        agent_id=workflow(store);manager=LibrarySources(store)
        selection=manager.save(agent_id,spec('new'),0)
        refs=library_export(store,selection,'table')['candidates'];expand=manager.expand
        def changed_after_plan(action,id,planned,chosen,token):
            other=Storage(folder)
            try:LibrarySources(other).save(agent_id,spec(asset_ids=[replacement['id']]),1)
            finally:other.close()
            return expand(action,id,planned,chosen,token)
        monkeypatch.setattr(manager,'expand',changed_after_plan)
        with pytest.raises(ValueError,match='자료함 설정이 변경'):
            manager.resolve('test',agent_id,refs,expected_selection=selection)
        assert store.db.execute('SELECT count(*) FROM builder_schedule_jobs WHERE {ws}').fetchone()[0]==0
        assert store.db.execute('SELECT count(*) FROM builder_source_processed WHERE {ws}').fetchone()[0]==0
    finally:store.close()


def test_replaced_schedule_owner_cannot_change_library_states(backend,tmp_path):
    from types import SimpleNamespace
    from service.handlers.schedules import fail_job
    from tests.workflow_engine.test_library_schedule_sources import leased_source_job
    store=Storage(workspace(tmp_path))
    try:
        previous,current,_=leased_source_job(store)
        [replacement]=Schedules(store).claim_due(current+timedelta(minutes=6))
        context=SimpleNamespace(storage=store,data={'id':previous['id'],'lease_token':previous['lease_token']})
        with pytest.raises(ValueError,match='소유권'):fail_job(context)
        assert store.db.execute('SELECT state,lease_token FROM builder_schedule_jobs WHERE {ws} AND id=?',(previous['id'],)).fetchone()==('running',replacement['lease_token'])
        for table,key in [('builder_source_snapshots','run_id'),('builder_source_processed','job_id')]:
            assert store.db.execute(f'SELECT state FROM {table} WHERE {{ws}} AND {key}=?',(previous['id'],)).fetchone()==('pending',)
        context.data['lease_token']=replacement['lease_token']
        assert fail_job(context)=={'status':'failed'}
        assert store.db.execute('SELECT state FROM builder_schedule_jobs WHERE {ws} AND id=?',(previous['id'],)).fetchone()==('failed',)
        for table,key in [('builder_source_snapshots','run_id'),('builder_source_processed','job_id')]:
            assert store.db.execute(f'SELECT state FROM {table} WHERE {{ws}} AND {key}=?',(previous['id'],)).fetchone()==('failed',)
    finally:store.close()


def test_two_workspaces_cannot_see_or_change_each_other(backend, tmp_path):
    folder_a, folder_b = workspace(tmp_path), workspace(tmp_path)
    a, asset, agent_id = seed(folder_a)
    session = a.new_session('gpt-4.1-mini', 'credential')
    a.save_test({'id': 'run-a', 'agent_id': agent_id, 'status': 'succeeded', 'outputs': {}})
    Schedules(a).enqueue_test(agent_id, 0)
    a.close()
    b = Storage(folder_b)
    try:
        for read in (lambda: b.session(session['id']), lambda: b.asset(asset['id']), lambda: b.rows(asset['id']),
                     lambda: b.agent(agent_id), lambda: b.test('run-a')):
            with pytest.raises(ValueError):
                read()
        assert b.agents() == [] and b.tests(agent_id) == []
        with pytest.raises(ValueError):
            b.update_session({**session, 'model': 'hijacked'}, session['revision'])
        schedules = Schedules(b)
        assert schedules.get(agent_id) is None and schedules.jobs(agent_id) == []
        assert schedules.claim_due(datetime.now(timezone.utc) + timedelta(days=1)) == []
        b.save_test({'id': 'run-b', 'agent_id': agent_id, 'status': 'failed', 'outputs': {}})
        if backend == 'postgres':
            # Without the code's workspace filter, row-level security still hides A's rows...
            assert b.db.connection.execute('SELECT count(*) FROM builder_assets').fetchone()[0] == 0
            assert b.db.connection.execute('SELECT count(*) FROM agent_tests').fetchone()[0] == 1
            # ...and refuses to write into A's workspace.
            import psycopg
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                b.db.connection.execute("INSERT INTO agent_tests(workspace_id,id,agent_id,json) VALUES(%s,'x','y','{}')", (folder_a.name,))
    finally:
        b.close()
    a = Storage(folder_a)
    try:
        assert a.session(session['id'])['model'] == 'gpt-4.1-mini'
        assert [t['id'] for t in a.tests(agent_id)] == ['run-a'] and a.test('run-a')['status'] == 'succeeded'
        assert len(Schedules(a).jobs(agent_id)) == 1
    finally:
        a.close()


THREAD = {'configurable': {'thread_id': 'shared-thread'}}


def counter_graph(store):
    from typing import TypedDict
    from langgraph.graph import StateGraph, START, END

    class State(TypedDict):
        count: int
    connection = checkpoint_connection(store)
    graph = StateGraph(State)
    graph.add_node('step', lambda state: {'count': state['count'] + 1})
    graph.add_edge(START, 'step')
    graph.add_edge('step', END)
    return graph.compile(checkpointer=checkpointer(connection)), connection


def test_langgraph_checkpoints_are_per_workspace(backend, tmp_path):
    folder_a, folder_b = workspace(tmp_path), workspace(tmp_path)
    a = Storage(folder_a)
    graph, connection = counter_graph(a)
    try:
        assert graph.invoke({'count': 1}, THREAD)['count'] == 2
    finally:
        connection.close()
        a.close()
    for folder, expected in ((folder_a, {'count': 2}), (folder_b, {})):
        store = Storage(folder)
        graph, connection = counter_graph(store)
        try:
            assert graph.get_state(THREAD).values == expected
        finally:
            connection.close()
            store.close()


def test_postgres_settings_follow_the_member_store_tls_rule():
    with pytest.raises(ValueError, match='SSL 옵션'):
        database.postgres_conninfo({'A4B_DATABASE_URL': 'postgresql://u@db.example.com/a4b?sslmode=disable'})
    with pytest.raises(ValueError, match='A4B_DATABASE_URL'):
        database.postgres_conninfo({})
    with pytest.raises(ValueError, match='TLS_SERVER_NAME'):
        database.postgres_conninfo({'A4B_DATABASE_URL': 'postgresql://u@127.0.0.1/a4b', 'A4B_DATABASE_TLS_SERVER_NAME': '10.0.0.1'})
    params = database.postgres_conninfo({'A4B_DATABASE_URL': 'postgresql://u@db.example.com/a4b', 'A4B_DATABASE_CA_FILE': '/data/rds.pem'})
    assert params['sslmode'] == 'verify-full' and params['sslrootcert'] == '/data/rds.pem' and 'hostaddr' not in params
    assert database.postgres_conninfo({'A4B_DATABASE_URL': 'postgresql://u@db.example.com/a4b'})['sslrootcert'] == 'system'
    # Through a tunnel the TCP address differs, but the certificate is checked for the RDS name.
    tunnel = database.postgres_conninfo({'A4B_DATABASE_URL': 'postgresql://u@127.0.0.1:15432/a4b',
                                         'A4B_DATABASE_TLS_SERVER_NAME': 'a4b.abc.ap-northeast-2.rds.amazonaws.com'})
    assert tunnel['hostaddr'] == '127.0.0.1' and tunnel['host'] == 'a4b.abc.ap-northeast-2.rds.amazonaws.com' and tunnel['port'] == '15432'


def test_sqlite_is_refused_for_a_public_server():
    assert database.storage_kind({}) == 'sqlite'
    assert database.storage_kind({'EASYFLOW_PUBLIC_URL': 'http://localhost:3030'}) == 'sqlite'
    with pytest.raises(ValueError, match='로컬 개발에서만'):
        database.storage_kind({'EASYFLOW_PUBLIC_URL': 'https://service.example.com'})
    assert database.storage_kind({'EASYFLOW_PUBLIC_URL': 'https://service.example.com', 'A4B_STORAGE': 'postgres'}) == 'postgres'
    with pytest.raises(ValueError, match='sqlite 또는 postgres'):
        database.storage_kind({'A4B_STORAGE': 'mysql'})


def test_workspace_markers_expand_per_dialect():
    sql = "SELECT t.json FROM a d JOIN b t ON {ws:t} AND t.id=d.id WHERE {ws:d} AND d.k=? AND d.s='50%' ORDER BY d.{seq}"
    assert database.sqlite_sql(sql) == "SELECT t.json FROM a d JOIN b t ON 1=1 AND t.id=d.id WHERE 1=1 AND d.k=? AND d.s='50%' ORDER BY d.rowid"
    query, slots = database.postgres_sql(sql)
    assert query == "SELECT t.json FROM a d JOIN b t ON t.workspace_id=%s AND t.id=d.id WHERE d.workspace_id=%s AND d.k=%s AND d.s='50%%' ORDER BY d.seq"
    assert database.bind(slots, ['key'], 'ws') == ['ws', 'ws', 'key']
    query, slots = database.postgres_sql('INSERT INTO t({wsc}id,v) VALUES({wsv}?,?) ON CONFLICT({wsc}id) DO NOTHING')
    assert query == 'INSERT INTO t(workspace_id,id,v) VALUES(%s,%s,%s) ON CONFLICT(workspace_id,id) DO NOTHING'
    assert database.bind(slots, [1, 2], 'ws') == ['ws', 1, 2]
    assert database.workspace_id(Path('data') / 'users' / 'A4B2C3D4-0000-4000-8000-000000000001') == 'a4b2c3d4-0000-4000-8000-000000000001'
    assert database.workspace_id(Path('data')) == 'local'


def test_malformed_member_folder_cannot_join_the_shared_local_workspace(monkeypatch):
    monkeypatch.delenv('A4B_STORAGE', raising=False)
    assert database.workspace_id(Path('data') / 'users' / 'not-a-member-id') == 'local'
    monkeypatch.setenv('A4B_STORAGE', 'postgres')
    with pytest.raises(ValueError, match='UUID'):
        database.workspace_id(Path('data') / 'users' / 'not-a-member-id')
    assert database.workspace_id(Path('data')) == 'local'


def test_reopening_storage_rewrites_only_changed_tools(backend, tmp_path, monkeypatch):
    folder = workspace(tmp_path)
    first = Storage(folder)
    tool_id, version = first.db.execute('SELECT id,version FROM registered_tools WHERE {ws} ORDER BY id').fetchone()
    first.close()
    writes = []
    kind = database.PostgresDatabase if backend == 'postgres' else database.SqliteDatabase
    execute = kind.execute
    def recording(self, sql, params=()):
        if 'INTO registered_tools' in sql:
            writes.append(params[0])
        return execute(self, sql, params)
    monkeypatch.setattr(kind, 'execute', recording)
    Storage(folder).close()
    assert writes == []
    stale = Storage(folder)
    stale.db.execute('UPDATE registered_tools SET module_path=? WHERE {ws} AND id=? AND version=?', ('old.module', tool_id, version))
    stale.db.commit()
    stale.close()
    repaired = Storage(folder)
    try:
        assert writes == [tool_id]
        row = repaired.db.execute('SELECT module_path FROM registered_tools WHERE {ws} AND id=? AND version=?', (tool_id, version)).fetchone()
        assert row[0] != 'old.module'
    finally:
        repaired.close()


def test_deferred_verification_covers_member_workspaces(backend, tmp_path):
    from verification.deferred import workspace_directories
    member = workspace(tmp_path)
    Storage(member).close()
    (tmp_path / 'users' / 'not-a-member-id').mkdir()
    if backend == 'postgres':
        assert workspace_directories(tmp_path) == [member]
    else:
        assert workspace_directories(tmp_path) == [member]
        Storage(tmp_path).close()
        assert workspace_directories(tmp_path) == [tmp_path, member]
    assert workspace_directories(tmp_path / 'empty') == [tmp_path / 'empty']


def load_migrator():
    spec = importlib.util.spec_from_file_location('migrate_sqlite_to_postgres', ROOT / 'scripts' / 'deploy' / 'migrate-sqlite-to-postgres.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(sys.platform != 'linux', reason='The EC2 release script runs on Linux.')
def test_ec2_storage_cutover_and_rollback(tmp_path):
    subprocess.run(['bash', Path(__file__).with_name('ec2_storage_cutover.sh'),
                    ROOT / 'scripts/deploy/deploy-a4b-ec2.sh', tmp_path], check=True)


def sqlite_workspace_with_data(tmp_path, monkeypatch):
    monkeypatch.delenv('A4B_STORAGE', raising=False)
    folder = workspace(tmp_path / 'data')
    store, asset, agent_id = seed(folder)
    session = store.new_session('gpt-4.1-mini', 'credential')
    store.save_test({'id': 'run-1', 'agent_id': agent_id, 'status': 'succeeded', 'outputs': {}})
    Schedules(store).enqueue_test(agent_id, 0)
    graph, connection = counter_graph(store)
    graph.invoke({'count': 1}, THREAD)
    connection.close()
    store.db.execute('INSERT INTO reply_review_decisions(run_id,review_id,json) VALUES(?,?,?)', ('run-1', 'r1', json.dumps({'decision': 'approve'})))
    store.db.commit()
    store.db.connection.execute('PRAGMA foreign_keys=OFF')  # an old file can hold orphaned rows
    store.db.execute('INSERT INTO reply_review_decisions(run_id,review_id,json) VALUES(?,?,?)', ('missing-run', 'r2', '{}'))
    store.db.commit()
    store.close()
    return folder, asset, agent_id, session


def test_migration_dry_run_counts_sqlite_rows_without_a_database(tmp_path, monkeypatch):
    monkeypatch.delenv('A4B_STORAGE', raising=False)
    folder = workspace(tmp_path / 'data')
    store, asset, agent_id = seed(folder)
    store.save_test({'id': 'run-1', 'agent_id': agent_id, 'status': 'succeeded', 'outputs': {}})
    store.close()
    migrator = load_migrator()
    [(workspace_id, found)] = migrator.workspaces(tmp_path / 'data')
    assert workspace_id == folder.name and found == folder
    counts, orphans = migrator.survey(folder)
    assert counts['builder_assets'] == 1 and counts['agent_versions'] == 1 and counts['agent_tests'] == 1
    assert counts['registered_tools'] > 0 and orphans == {}
    assert counts['checkpoints (Python LangGraph)'] == 0 and counts['agent_checkpoints (Node)'] == 0


@pytest.mark.skipif(not TEST_URL, reason='Disposable PostgreSQL required')
def test_cutover_rejects_omitted_databases_and_requires_explicit_empty_volume(tmp_path, postgres_app_conninfo):
    import psycopg
    migrator = load_migrator()
    receipt = tmp_path / '.a4b-sqlite-import'
    with pytest.raises(ValueError, match='confirm-empty'):
        migrator.import_volume(postgres_app_conninfo, tmp_path)
    assert not receipt.exists()
    migrator.import_volume(postgres_app_conninfo, tmp_path, confirm_empty=True)
    with psycopg.connect(postgres_app_conninfo) as target:
        assert target.execute('SELECT workspace_count FROM sqlite_imports WHERE id=%s', (receipt.read_text().strip(),)).fetchone() == (0,)
    (tmp_path / 'agent-checkpoints.sqlite').touch()
    with pytest.raises(ValueError, match='include-local'):
        migrator.import_volume(postgres_app_conninfo, tmp_path, confirm_empty=True)
    assert not receipt.exists()
    with pytest.raises(ValueError, match='confirm-empty'):
        migrator.import_volume(postgres_app_conninfo, tmp_path, include_local=True, confirm_empty=True)
    invalid = tmp_path / 'users' / 'unknown-member'
    invalid.mkdir(parents=True)
    (invalid / 'agent-builder.sqlite').touch()
    with pytest.raises(ValueError, match='회원 ID'):
        migrator.import_volume(postgres_app_conninfo, tmp_path, include_local=True)
    assert not receipt.exists()


@pytest.mark.skipif(not TEST_URL, reason='A4B_TEST_DATABASE_URL이 없으면 이전 스크립트 실DB 검증을 건너뜁니다.')
def test_migration_copies_workspaces_once(tmp_path, monkeypatch, postgres_app_conninfo):
    folder, asset, agent_id, session = sqlite_workspace_with_data(tmp_path, monkeypatch)
    migrator = load_migrator()
    first = migrator.copy_workspace(postgres_app_conninfo, folder.name, folder)
    assert first['builder_assets'] == 1 and first['agent_tests'] == 1 and first['reply_review_decisions'] == 1
    assert first['builder_schedule_jobs'] == 1 and first['builder_sessions'] == 1
    assert first['checkpoints (Python LangGraph)'] >= 1
    second = migrator.copy_workspace(postgres_app_conninfo, folder.name, folder)
    assert sum(second.values()) == 0
    monkeypatch.setenv('A4B_STORAGE', 'postgres')
    database.use_conninfo(postgres_app_conninfo)
    store = Storage(folder)
    try:
        assert store.dialect == 'postgres' and store.workspace_id == folder.name
        assert store.asset(asset['id'])['name'] == 'stock.csv' and list(store.rows(asset['id'])[0]) == ['code', 'stock', 'reorder_level']
        assert store.agent(agent_id)['version'] == 1 and store.session(session['id'])['revision'] == session['revision']
        assert store.test('run-1')['status'] == 'succeeded' and len(Schedules(store).jobs(agent_id)) == 1
        graph, connection = counter_graph(store)
        try:
            assert graph.get_state(THREAD).values == {'count': 2}
        finally:
            connection.close()
    finally:
        store.close()
        database.close_pools()
        database.use_conninfo(None)


@pytest.mark.skipif(not TEST_URL, reason='Disposable PostgreSQL required')
def test_cutover_receipt_is_withheld_if_duplicate_rows_have_different_contents(tmp_path, monkeypatch, postgres_app_conninfo):
    import psycopg
    folder, _, _, session = sqlite_workspace_with_data(tmp_path, monkeypatch)
    volume = tmp_path / 'data'
    migrator = load_migrator()
    migrator.import_volume(postgres_app_conninfo, volume)
    receipt = volume / '.a4b-sqlite-import'
    assert receipt.is_file()
    with psycopg.connect(postgres_app_conninfo) as target:
        target.execute("SELECT set_config('a4b.workspace_id',%s,true)", (folder.name,))
        target.execute('UPDATE builder_sessions SET revision=revision+1 WHERE id=%s', (session['id'],))
    with pytest.raises(ValueError, match='builder_sessions'):
        migrator.import_volume(postgres_app_conninfo, volume)
    assert not receipt.exists()


def test_nul_in_stored_json_does_not_break_status_queries(backend, tmp_path):
    """PostgreSQL ->> fails on \\u0000 anywhere in the document; recovery and claims must not."""
    store, _, agent_id = seed(workspace(tmp_path))
    try:
        run = {'id': uid(), 'agent_id': agent_id, 'status': 'running', 'created_at': '2026-11-03T00:00:00+00:00',
               'outputs': {'node': {'text': 'a\x00\x00b \\u0000 \\\x00'}}}
        store.save_test(run)
        RunLifecycle(store).recover()
        recovered = store.test(run['id'])
        assert recovered['status'] == 'failed'
        assert recovered['outputs']['node']['text'] == 'a\x00\x00b \\u0000 \\\x00'  # the stored document is unchanged
        schedules = Schedules(store)
        job = schedules.enqueue_test(agent_id, 0)
        store.db.execute('UPDATE builder_schedule_jobs SET json=? WHERE {ws} AND id=?',
                         (json.dumps({**job, 'note': 'a\x00b'}, ensure_ascii=False), job['id']))
        store.db.commit()
        [claimed] = schedules.claim_due(datetime.now(timezone.utc) + timedelta(seconds=30))
        assert claimed['id'] == job['id'] and claimed['note'] == 'a\x00b'
    finally:
        store.close()


def test_nul_in_string_channel_can_be_checkpointed(backend, tmp_path):
    from typing import TypedDict
    from langgraph.graph import StateGraph, START, END

    class State(TypedDict):
        text: str
    store = Storage(workspace(tmp_path))
    connection = checkpoint_connection(store)
    try:
        graph = StateGraph(State)
        graph.add_node('step', lambda state: {'text': state['text'] + '\x00end'})
        graph.add_edge(START, 'step')
        graph.add_edge('step', END)
        compiled = graph.compile(checkpointer=checkpointer(connection))
        config = {'configurable': {'thread_id': str(uuid.uuid4())}}
        compiled.invoke({'text': 'a\x00b'}, config)
        kept = '�' if backend == 'postgres' else '\x00'  # jsonb cannot hold NUL
        assert compiled.get_state(config).values['text'] == f'a{kept}b{kept}end'
    finally:
        connection.close()
        store.close()


def test_cancel_during_claim_never_runs_the_cancelled_job(backend, tmp_path):
    """A test job cancelled while claim_due holds it must not be returned as running."""
    import threading
    folder = workspace(tmp_path)
    store, _, agent_id = seed(folder)
    job = Schedules(store).enqueue_test(agent_id, 0)
    store.close()
    claimer = Storage(folder)
    outcome = {}

    def cancel():
        other = Storage(folder)
        try:
            outcome['cancel'] = Schedules(other).cancel_test(agent_id, job['id'])['status']
        except ValueError as error:
            outcome['cancel'] = str(error)
        finally:
            other.close()
    original, started = claimer.db.execute, []

    def execute(sql, params=()):
        result = original(sql, params)
        # Right after claim_due has read the candidate job, a second connection cancels it.
        if not started and 'ORDER BY {seq} LIMIT 1' in sql:
            thread = threading.Thread(target=cancel)
            thread.start()
            thread.join(timeout=1)  # blocks behind the claim's write lock on both backends
            started.append(thread)
        return result
    claimer.db.execute = execute
    try:
        claimed = Schedules(claimer).claim_due(datetime.now(timezone.utc) + timedelta(seconds=30))
    finally:
        claimer.db.execute = original
        claimer.close()
    started[0].join(timeout=15)
    check = Storage(folder)
    try:
        [stored] = Schedules(check).jobs(agent_id)
    finally:
        check.close()
    if claimed:
        assert stored['status'] == 'running' and outcome['cancel'] != 'cancelled'
    else:
        assert stored['status'] == 'cancelled' and outcome['cancel'] == 'cancelled'


@pytest.mark.skipif(not TEST_URL, reason='A4B_TEST_DATABASE_URL이 없으면 이전 스크립트 실DB 검증을 건너뜁니다.')
def test_interrupted_checkpoint_copy_is_completed_by_a_rerun(tmp_path, monkeypatch, postgres_app_conninfo):
    """A copy that stops after a checkpoint but before its writes must restore them on rerun."""
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.types import interrupt
    from typing import TypedDict
    from langgraph.graph import StateGraph, START, END
    monkeypatch.delenv('A4B_STORAGE', raising=False)
    folder = workspace(tmp_path / 'data')
    store = Storage(folder)

    class State(TypedDict):
        answer: str
    graph = StateGraph(State)
    graph.add_node('ask', lambda state: {'answer': interrupt('기준 점수를 알려주세요.')})
    graph.add_edge(START, 'ask')
    graph.add_edge('ask', END)
    connection = checkpoint_connection(store)
    compiled = graph.compile(checkpointer=checkpointer(connection))
    compiled.invoke({'answer': ''}, THREAD)
    source = [w for t in compiled.checkpointer.list(None) for w in t.pending_writes or []]
    connection.close()
    store.close()
    assert any(channel == '__interrupt__' for _, channel, _ in source)

    migrator = load_migrator()
    original = PostgresSaver.put_writes
    def lost_connection(self, config, writes, task_id, *args, **kwargs):
        # The checkpoint itself was saved; the connection drops before its pending interrupt.
        if any(channel == '__interrupt__' for channel, _ in writes):
            raise psycopg.OperationalError('connection lost during copy')
        return original(self, config, writes, task_id, *args, **kwargs)
    monkeypatch.setattr(PostgresSaver, 'put_writes', lost_connection)
    volume = tmp_path / 'data'
    receipt = volume / '.a4b-sqlite-import'
    with pytest.raises(psycopg.OperationalError):
        migrator.import_volume(postgres_app_conninfo, volume)
    assert not receipt.exists()
    monkeypatch.setattr(PostgresSaver, 'put_writes', original)
    migrator.import_volume(postgres_app_conninfo, volume)
    with psycopg.connect(postgres_app_conninfo, autocommit=True) as target:
        assert target.execute('SELECT workspace_count FROM sqlite_imports WHERE id=%s', (receipt.read_text().strip(),)).fetchone() == (1,)
        target.execute("SELECT set_config('a4b.workspace_id',%s,false)", (folder.name,))
        copied = target.execute("SELECT count(*) FROM checkpoint_writes WHERE thread_id='shared-thread' AND channel='__interrupt__'").fetchone()[0]
    assert copied == sum(1 for _, channel, _ in source if channel == '__interrupt__')
