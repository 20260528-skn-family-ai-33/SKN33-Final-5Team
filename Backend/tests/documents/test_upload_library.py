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


@pytest.mark.parametrize('table', ['agent_versions', 'agent_tests', 'builder_data_sources'])
def test_used_upload_is_protected_before_detaching_or_removing_content(tmp_path, table):
    store = Storage(tmp_path)
    asset_id = uid()
    store.add_asset(dict(id=asset_id, name='source.csv'), [{'amount': 2}])
    if table == 'agent_versions':
        store.db.execute('INSERT INTO agent_versions(agent_id,version,json) VALUES(?,1,?)',
                         (uid(), json.dumps({'config': {'document_ids': [asset_id]}})))
    elif table == 'agent_tests':
        store.db.execute('INSERT INTO agent_tests(id,agent_id,json) VALUES(?,?,?)',
                         (uid(), uid(), json.dumps({'file_ids': [asset_id]})))
    else:
        from workflow_engine.scheduling.schedules import Schedules
        Schedules(store)
        store.db.execute('INSERT INTO builder_data_sources VALUES(?,?,?,1,?)', (uid(), uid(), asset_id, 'now'))
    store.db.commit()
    try:
        with pytest.raises(ValueError, match='워크플로우|실행 기록|예약 데이터 소스'):
            uploads.prepare_delete(SimpleNamespace(storage=store, data={'id': asset_id}))
        assert store.asset(asset_id)['name'] == 'source.csv'
        assert store.rows(asset_id) == [{'amount': 2}]
    finally:
        store.close()
