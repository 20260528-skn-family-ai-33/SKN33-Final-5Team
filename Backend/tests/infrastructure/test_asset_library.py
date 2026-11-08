"""Personal folder metadata preserves uploads and every existing file reference."""
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from infrastructure.persistence.asset_library import AssetLibrary
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from service.handlers import asset_library as handlers, uploads


@pytest.fixture(autouse=True)
def local_storage(monkeypatch):
    monkeypatch.setenv('A4B_STORAGE', 'sqlite')
    monkeypatch.setenv('EASYFLOW_PUBLIC_URL', 'http://localhost')
    monkeypatch.delenv('A4B_BUSINESS_RECORDS', raising=False)


@pytest.fixture
def store(tmp_path):
    current = Storage(tmp_path / 'users' / uid())
    try:
        yield current
    finally:
        current.close()


def context(store, **data):
    return SimpleNamespace(storage=store, data=data)


def asset(name='원본.csv', **fields):
    return dict(name=name, kind='table', format='csv', columns=['amount'],
                preview=[{'amount': '12'}], original_path='builder-originals/original.csv',
                created_at='2026-11-09T00:00:00Z', size_bytes=12, **fields)


def test_existing_assets_stay_at_root_and_moves_preserve_workflow_and_original(store):
    library = AssetLibrary(store)
    original = dict(id=uid(), **asset())
    store.add_asset(original, [{'amount': '12'}])
    session = store.new_session('model', 'credential')
    store.update_session({**session, 'file_ids': [original['id']]}, session['revision'])
    workflow = dict(id=uid(), session_id=session['id'], config={'name': '업무', 'document_ids': [original['id']]})
    store.db.execute('INSERT INTO agent_versions(agent_id,version,json) VALUES(?,1,?)',
                     (workflow['id'], json.dumps(workflow)))
    store.db.commit()
    snapshot = store.db.execute('SELECT json FROM agent_versions').fetchone()[0]
    assert library.listing()['assets'][0]['id'] == original['id']
    folder = library.create_folder('보관')
    assert library.move_asset(original['id'], folder['id'])['folder_id'] == folder['id']
    library.update_folder(folder['id'], name='이름 변경')
    assert library.listing()['assets'] == []
    listed = library.listing(folder['id'])['assets'][0]
    assert listed['id'] == original['id'] and listed['has_original']
    assert listed['usages'][0]['kind'] == 'workflow'
    assert uploads.assets(context(store))['assets'][0]['id'] == original['id']
    assert store.raw_asset(original['id']) == original
    assert store.rows(original['id']) == [{'amount': '12'}]
    assert store.session(session['id'])['file_ids'] == [original['id']]
    assert store.db.execute('SELECT json FROM agent_versions').fetchone()[0] == snapshot
    assert library.move_asset(original['id'])['folder_id'] is None


def test_standalone_upload_is_atomic_and_server_assigns_identity(store):
    library = AssetLibrary(store)
    folder = library.create_folder('신규')
    supplied = dict(id=uid(), folder_id=uid(), **asset())
    saved = library.upload(supplied, [{'amount': '12'}], folder['id'])
    assert saved['id'] != supplied['id'] and saved['created_at'] != supplied['created_at']
    assert store.raw_asset(saved['id'])['original_path'] == supplied['original_path']
    assert 'folder_id' not in store.raw_asset(saved['id'])
    assert store.rows(saved['id']) == [{'amount': '12'}]
    assert store.db.execute('SELECT count(*) FROM builder_sessions').fetchone()[0] == 0
    with pytest.raises(ValueError, match='폴더'):
        library.upload(asset(), [], uid())
    assert store.db.execute('SELECT count(*) FROM builder_assets').fetchone()[0] == 1
    assert store.db.execute('SELECT count(*) FROM library_asset_placements').fetchone()[0] == 1


def test_failed_upload_rolls_back_the_asset_and_placement(store, monkeypatch):
    library = AssetLibrary(store)
    def unavailable(*args):
        raise RuntimeError('placement write failed')
    monkeypatch.setattr(library, '_place', unavailable)
    with pytest.raises(RuntimeError, match='placement'):
        library.upload(asset(), [])
    assert store.db.execute('SELECT count(*) FROM builder_assets').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM library_asset_placements').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM library_spaces').fetchone()[0] == 0


def test_sibling_names_are_trimmed_normalized_and_unique(store):
    library = AssetLibrary(store)
    first = library.create_folder('  Reports  ')
    assert first['name'] == 'Reports'
    with pytest.raises(ValueError, match='같은'):
        library.create_folder('reports')
    library.create_folder('\u00e9')
    with pytest.raises(ValueError, match='같은'):
        library.create_folder('e\u0301')
    parent = library.create_folder('다른 위치')
    library.create_folder('reports', parent['id'])
    assert len(library.folders()) == 4


@pytest.mark.parametrize('name', ['', '  ', '.', '..', 'a/b', 'a\\b', 'a\nb', 'a\0b', 'a\u2028b', 'x' * 81, 7])
def test_invalid_folder_names_are_rejected_without_writes(store, name):
    with pytest.raises(ValueError):
        AssetLibrary(store).create_folder(name)
    assert store.db.execute('SELECT count(*) FROM library_folders').fetchone()[0] == 0


def test_folder_rename_move_and_cycles(store):
    library = AssetLibrary(store)
    parent = library.create_folder('상위')
    child = library.create_folder('하위', parent['id'])
    other = library.create_folder('다른 상위')
    duplicate = library.create_folder('하위', other['id'])
    with pytest.raises(ValueError, match='같은'):
        library.update_folder(child['id'], parent_id=other['id'], move=True)
    for destination in (parent['id'], child['id']):
        with pytest.raises(ValueError, match='하위 폴더'):
            library.update_folder(parent['id'], parent_id=destination, move=True)
    library.update_folder(duplicate['id'], name='다른 이름')
    moved = library.update_folder(child['id'], parent_id=other['id'], move=True)
    assert moved['parent_id'] == other['id']
    assert library.update_folder(child['id'], parent_id=None, move=True)['parent_id'] is None
    library.delete_folder(parent['id'])


def test_workspace_and_future_team_space_ids_are_inaccessible(store, tmp_path):
    library = AssetLibrary(store)
    folder = library.create_folder('개인')
    saved = library.upload(asset(), [], folder['id'])
    other = Storage(tmp_path / 'users' / uid())
    try:
        outsider = AssetLibrary(other)
        assert outsider.space_id != library.space_id
        assert outsider.listing()['folders'] == [] and outsider.listing()['assets'] == []
        for operation in (lambda: outsider.listing(folder['id']),
                          lambda: outsider.create_folder('침범', folder['id']),
                          lambda: outsider.update_folder(folder['id'], name='침범'),
                          lambda: outsider.delete_folder(folder['id']),
                          lambda: outsider.move_asset(saved['id'])):
            with pytest.raises(ValueError):
                operation()
    finally:
        other.close()
    team, team_folder = uid(), uid()
    stamp = '2026-11-09T00:00:00Z'
    store.db.execute('INSERT INTO library_spaces VALUES(?,?,?,?,?)', (team, 'team', uid(), '미래 팀', stamp))
    store.db.execute('INSERT INTO library_folders VALUES(?,?,?,?,?,?,?,?)',
                     (team_folder, team, None, '', '팀 폴더', '팀 폴더', stamp, stamp))
    store.db.execute('UPDATE library_asset_placements SET space_id=?,folder_id=? WHERE asset_id=?',
                     (team, team_folder, saved['id']))
    store.db.commit()
    assert [f['id'] for f in library.folders()] == [folder['id']]
    assert library.listing()['assets'] == []
    with pytest.raises(ValueError, match='폴더'):
        library.destination(team_folder)
    with pytest.raises(ValueError, match='권한'):
        library.move_asset(saved['id'])


def test_empty_folder_delete_respects_active_children_and_releases_tombstones(store):
    library = AssetLibrary(store)
    parent = library.create_folder('상위')
    folder = library.create_folder('하위', parent['id'])
    with pytest.raises(ValueError, match='하위 폴더'):
        library.delete_folder(parent['id'])
    saved = library.upload(asset(), [], folder['id'])
    with pytest.raises(ValueError, match='자료가'):
        library.delete_folder(folder['id'])
    immutable = dict(id=saved['id'], name=saved['name'], deleted=True, kind='table', columns=['amount'])
    store.db.execute('UPDATE builder_assets SET json=? WHERE id=?', (json.dumps(immutable), saved['id']))
    store.db.commit()
    assert library.delete_folder(folder['id']) == {'deleted': True}
    assert store.db.execute('SELECT json FROM builder_assets WHERE id=?', (saved['id'],)).fetchone()[0] == json.dumps(immutable)
    assert store.db.execute('SELECT count(*) FROM library_asset_placements').fetchone()[0] == 0
    library.delete_folder(parent['id'])


def test_pending_cleanup_does_not_permanently_hold_an_empty_folder(store):
    library = AssetLibrary(store)
    folder = library.create_folder('삭제 예정')
    saved = library.upload(asset(), [], folder['id'])
    uploads.prepare_delete(context(store, id=saved['id']))
    library.delete_folder(folder['id'])
    uploads.finish_delete(context(store, id=saved['id']))
    assert library.listing()['assets'] == [] and library.folders() == []


def test_search_is_folder_scoped_literal_and_pages_without_duplicates(store):
    library = AssetLibrary(store)
    folder = library.create_folder('선택')
    for index in range(55):
        library.upload(asset(name=f'File {index:02}.csv'), [])
    literal = library.upload(asset(name='100%_완료!.csv'), [], folder['id'])
    library.upload(asset(name='100XX완료.csv'), [], folder['id'])
    assert library.listing(q='완료')['assets'] == []
    assert [a['id'] for a in library.listing(folder['id'], q='%_완료!')['assets']] == [literal['id']]
    assert len(library.listing(q='FILE')['assets']) == 50
    first = library.listing()
    second = library.listing(before=int(first['next']))
    ids = [a['id'] for a in first['assets'] + second['assets']]
    assert len(first['assets']) == 50 and len(second['assets']) == 5
    assert len(set(ids)) == 55 and second['next'] is None


@pytest.mark.parametrize('data', [dict(before=0), dict(before=True), dict(before='1'),
                                dict(before=9007199254740992), dict(folder_id='bad'),
                                dict(q='x' * 201), dict(q='\0'), dict(q=1)])
def test_invalid_listing_parameters_are_rejected(store, data):
    with pytest.raises(ValueError):
        AssetLibrary(store).listing(**data)


@pytest.mark.parametrize('key', ['space_id', 'team_id', 'user_id', 'owner_id', 'workspace_id'])
def test_worker_rejects_client_selected_namespaces(store, key):
    with pytest.raises(ValueError, match='로그인'):
        handlers.listing(context(store, **{key: uid()}))


@pytest.mark.parametrize('key', ['space_id', 'team_id', 'user_id', 'owner_id', 'workspace_id', 'deleted', 'pending_delete'])
def test_upload_cannot_claim_ownership_or_deletion_state(store, key):
    with pytest.raises(ValueError):
        AssetLibrary(store).upload(asset(**{key: True}), [])


def test_worker_action_contracts_are_session_independent(store):
    from service.dispatch import handle
    def run(action, **data):
        return handle(dict(directory=str(Path(store.path).parent), action=action, **data))
    folder = run('folder_create', name='독립 업로드')['folder']
    destination = run('validate_destination', folder_id=folder['id'])
    assert destination['folder_id'] == folder['id'] and destination['space']['kind'] == 'personal'
    saved = run('library_upload', asset=asset(), rows=[{'amount': '12'}], folder_id=folder['id'])['asset']
    listing = run('library', folder_id=folder['id'], q='원본')
    assert set(listing) == {'space', 'folder_id', 'folders', 'assets', 'next'}
    assert listing['assets'][0]['id'] == saved['id'] and listing['folders'] == [folder]
    assert run('folder_update', id=folder['id'], name='변경')['folder']['name'] == '변경'
    assert run('asset_move', id=saved['id'], folder_id=None)['asset']['id'] == saved['id']
    assert run('folder_delete', id=folder['id']) == {'deleted': True}
    assert store.db.execute('SELECT count(*) FROM builder_sessions').fetchone()[0] == 0


def test_concurrent_duplicate_creation_has_one_winner(store):
    barrier = Barrier(2)
    def create(_):
        current = Storage(Path(store.path).parent)
        try:
            barrier.wait(timeout=5)
            try:
                return AssetLibrary(current).create_folder('동시 생성')['id']
            except ValueError:
                return None
        finally:
            current.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(AssetLibrary(store).folders()) == 1


def test_concurrent_opposite_moves_cannot_create_a_cycle(store):
    library = AssetLibrary(store)
    first, second = library.create_folder('첫째'), library.create_folder('둘째')
    barrier = Barrier(2)
    def move(pair):
        current = Storage(Path(store.path).parent)
        try:
            barrier.wait(timeout=5)
            try:
                AssetLibrary(current).update_folder(pair[0], parent_id=pair[1], move=True)
                return True
            except ValueError:
                return False
        finally:
            current.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(move, [(first['id'], second['id']), (second['id'], first['id'])]))
    assert sorted(results) == [False, True]
    assert sum(folder['parent_id'] is None for folder in library.folders()) == 1


def test_concurrent_upload_and_folder_delete_cannot_orphan_a_file(store):
    library = AssetLibrary(store)
    folder = library.create_folder('경합 폴더')
    barrier = Barrier(2)
    def mutate(action):
        current = Storage(Path(store.path).parent)
        try:
            barrier.wait(timeout=5)
            try:
                if action == 'delete':
                    return AssetLibrary(current).delete_folder(folder['id'])
                return AssetLibrary(current).upload(asset(), [], folder['id'])
            except ValueError:
                return None
        finally:
            current.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleted, uploaded = list(pool.map(mutate, ['delete', 'upload']))
    assert (deleted is None) != (uploaded is None)
    if deleted:
        assert library.folders() == []
        assert store.db.execute('SELECT count(*) FROM builder_assets').fetchone()[0] == 0
    else:
        assert [row['id'] for row in library.listing(folder['id'])['assets']] == [uploaded['id']]
    assert store.db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_sqlite_import_keeps_library_metadata_and_orders_moved_parents_first(store):
    library = AssetLibrary(store)
    older = library.create_folder('이전 폴더')
    newer = library.create_folder('새 폴더')
    library.update_folder(older['id'], parent_id=newer['id'], move=True)
    saved = library.upload(asset(), [], older['id'])
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location('asset_library_importer', root / 'scripts/deploy/migrate-sqlite-to-postgres.py')
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    rows, skipped = importer.source_rows(store.db.connection, 'library_folders')
    assert skipped == 0 and [row[0] for row in rows] == [newer['id'], older['id']]
    assert importer.source_rows(store.db.connection, 'library_spaces')[0][0][0] == library.space_id
    placements, skipped = importer.source_rows(store.db.connection, 'library_asset_placements')
    assert skipped == 0 and placements[0][:3] == (saved['id'], library.space_id, older['id'])
    tables = list(importer.TABLES)
    assert tables.index('builder_assets') < tables.index('library_spaces') < tables.index('library_folders') < tables.index('library_asset_placements')
