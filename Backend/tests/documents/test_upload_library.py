import json
from types import SimpleNamespace

import pytest

from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from service.handlers import uploads


def test_duplicate_legacy_original_survives_deleting_one_draft_attachment(tmp_path):
    store = Storage(tmp_path)
    path = 'builder-originals/' + 'a' * 64 + '.csv'
    first, second = uid(), uid()
    session = store.new_session('model', 'credential')
    for id in (first, second):
        store.add_asset(dict(id=id, name='비용.csv', original_path=path), [])
    store.update_session({**session, 'file_ids': [first, second]}, session['revision'])
    context = SimpleNamespace(storage=store, data={'id': first})
    try:
        assert [a['id'] for a in uploads.assets(context)['assets']] == [second, first]
        assert 'original' not in uploads.prepare_delete(context)
        assert store.session(session['id'])['file_ids'] == [second]
        assert store.session(session['id'])['revision'] == session['revision'] + 2
        context.data['id'] = second
        assert uploads.prepare_delete(context)['original'] == path
        assert uploads.prepare_delete(context)['original'] == path
        assert all(a['pending_delete'] for a in uploads.assets(context)['assets'])
        with pytest.raises(ValueError):
            store.asset(second)
        for id in (second, first):
            context.data['id'] = id
            uploads.finish_delete(context)
        assert uploads.assets(context)['assets'] == []
    finally:
        store.close()


@pytest.mark.parametrize('table', ['agent_tests', 'builder_data_sources'])
def test_current_execution_and_managed_source_are_protected(tmp_path, table):
    store = Storage(tmp_path)
    asset_id = uid()
    store.add_asset(dict(id=asset_id, name='source.csv'), [{'amount': 2}])
    if table == 'agent_tests':
        store.db.execute('INSERT INTO agent_tests(id,agent_id,json) VALUES(?,?,?)',
                         (uid(), uid(), json.dumps({'file_ids': [asset_id], 'status':'running'})))
    else:
        from workflow_engine.scheduling.schedules import Schedules
        Schedules(store)
        store.db.execute('INSERT INTO builder_data_sources VALUES(?,?,?,1,?)', (uid(), uid(), asset_id, 'now'))
    store.db.commit()
    try:
        with pytest.raises(ValueError, match='실행 중|예약 데이터 소스'):
            uploads.prepare_delete(SimpleNamespace(storage=store, data={'id': asset_id}))
        assert store.asset(asset_id)['name'] == 'source.csv'
        assert store.rows(asset_id) == [{'amount': 2}]
    finally:
        store.close()


def test_saved_input_keeps_only_its_slot_after_original_cleanup(tmp_path):
    store = Storage(tmp_path)
    asset_id = uid()
    path = 'builder-originals/' + 'c' * 64 + '.csv'
    asset = dict(id=asset_id, name='기준표.csv', kind='table', columns=['가격'], preview=[{'가격':17000}],
                 original_path=path, original_bucket='fixture', text='비공개 본문', pages=['비공개 본문'])
    context = SimpleNamespace(storage=store, data={'id':asset_id})
    try:
        store.add_asset(asset, asset['preview'])
        store.db.execute('INSERT INTO agent_versions(agent_id,version,json) VALUES(?,1,?)',
                         (uid(), json.dumps({'config':{'document_ids':[asset_id]}})))
        store.db.commit()
        cleanup = uploads.prepare_delete(context)
        assert cleanup['original'] == path and cleanup['bucket'] == 'fixture'
        assert uploads.prepare_delete(context) == cleanup
        uploads.finish_delete(context)
        assert uploads.prepare_delete(context) == {}
        assert uploads.assets(context)['assets'] == []
        raw, rows = store.db.execute('SELECT json,rows_json FROM builder_assets WHERE id=?',(asset_id,)).fetchone()
        assert json.loads(raw) == {'id':asset_id,'name':'기준표.csv','kind':'table','columns':['가격'],
                                   'input_kinds':['table'],'deleted':True}
        assert json.loads(rows) == []
        for read in (store.asset, store.raw_asset, store.rows):
            with pytest.raises(ValueError):
                read(asset_id)
        with pytest.raises(ValueError):
            store.require_assets([asset_id])
    finally:
        store.close()


def test_run_only_receipt_can_be_deleted_without_changing_the_result_or_its_namesake(tmp_path):
    store = Storage(tmp_path)
    first, second = uid(), uid()
    run = dict(id=uid(), agent_id=uid(), file_ids=[first], status='succeeded', answer='정산 완료')
    context = SimpleNamespace(storage=store, data={'id':first})
    try:
        for id in (first, second):
            store.add_asset(dict(id=id, name='영수증.jpg', kind='document'), [])
        store.save_test(run)
        uploads.prepare_delete(context)
        uploads.finish_delete(context)
        assert [a['id'] for a in uploads.assets(context)['assets']] == [second]
        assert store.test(run['id'])['answer'] == '정산 완료'
        assert not store.db.execute('SELECT id FROM builder_assets WHERE id=?',(first,)).fetchone()
    finally:
        store.close()
