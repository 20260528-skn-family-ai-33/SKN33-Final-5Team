"""Team directories use a separate namespace selected by the authorized server."""
import importlib.util
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest

from infrastructure.persistence import db as database
from infrastructure.persistence import storage as storage_module
from infrastructure.persistence.asset_library import AssetLibrary
from infrastructure.persistence.ids import uid
from infrastructure.persistence.storage import Storage
from service.handlers import asset_library as handlers


@pytest.fixture(autouse=True)
def local_storage(monkeypatch):
    monkeypatch.setenv('A4B_STORAGE', 'sqlite')
    monkeypatch.setenv('EASYFLOW_PUBLIC_URL', 'http://localhost')
    monkeypatch.delenv('A4B_BUSINESS_RECORDS', raising=False)


def upload(name='팀 파일.csv'):
    return dict(name=name, kind='table', format='csv', original_path='builder-originals/team.csv')


def load_importer():
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location('team_library_importer', root / 'scripts/deploy/migrate-sqlite-to-postgres.py')
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    return importer


def test_member_and_team_with_the_same_uuid_have_distinct_stable_namespaces(tmp_path):
    identifier = uid()
    personal = Storage(tmp_path / 'users' / identifier)
    team = Storage(tmp_path / 'teams' / identifier)
    other = Storage(tmp_path / 'teams' / uid())
    try:
        private, shared = AssetLibrary(personal), AssetLibrary(team)
        assert personal.workspace_id == identifier and team.workspace_id == 'team:' + identifier
        assert private.space_id == str(uuid5(NAMESPACE_URL, 'a4b.library.personal:' + identifier))
        assert private.space_id != shared.space_id
        assert private.space == dict(id=private.space_id, kind='personal', name='내 자료')
        assert shared.space == dict(id=shared.space_id, kind='team', name='팀 자료')
        folder = shared.create_folder('공유 폴더')
        saved = shared.upload(upload(), [{'amount': 12}], folder['id'])
        assert team.db.execute('SELECT kind,owner_id,name FROM library_spaces').fetchone() == ('team', identifier, '팀 자료')
        assert AssetLibrary(personal).listing()['assets'] == []
        assert AssetLibrary(other).listing()['assets'] == []
        assert AssetLibrary(other).listing()['folders'] == []
        for outsider in (private, AssetLibrary(other)):
            with pytest.raises(ValueError):
                outsider.listing(folder['id'])
            with pytest.raises(ValueError):
                outsider.move_asset(saved['id'])
        second_connection = Storage(tmp_path / 'teams' / identifier)
        try:
            again = AssetLibrary(second_connection)
            assert again.space == shared.space
            assert again.listing(folder['id'])['assets'][0]['id'] == saved['id']
            assert second_connection.rows(saved['id']) == [{'amount': 12}]
        finally:
            second_connection.close()
    finally:
        personal.close()
        team.close()
        other.close()


@pytest.mark.parametrize('backend', ['sqlite', 'postgres'])
def test_team_directory_uuid_is_normalized_without_changing_personal_or_local(tmp_path, monkeypatch, backend):
    monkeypatch.setenv('A4B_STORAGE', backend)
    identifier = uid()
    assert database.workspace_id(tmp_path / 'teams' / identifier.upper()) == 'team:' + identifier
    assert database.workspace_id(tmp_path / 'users' / identifier.upper()) == identifier
    assert database.workspace_id(tmp_path) == 'local'
    query, slots = database.postgres_sql('SELECT id FROM library_folders WHERE {ws} AND id=?')
    assert query == 'SELECT id FROM library_folders WHERE workspace_id=%s AND id=%s'
    assert database.bind(slots, ['folder'], 'team:' + identifier) == ['team:' + identifier, 'folder']


@pytest.mark.parametrize('suffix', ['not-a-uuid', 'team:00000000-0000-0000-0000-000000000001',
                                    '00000000-0000-0000-0000-000000000001/nested', ''])
@pytest.mark.parametrize('backend', ['sqlite', 'postgres'])
def test_malformed_team_directories_fail_before_creating_storage(tmp_path, monkeypatch, suffix, backend):
    monkeypatch.setenv('A4B_STORAGE', backend)
    directory = tmp_path / 'teams' / suffix
    with pytest.raises(ValueError, match='팀 작업 공간'):
        Storage(directory)
    assert not directory.exists()


def test_legacy_local_and_malformed_sqlite_member_namespace_is_preserved(tmp_path):
    assert database.workspace_id(tmp_path / 'users' / 'old-local-fixture') == 'local'
    assert AssetLibrary(SimpleNamespace(workspace_id='local', db=None)).space_id == str(
        uuid5(NAMESPACE_URL, 'a4b.library.personal:local'))


def test_team_worker_actions_have_the_same_folder_and_upload_contracts(tmp_path):
    from service.dispatch import handle
    directory = tmp_path / 'teams' / uid()
    def run(action, **data):
        return handle(dict(directory=str(directory), action=action, **data))
    listing = run('library')
    assert listing['space']['kind'] == 'team' and listing['space']['name'] == '팀 자료'
    folder = run('folder_create', name='팀 폴더')['folder']
    saved = run('library_upload', asset=upload(), rows=[{'amount': 12}], folder_id=folder['id'])['asset']
    assert run('validate_destination', folder_id=folder['id'])['space'] == listing['space']
    assert run('library', folder_id=folder['id'])['assets'][0]['id'] == saved['id']
    assert run('assets')['assets'][0]['id'] == saved['id']
    assert run('folder_update', id=folder['id'], name='팀 이름 변경')['folder']['name'] == '팀 이름 변경'
    assert run('asset_move', id=saved['id'], folder_id=None)['asset']['folder_id'] is None
    assert run('folder_delete', id=folder['id']) == {'deleted': True}


@pytest.mark.parametrize('key', ['space_id', 'team_id', 'user_id', 'owner_id', 'workspace_id'])
def test_team_worker_also_rejects_client_selected_ownership(tmp_path, key):
    store = Storage(tmp_path / 'teams' / uid())
    try:
        with pytest.raises(ValueError, match='서버'):
            handlers.listing(SimpleNamespace(storage=store, data={key: uid()}))
    finally:
        store.close()


@pytest.mark.parametrize('kind, mirrored', [('users', True), ('teams', False)])
def test_postgres_business_records_are_only_created_for_personal_members(tmp_path, monkeypatch, kind, mirrored):
    from infrastructure.persistence import business_records
    directory = tmp_path / kind / uid()
    Storage(directory).close()
    connection = database.SqliteDatabase(directory / 'agent-builder.sqlite')
    connection.dialect = 'postgres'
    calls, marker = [], object()
    def mirror(db_connection, member):
        calls.append(member)
        return marker
    monkeypatch.setattr(storage_module, 'open_database', lambda path, workspace: connection)
    monkeypatch.setattr(business_records, 'BusinessRecords', mirror)
    monkeypatch.setenv('A4B_BUSINESS_RECORDS', 'a4b')
    store = Storage(directory)
    try:
        assert (store.business is marker) == mirrored
        assert calls == ([directory.name] if mirrored else [])
    finally:
        store.close()


def test_sqlite_import_discovers_teams_without_colliding_with_member_ids(tmp_path):
    identifier = uid()
    member, team = tmp_path / 'users' / identifier, tmp_path / 'teams' / identifier
    for directory in (member, team):
        current = Storage(directory)
        AssetLibrary(current).upload(upload(), [])
        current.close()
    importer = load_importer()
    assert importer.workspaces(tmp_path) == [(identifier, member), ('team:' + identifier, team)]
    invalid = tmp_path / 'teams' / 'invalid'
    invalid.mkdir()
    (invalid / 'agent-builder.sqlite').touch()
    with pytest.raises(ValueError, match='팀 ID'):
        importer.workspaces(tmp_path)


@pytest.mark.parametrize('table', ['teams', 'team_members', 'team_invitations'])
def test_sqlite_cutover_refuses_to_drop_central_team_metadata(tmp_path, table):
    importer = load_importer()
    with sqlite3.connect(tmp_path / 'teams.sqlite') as central:
        central.execute(f'CREATE TABLE {table}(id TEXT)')
        central.commit()
        assert importer.workspaces(tmp_path) == []  # Authentication also creates an empty team store.
        central.execute(f'INSERT INTO {table} VALUES(?)', (uid(),))
        central.commit()
        with pytest.raises(ValueError, match='teams.sqlite.*보존'):
            importer.workspaces(tmp_path)


def test_sqlite_cutover_fails_closed_when_the_central_team_store_is_corrupt(tmp_path):
    (tmp_path / 'teams.sqlite').write_bytes(b'not a sqlite database')
    with pytest.raises(ValueError, match='teams.sqlite.*확인'):
        load_importer().workspaces(tmp_path)
