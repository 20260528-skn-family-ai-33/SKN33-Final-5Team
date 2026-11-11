"""Real SQLite fixtures for scoped selection, immutable inputs and atomic cursors."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from types import SimpleNamespace

import pytest

from contracts.models import AgentDraft
from infrastructure.persistence.storage import Storage
from infrastructure.persistence.ids import uid
from infrastructure.persistence.asset_library import AssetLibrary
from tests.workflow_engine.test_gmail_scheduling import initial,draft,node,bind,edge,SPEC
from workflow_engine.scheduling.library_sources import LibrarySources,library_export,source_metadata,source_tags,input_id
from workflow_engine.scheduling.schedules import Schedules,preflight_schedule


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    monkeypatch.setenv('A4B_STORAGE','sqlite');monkeypatch.setenv('EASYFLOW_PUBLIC_URL','http://localhost')
    monkeypatch.delenv('A4B_BUSINESS_RECORDS',raising=False)


def workflow(store):
    config={**draft(),'tool_ids':['read_table','write_result'],'document_ids':[],'schedule':SPEC,'report_destination':'reports',
            'workflow':{'entry':'read','nodes':[node('read','read_table'),node('write','write_result',[bind('table','read','table')])],'edges':[edge('read','write')]}}
    return store.save_agent({**initial(),'confirmed':True,'draft':AgentDraft.model_validate(config).model_dump()})


def upload(store,name='table.csv',folder=None,amount=1):
    return AssetLibrary(store).upload({'name':name,'kind':'table','format':'csv','columns':['amount'],'profile':{},'text':'',
        'processing_status':'ready','hash':str(amount),'version':1},[{'amount':amount}],folder)


def spec(mode='fixed',asset_ids=None,folder=None,space='personal',team=None):
    return {'mode':mode,'space':space,'team_id':team,'folder_id':folder,'asset_ids':asset_ids or [],'target_node_id':'read'}


def snapshot(store,selection,refs):
    return {**library_export(store,selection,'table',refs),'selection':selection}


def test_fixed_id_survives_move_rename_and_deletion_of_its_old_folder(tmp_path):
    store=Storage(tmp_path)
    try:
        library=AssetLibrary(store);first=library.create_folder('first');second=library.create_folder('second')
        asset=upload(store,folder=first['id']);id=workflow(store);manager=LibrarySources(store)
        selected=manager.save(id,spec(asset_ids=[asset['id']],folder=first['id']),0)
        original=library_export(store,selected,'table')['candidates'][0]
        library.move_asset(asset['id'],second['id']);library.delete_folder(first['id'])
        changed=store.raw_asset(asset['id']);changed['name']='renamed.csv'
        store.db.execute('UPDATE builder_assets SET json=? WHERE {ws} AND id=?',(json.dumps(changed),asset['id']));store.db.commit()
        current=library_export(store,selected,'table')['candidates'][0]
        assert current['id']==original['id'] and current['version']==original['version']
        assert snapshot(store,selected,[original])['assets'][0]['rows']==[{'amount':1}]
    finally:store.close()


def test_latest_is_direct_folder_registered_time_then_id_not_review_updated_time(tmp_path):
    store=Storage(tmp_path)
    try:
        library=AssetLibrary(store);parent=library.create_folder('parent');child=library.create_folder('child',parent['id'])
        old=upload(store,'old.csv',parent['id']);recent=upload(store,'recent.csv',parent['id']);upload(store,'nested.csv',child['id'])
        for asset,created,updated in [(old,'2026-01-01T00:00:00Z','2027-01-01T00:00:00Z'),(recent,'2026-02-01T00:00:00Z','2026-02-01T00:00:00Z')]:
            raw=store.raw_asset(asset['id']);raw.update(created_at=created,updated_at=updated)
            store.db.execute('UPDATE builder_assets SET json=? WHERE {ws} AND id=?',(json.dumps(raw),asset['id']))
        store.db.commit();id=workflow(store);manager=LibrarySources(store);selection=manager.save(id,spec('latest',folder=parent['id']),0)
        candidates=library_export(store,selection,'table')['candidates']
        assert {ref['id'] for ref in candidates}=={old['id'],recent['id']}
        assert manager.resolve('test',id,candidates)['refs'][0]['id']==recent['id']
        selection=manager.save(id,spec('latest',folder=child['id']),1)
        with pytest.raises(ValueError,match='최신 파일'):manager.resolve('test',id,[])
    finally:store.close()


def test_all_folder_is_one_report_with_all_direct_files_and_original_provenance(tmp_path):
    from workflow_engine.runner import run_workflow
    store=Storage(tmp_path)
    try:
        library=AssetLibrary(store);folder=library.create_folder('weekly');child=library.create_folder('nested',folder['id'])
        first=upload(store,'first.csv',folder['id'],amount=11);second=upload(store,'second.csv',folder['id'],amount=22)
        upload(store,'nested.csv',child['id'],amount=99);upload(store,'outside.csv',amount=88)
        id=workflow(store);manager=LibrarySources(store);selection=manager.save(id,spec('all',folder=folder['id']),0)
        candidates=library_export(store,selection,'table')['candidates']
        resolved=manager.resolve('test',id,candidates)
        assert resolved['status']=='resolved' and {ref['id'] for ref in resolved['refs']}=={first['id'],second['id']}
        assert resolved['refs']==manager.resolve('test',id,list(reversed(candidates)))['refs']
        assert store.db.execute('SELECT count(*) FROM builder_schedule_jobs WHERE {ws}').fetchone()[0]==0
        view=manager.stage(resolved['run_id'],store.agent(id),snapshot(store,selection,resolved['refs']))
        [merged_id]=view.bound['config']['workflow']['nodes'][0]['settings']['file_ids']
        assert sorted(row['amount'] for row in view.rows(merged_id))==[11,22]
        assert view.asset(merged_id)['columns']==['amount']
        tags=source_tags(view)
        assert {item['asset_id'] for item in tags['library_source_files']}=={first['id'],second['id']}
        for item in tags['library_source_files']:
            assert view.rows(item['execution_asset_id']) in ([{'amount':11}],[{'amount':22}])
        run=run_workflow(view,id,'실행','unused',forced_run_id=resolved['run_id'],run_tags=tags)
        assert run['status']=='succeeded',run.get('error')
        assert sorted(row['amount'] for row in run['rows'])==[11,22]
        assert json.loads(run['artifact']['content'])==run['rows']
        assert len(store.tests(id))==1
        assert store.db.execute('SELECT count(*) FROM builder_source_processed WHERE {ws}').fetchone()[0]==0
    finally:store.close()


def test_all_folder_reselects_new_files_but_retries_keep_the_original_snapshot(tmp_path):
    store=Storage(tmp_path)
    try:
        library=AssetLibrary(store);folder=library.create_folder('selected');other=library.create_folder('other')
        first=upload(store,'first.csv',folder['id'],amount=1);second=upload(store,'second.csv',folder['id'],amount=2)
        id=workflow(store);manager=LibrarySources(store);selection=manager.save(id,spec('all',folder=folder['id']),0)
        resolved=manager.resolve('test',id,library_export(store,selection,'table')['candidates'])
        view=manager.stage(resolved['run_id'],store.agent(id),snapshot(store,selection,resolved['refs']))
        [merged_id]=view.bound['config']['workflow']['nodes'][0]['settings']['file_ids']
        third=upload(store,'third.csv',folder['id'],amount=3)
        next_run=manager.resolve('test',id,library_export(store,selection,'table')['candidates'])
        assert {ref['id'] for ref in next_run['refs']}=={first['id'],second['id'],third['id']}
        changed=store.raw_asset(first['id']);changed['hash']='changed'
        store.db.execute('UPDATE builder_assets SET json=?,rows_json=? WHERE {ws} AND id=?',(json.dumps(changed),'[{"amount":99}]',first['id']));store.db.commit()
        library.move_asset(first['id'],other['id'])
        verified=library_export(store,selection,'table',resolved['refs'],verify_only=True)
        retried=manager.stage(resolved['run_id'],store.agent(id),{**verified,'selection':selection})
        assert retried.bound['config']['workflow']['nodes'][0]['settings']['file_ids']==[merged_id]
        assert sorted(row['amount'] for row in retried.rows(merged_id))==[1,2]
        assert len(retried.snapshot['refs'])==2
    finally:store.close()


def test_all_documents_share_one_extraction_report_with_both_file_sources(tmp_path):
    from tests.workflow_engine.test_workflow import flow,FakeLLM
    from workflow_engine.runner import run_workflow
    store=Storage(tmp_path)
    try:
        library=AssetLibrary(store);folder=library.create_folder('documents');nested=library.create_folder('nested',folder['id'])
        for name,parent in [('first.txt',folder['id']),('second.txt',folder['id']),('nested.txt',nested['id'])]:
            library.upload({'name':name,'kind':'document','format':'txt','text':'제목: 테스트\n작성일: 2026-10-12',
                            'processing_status':'ready'},[],parent)
        config={**draft(),'tool_ids':['read_document','extract_information','export_result'],'document_ids':[],
                'workflow':flow()}
        id=store.save_agent({**initial(),'confirmed':True,'draft':AgentDraft.model_validate(config).model_dump()})
        manager=LibrarySources(store);selection=manager.save(id,spec('all',folder=folder['id']),0)
        resolved=manager.resolve('test',id,library_export(store,selection,'document')['candidates'])
        payload={**library_export(store,selection,'document',resolved['refs']),'selection':selection}
        view=manager.stage(resolved['run_id'],store.agent(id),payload)
        assert len(view.bound['config']['workflow']['nodes'][0]['settings']['file_ids'])==2
        run=run_workflow(view,id,'추출','unused',llm=FakeLLM(),forced_run_id=resolved['run_id'],run_tags=source_tags(view))
        assert run['status']=='succeeded',run.get('error')
        records=json.loads(run['artifact']['content'])
        assert len(records)==4 and {record['file_name'] for record in records}=={'first.txt','second.txt'}
        assert len(store.tests(id))==1
        assert len(run['library_source_files'])==2
    finally:store.close()


@pytest.mark.parametrize('content',[
    {'text':'   '},
    {'text':'사용할 수 없는 요약 본문','pages':['   ']},
    {'text':'선택하지 않은 본문','parser_metadata':{'document_analysis':{
        'contract_version':'a4b.document_analysis.v1','blocks':[{'selected':False,'text':'선택하지 않은 본문'}]}}},
])
def test_all_documents_reject_ready_files_without_readable_selected_content(tmp_path,content):
    store=Storage(tmp_path)
    try:
        library=AssetLibrary(store)
        asset=library.upload({'name':'blank-ready.txt','kind':'document','format':'txt',
                              'processing_status':'ready',**content},[])
        selection=spec('all')
        with pytest.raises(ValueError,match='blank-ready.txt.*읽을 본문이 없습니다'):
            library_export(store,selection,'document')
        latest={**selection,'mode':'latest'}
        assert library_export(store,latest,'document')['candidates'][0]['id']==asset['id']
    finally:store.close()


def test_all_folder_rejects_empty_and_oversized_batches_without_partial_selection(tmp_path):
    store=Storage(tmp_path)
    try:
        id=workflow(store);manager=LibrarySources(store);selection=manager.save(id,spec('all'),0)
        with pytest.raises(ValueError,match='함께 읽을 파일이 없습니다'):manager.resolve('test',id,[])
        asset=upload(store);[ref]=library_export(store,selection,'table')['candidates']
        candidates=[{**ref,'id':uid()} for _ in range(101)]
        with pytest.raises(ValueError,match='최대 100개'):manager.resolve('test',id,candidates)
        with pytest.raises(ValueError,match='1~100개'):manager.stage(uid(),store.agent(id),{'selection':selection,'refs':[],'assets':[]})
        assert store.db.execute('SELECT count(*) FROM builder_source_snapshots WHERE {ws}').fetchone()[0]==0
        assert store.db.execute('SELECT count(*) FROM builder_schedule_jobs WHERE {ws}').fetchone()[0]==0
    finally:store.close()


@pytest.mark.parametrize('problem',['partial','wrong_kind'])
def test_all_folder_never_silently_skips_an_unreadable_file(tmp_path,problem):
    store=Storage(tmp_path)
    try:
        upload(store,'good.csv');id=workflow(store);manager=LibrarySources(store)
        selection=manager.save(id,spec('all'),0)
        if problem=='partial':
            asset=upload(store,'unfinished.csv');raw=store.raw_asset(asset['id']);raw['parser_status']='partial'
            store.db.execute('UPDATE builder_assets SET json=? WHERE {ws} AND id=?',(json.dumps(raw),asset['id']));store.db.commit()
            expected='unfinished.csv'
        else:
            AssetLibrary(store).upload({'name':'notes.txt','kind':'document','format':'txt','text':'업무 내용','processing_status':'ready'},[])
            expected='notes.txt'
        with pytest.raises(ValueError,match=expected):library_export(store,selection,'table')
        # Other folder modes retain their existing eligible-file behavior.
        latest={**selection,'mode':'latest'}
        assert len(library_export(store,latest,'table')['candidates'])==1
    finally:store.close()


def test_new_reservations_skip_success_retry_failed_same_snapshot_and_accept_new_version(tmp_path):
    store=Storage(tmp_path)
    try:
        asset=upload(store);id=workflow(store);manager=LibrarySources(store);selection=manager.save(id,spec('new'),0)
        refs=library_export(store,selection,'table')['candidates']
        expanded=manager.resolve('test',id,refs);[child]=expanded['job_ids']
        assert expanded['enqueued_count']==1 and manager.resolve('test',id,refs)['status']=='skipped'
        view=manager.stage(child,store.agent(id),snapshot(store,selection,refs))
        internal=input_id(selection,view.snapshot['assets'][0])
        assert view.rows(internal)==[{'amount':1}]
        meta=source_metadata(view)
        failed={'id':child,'agent_id':id,'status':'failed','library_source':meta}
        manager.complete(failed)
        store.db.execute("UPDATE builder_schedule_jobs SET state='failed' WHERE {ws} AND id=?",(child,));store.db.commit()
        assert manager.resolve('test',id,refs)['job_ids']==[child]
        # Current content may change; a permission-only export still authorizes
        # access, while retry uses the immutable earlier parsed snapshot.
        raw=store.raw_asset(asset['id']);raw['hash']='changed'
        store.db.execute('UPDATE builder_assets SET json=?,rows_json=? WHERE {ws} AND id=?',(json.dumps(raw),'[{"amount":99}]',asset['id']));store.db.commit()
        verified=library_export(store,selection,'table',refs,True)
        pinned=manager.stage(child,store.agent(id),{**verified,'selection':selection})
        assert pinned.rows(internal)==[{'amount':1}]
        with pytest.raises(ValueError,match='버전'):library_export(store,selection,'table',refs)
        manager.complete({**failed,'status':'succeeded'})
        manager.complete(failed)  # A late failed callback cannot erase success.
        assert manager.resolve('test',id,refs)['enqueued_count']==0
        new_refs=library_export(store,selection,'table')['candidates']
        assert new_refs[0]['version']!=refs[0]['version']
        assert manager.resolve('test',id,new_refs)['enqueued_count']==1
    finally:store.close()


def test_two_workers_cannot_enqueue_the_same_unprocessed_file(tmp_path):
    store=Storage(tmp_path);upload(store);id=workflow(store);selection=LibrarySources(store).save(id,spec('new'),0)
    refs=library_export(store,selection,'table')['candidates'];store.close()
    def reserve():
        scoped=Storage(tmp_path)
        try:return LibrarySources(scoped).resolve('test',id,refs)
        finally:scoped.close()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:reserve(),range(2)))
    assert sum(result['enqueued_count'] for result in results)==1
    store=Storage(tmp_path)
    try:assert store.db.execute('SELECT count(*) FROM builder_source_processed WHERE {ws}').fetchone()[0]==1
    finally:store.close()


def test_source_changes_pause_cancel_queued_jobs_and_never_reset_on_idempotent_save(tmp_path):
    store=Storage(tmp_path)
    try:
        upload(store);id=workflow(store);manager=LibrarySources(store);raw=spec('new');selection=manager.save(id,raw,0)
        refs=library_export(store,selection,'table')['candidates'];child=manager.resolve('test',id,refs)['job_ids'][0]
        store.db.execute('UPDATE builder_schedules SET active=1 WHERE {ws} AND agent_id=?',(id,));store.db.commit()
        assert manager.save(id,raw,1)==selection
        assert Schedules(store).get(id)['active']
        changed=manager.save(id,spec('latest'),1)
        assert changed['revision']==2 and not Schedules(store).get(id)['active']
        assert store.db.execute('SELECT state FROM builder_schedule_jobs WHERE {ws} AND id=?',(child,)).fetchone()[0]=='cancelled'
        with pytest.raises(ValueError,match='변경'):manager.save(id,raw,1)
        assert manager.save(id,None,2) is None
        assert manager.save(id,raw,0)['revision']==4
    finally:store.close()


def test_team_overlay_is_not_a_personal_upload_and_cannot_shadow_other_reader_uuid(tmp_path):
    owner=Storage(tmp_path/'users'/uid());team_id=uid();team=Storage(tmp_path/'teams'/team_id)
    try:
        personal=upload(owner,'personal.csv',amount=3)
        foreign={**personal,'name':'team.csv','hash':'team-hash'}
        team.add_asset(foreign,[{'amount':7}])
        id=workflow(owner);agent=owner.agent(id)
        agent['config']['document_ids']=[personal['id']]
        agent['config']['workflow']['nodes'].insert(1,node('other','read_table',file_ids=[personal['id']]))
        selection={**spec(asset_ids=[foreign['id']],space='team',team=team_id),'revision':1}
        refs=library_export(team,selection,'table')['candidates'];payload=snapshot(team,selection,refs)
        view=LibrarySources(owner).stage(uid(),agent,payload)
        internal=view.bound['config']['workflow']['nodes'][0]['settings']['file_ids'][0]
        assert internal!=personal['id'] and view.rows(internal)==[{'amount':7}]
        assert view.rows(personal['id'])==[{'amount':3}]
        from workflow_engine.executors.dispatch import execute_node
        read=execute_node(view.bound['config']['workflow']['nodes'][0],{'file_ids':[],'outputs':{}},view,view.bound['config'],None)
        assert read['table']['rows']==[{'amount':7}] and read['table']['file_id']==internal
        assert owner.db.execute('SELECT count(*) FROM builder_assets WHERE {ws}').fetchone()[0]==1
        assert view.bound['config']['workflow']['nodes'][1]['settings']['file_ids']==[personal['id']]
        with pytest.raises(ValueError,match='공간'):library_export(owner,selection,'table')
    finally:owner.close();team.close()


def test_new_empty_folder_is_valid_for_activation_but_latest_empty_is_not(tmp_path):
    store=Storage(tmp_path)
    try:
        id=workflow(store);manager=LibrarySources(store);selection=manager.save(id,spec('new'),0)
        preview=manager.resolve('schedule',id,[])
        view=manager.stage(None,store.agent(id),{'selection':selection,'refs':preview['refs'],'assets':[]})
        assert Schedules(view).activate(id,True)['active']
        assert manager.resolve('test',id,[])['status']=='skipped'
        assert Schedules(store).jobs(id)[0]['message']
        with pytest.raises(ValueError,match='서버'):Schedules(store).activate(id,True)
    finally:store.close()


def test_fixed_and_pinned_exports_read_only_the_requested_ids(tmp_path,monkeypatch):
    store=Storage(tmp_path)
    try:
        assets=[upload(store,f'table{index}.csv',amount=index) for index in range(12)]
        id=workflow(store);manager=LibrarySources(store);selection=manager.save(id,spec(asset_ids=[assets[5]['id']]),0)
        read_ids=[];read=store.asset
        def tracked(asset_id):read_ids.append(asset_id);return read(asset_id)
        monkeypatch.setattr(store,'asset',tracked)
        refs=library_export(store,selection,'table')['candidates']
        assert read_ids==[assets[5]['id']]
        read_ids.clear();library_export(store,selection,'table',refs,True)
        assert read_ids==[assets[5]['id']]
    finally:store.close()


def test_pinned_latest_input_survives_move_and_old_folder_deletion(tmp_path):
    store=Storage(tmp_path)
    try:
        library=AssetLibrary(store);folder=library.create_folder('selected');other=library.create_folder('other')
        asset=upload(store,folder=folder['id']);id=workflow(store);manager=LibrarySources(store)
        selection=manager.save(id,spec('latest',folder=folder['id']),0);refs=library_export(store,selection,'table')['candidates']
        library.move_asset(asset['id'],other['id']);library.delete_folder(folder['id'])
        assert library_export(store,selection,'table',refs,True)['verified_only']
        with pytest.raises(ValueError,match='폴더'):library_export(store,selection,'table')
    finally:store.close()


def test_resolve_rejects_a_changed_plan_and_foreign_space_candidates(tmp_path):
    store=Storage(tmp_path)
    try:
        upload(store);id=workflow(store);manager=LibrarySources(store)
        old=manager.save(id,spec('new'),0);refs=library_export(store,old,'table')['candidates']
        manager.save(id,spec('latest'),1)
        with pytest.raises(ValueError,match='변경'):manager.resolve('test',id,refs,expected_selection=old)
        current=manager.get(id)
        with pytest.raises(ValueError,match='공간'):manager.resolve('test',id,[{**refs[0],'space':'team','team_id':uid()}],expected_selection=current)
        assert store.db.execute('SELECT count(*) FROM builder_source_processed WHERE {ws}').fetchone()[0]==0
    finally:store.close()


def test_binding_change_cancels_an_ordinary_queued_parent_before_it_can_pick_the_new_source(tmp_path):
    store=Storage(tmp_path)
    try:
        asset=upload(store);id=workflow(store);manager=LibrarySources(store);job_id=uid()
        job={'id':job_id,'agent_id':id,'agent_snapshot':store.agent(id),'kind':'schedule_test','due_at':'2026-01-01T00:00:00Z'}
        store.db.execute("INSERT INTO builder_schedule_jobs(id,agent_id,slot_key,state,json) VALUES(?,?,?,'queued',?)",(job_id,id,'test:'+job_id,json.dumps(job)));store.db.commit()
        manager.save(id,spec(asset_ids=[asset['id']]),0)
        assert store.db.execute('SELECT state FROM builder_schedule_jobs WHERE {ws} AND id=?',(job_id,)).fetchone()[0]=='cancelled'
        assert manager.plan('scheduled_run',job_id)['selection'] is None
    finally:store.close()


def test_original_plan_binds_alias_to_the_owned_run_and_snapshot(tmp_path):
    store=Storage(tmp_path/'users'/uid());other=Storage(tmp_path/'users'/uid())
    try:
        asset=upload(store);id=workflow(store);manager=LibrarySources(store)
        raw=store.raw_asset(asset['id']);raw['original_path']='builder-originals/'+'a'*64+'.csv'
        store.db.execute('UPDATE builder_assets SET json=? WHERE {ws} AND id=?',(json.dumps(raw),asset['id']));store.db.commit()
        selection=manager.save(id,spec(asset_ids=[asset['id']]),0);refs=library_export(store,selection,'table')['candidates']
        run_id=uid();view=manager.stage(run_id,store.agent(id),snapshot(store,selection,refs))
        tags=source_tags(view);alias=tags['library_source_files'][0]['execution_asset_id']
        store.save_test({'id':run_id,'agent_id':id,'status':'succeeded','outputs':{},**tags})
        planned=manager.original_plan(run_id,alias)
        assert planned['asset']['id']==asset['id'] and planned['asset']['original_path']==raw['original_path']
        assert 'rows' not in planned and 'text' not in planned['asset']
        with pytest.raises(ValueError):manager.original_plan(run_id,asset['id'])
        with pytest.raises(ValueError):manager.original_plan(uid(),alias)
        with pytest.raises(ValueError):LibrarySources(other).original_plan(run_id,alias)
        another=uid();store.save_test({'id':another,'agent_id':id,'status':'succeeded','outputs':{}})
        with pytest.raises(ValueError):manager.original_plan(another,alias)
    finally:store.close();other.close()


def test_concurrent_binding_changes_reject_the_stale_revision(tmp_path):
    store=Storage(tmp_path);asset=upload(store);id=workflow(store)
    LibrarySources(store).save(id,spec(asset_ids=[asset['id']]),0);store.close()
    def change(mode):
        scoped=Storage(tmp_path)
        try:
            try:return LibrarySources(scoped).save(id,spec(mode),1)['revision']
            except ValueError:return 'stale'
        finally:scoped.close()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(change,['latest','new']))
    assert sorted(map(str,results))==['2','stale']


@pytest.mark.parametrize('replacement',['fixed','removed'])
def test_manual_new_expansion_rechecks_the_binding_inside_the_enqueue_transaction(tmp_path,monkeypatch,replacement):
    store=Storage(tmp_path)
    try:
        old_file=upload(store,'old.csv',amount=1);new_file=upload(store,'new.csv',amount=2)
        id=workflow(store);manager=LibrarySources(store);selection=manager.save(id,spec('new'),0)
        refs=library_export(store,selection,'table')['candidates']
        expand=manager.expand
        def changed_after_plan(action,agent_id,planned,chosen,token):
            other=Storage(tmp_path)
            try:LibrarySources(other).save(id,spec(asset_ids=[new_file['id']]) if replacement=='fixed' else None,1)
            finally:other.close()
            return expand(action,agent_id,planned,chosen,token)
        monkeypatch.setattr(manager,'expand',changed_after_plan)
        with pytest.raises(ValueError,match='자료함 설정이 변경'):
            manager.resolve('test',id,refs,expected_selection=selection)
        current=manager.get(id)
        assert (current is None) if replacement=='removed' else current['asset_ids']==[new_file['id']]
        assert store.db.execute('SELECT count(*) FROM builder_schedule_jobs WHERE {ws}').fetchone()[0]==0
        assert store.db.execute('SELECT count(*) FROM builder_source_processed WHERE {ws}').fetchone()[0]==0
        assert store.db.execute('SELECT count(*) FROM builder_source_snapshots WHERE {ws}').fetchone()[0]==0
        assert old_file['id']!=new_file['id']
    finally:store.close()


def leased_source_job(store):
    upload(store);id=workflow(store);manager=LibrarySources(store)
    selection=manager.save(id,spec('new'),0);refs=library_export(store,selection,'table')['candidates']
    [job_id]=manager.resolve('test',id,refs)['job_ids']
    view=manager.stage(job_id,store.agent(id),snapshot(store,selection,refs))
    current=datetime.now(timezone.utc)+timedelta(seconds=1)
    [job]=Schedules(store).claim_due(current)
    run={'id':job_id,'agent_id':id,'status':'succeeded','outputs':{},'library_source':source_metadata(view)}
    return job,current,run


@pytest.mark.parametrize('recorded_success',[False,True])
def test_fail_job_rejects_a_replaced_owner_without_changing_source_state(tmp_path,recorded_success):
    from service.handlers.schedules import fail_job
    store=Storage(tmp_path)
    try:
        previous,current,run=leased_source_job(store)
        if recorded_success:store.save_test(run)
        [replacement]=Schedules(store).claim_due(current+timedelta(minutes=6))
        assert previous['lease_token']!=replacement['lease_token']
        context=SimpleNamespace(storage=store,data={'id':previous['id'],'lease_token':previous['lease_token']})
        with pytest.raises(ValueError,match='소유권'):fail_job(context)
        assert store.db.execute('SELECT state,lease_token FROM builder_schedule_jobs WHERE {ws} AND id=?',(previous['id'],)).fetchone()==('running',replacement['lease_token'])
        assert store.db.execute('SELECT state FROM builder_source_snapshots WHERE {ws} AND run_id=?',(previous['id'],)).fetchone()==('pending',)
        assert store.db.execute('SELECT state FROM builder_source_processed WHERE {ws} AND job_id=?',(previous['id'],)).fetchone()==('pending',)
    finally:store.close()


@pytest.mark.parametrize('status',['succeeded','failed'])
def test_schedule_finish_rolls_back_job_and_source_updates_together(tmp_path,monkeypatch,status):
    store=Storage(tmp_path)
    try:
        job,_,run=leased_source_job(store)
        operation='complete' if status=='succeeded' else 'failed_job'
        update=getattr(LibrarySources,operation)
        def interrupted(manager,value,commit=True):
            update(manager,value,commit=False)
            raise RuntimeError('simulated source update interruption')
        monkeypatch.setattr(LibrarySources,operation,interrupted)
        with pytest.raises(RuntimeError,match='simulated'):
            Schedules(store).finish(job['id'],job['lease_token'],status,run_id=job['id'],source_run=run if status=='succeeded' else None)
        assert store.db.execute('SELECT state,lease_token FROM builder_schedule_jobs WHERE {ws} AND id=?',(job['id'],)).fetchone()==('running',job['lease_token'])
        assert store.db.execute('SELECT state FROM builder_source_snapshots WHERE {ws} AND run_id=?',(job['id'],)).fetchone()==('pending',)
        assert store.db.execute('SELECT state FROM builder_source_processed WHERE {ws} AND job_id=?',(job['id'],)).fetchone()==('pending',)
    finally:store.close()


@pytest.mark.parametrize('status',['succeeded','failed'])
def test_current_owner_finishes_job_and_source_state_in_one_transaction(tmp_path,status):
    store=Storage(tmp_path)
    try:
        job,_,run=leased_source_job(store)
        Schedules(store).finish(job['id'],job['lease_token'],status,run_id=job['id'],source_run=run if status=='succeeded' else None)
        assert store.db.execute('SELECT state FROM builder_schedule_jobs WHERE {ws} AND id=?',(job['id'],)).fetchone()==(status,)
        assert store.db.execute('SELECT state FROM builder_source_snapshots WHERE {ws} AND run_id=?',(job['id'],)).fetchone()==(status,)
        assert store.db.execute('SELECT state FROM builder_source_processed WHERE {ws} AND job_id=?',(job['id'],)).fetchone()==(status,)
    finally:store.close()
