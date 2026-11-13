"""A production release upgrades an existing schema without initializing a wrong DB."""
import importlib.util
import sys
from types import ModuleType
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / 'scripts' / 'deploy' / 'apply-builder-migrations.py'


@pytest.fixture
def command(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('apply_builder_migrations_for_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    monkeypatch.setenv('A4B_MIGRATION_DATABASE_URL', 'postgresql://admin:private@localhost/test')
    fake_psycopg = ModuleType('psycopg')
    fake_conninfo = ModuleType('psycopg.conninfo')
    fake_conninfo.make_conninfo = lambda **kwargs: 'fixture-connection'
    fake_psycopg.conninfo = fake_conninfo
    monkeypatch.setitem(sys.modules, 'psycopg', fake_psycopg)
    monkeypatch.setitem(sys.modules, 'psycopg.conninfo', fake_conninfo)
    from infrastructure.persistence import db
    monkeypatch.setattr(db, 'postgres_conninfo', lambda env: {'host': 'localhost', 'dbname': 'test', 'user': 'admin'})
    return module


def test_existing_only_refuses_an_uninitialized_database(command, monkeypatch):
    from infrastructure.persistence import migrations
    all_pending = [(version, name) for version, name, _ in migrations.MIGRATIONS]
    monkeypatch.setattr(migrations, 'pending', lambda _: all_pending)
    monkeypatch.setattr(migrations, 'apply', lambda _: pytest.fail('empty DB must not be initialized by a release'))
    monkeypatch.setattr(sys, 'argv', ['apply-builder-migrations.py', '--existing-only'])
    with pytest.raises(SystemExit, match='빈 업무 데이터 DB'):
        command.main()


def test_existing_only_applies_only_pending_upgrade(command, monkeypatch, capsys):
    from infrastructure.persistence import migrations
    name = migrations.MIGRATIONS[-1][1]
    calls = []
    monkeypatch.setattr(migrations, 'pending', lambda _: [(migrations.MIGRATIONS[-1][0], name)])
    monkeypatch.setattr(migrations, 'apply', lambda _: calls.append(name) or [name])
    monkeypatch.setattr(sys, 'argv', ['apply-builder-migrations.py', '--existing-only'])
    command.main()
    assert calls == [name]
    assert name in capsys.readouterr().out


def test_dry_run_lists_pending_without_apply(command, monkeypatch, capsys):
    from infrastructure.persistence import migrations
    name = migrations.MIGRATIONS[-1][1]
    monkeypatch.setattr(migrations, 'pending', lambda _: [(migrations.MIGRATIONS[-1][0], name)])
    monkeypatch.setattr(migrations, 'apply', lambda _: pytest.fail('dry run must not apply SQL'))
    monkeypatch.setattr(sys, 'argv', ['apply-builder-migrations.py', '--dry-run'])
    command.main()
    output = capsys.readouterr().out
    assert name in output and 'private' not in output


def test_existing_only_does_not_reapply_a_current_schema(command, monkeypatch, capsys):
    from infrastructure.persistence import migrations
    monkeypatch.setattr(migrations, 'pending', lambda _: [])
    monkeypatch.setattr(migrations, 'apply', lambda _: pytest.fail('current schema needs no SQL'))
    monkeypatch.setattr(sys, 'argv', ['apply-builder-migrations.py', '--existing-only'])
    command.main()
    assert '이미 최신' in capsys.readouterr().out
