"""One-off copy of SQLite workspaces into the PostgreSQL business-data schema.

Walks <data>/users/<member ID>/ and <data>/teams/<team ID>/ and copies each folder's agent-builder.sqlite (business
tables and Python LangGraph checkpoints) and agent-checkpoints.sqlite (Node assistant
checkpoints) with the member ID or team:<team ID> as workspace_id. An existing business row or Node
checkpoint must match its source. Stop the app first so no worker is writing.

  Backend/.venv-agent/bin/python scripts/deploy/migrate-sqlite-to-postgres.py --dry-run
  Backend/.venv-agent/bin/python scripts/deploy/migrate-sqlite-to-postgres.py

--dry-run only reads the SQLite files and prints row counts; it never connects to
PostgreSQL. A real run uses the app settings (A4B_DATABASE_URL, A4B_DATABASE_CA_FILE,
A4B_DATABASE_TLS_SERVER_NAME) from the environment or the repository-root .env, after
scripts/deploy/apply-builder-migrations.py has created the schema. The SQLite files are not changed.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend'))
WORKSPACE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)

# Parents before children (foreign keys). Columns are the SQLite columns, minus rowid.
TABLES = {
    'registered_tools': ['id', 'version', 'module_path', 'schema_json'],
    'builder_sessions': ['id', 'json', 'revision'],
    'builder_assets': ['id', 'json', 'rows_json'],
    'library_spaces': ['id', 'kind', 'owner_id', 'name', 'created_at'],
    'library_folders': ['id', 'space_id', 'parent_id', 'parent_key', 'name', 'name_key', 'created_at', 'updated_at'],
    'library_asset_placements': ['asset_id', 'space_id', 'folder_id', 'updated_at'],
    'agent_versions': ['agent_id', 'version', 'json'],
    'workflow_organization': ['id', 'revision', 'json'],
    'agent_tests': ['id', 'agent_id', 'json'],
    'builder_generation_requests': ['id', 'session_id', 'json'],
    'builder_run_leases': ['run_id', 'token', 'expires_at', 'pid', 'active'],
    'builder_data_sources': ['agent_id', 'id', 'asset_id', 'version', 'updated_at'],
    'builder_schedules': ['agent_id', 'json', 'next_at', 'active'],
    'builder_schedule_jobs': ['id', 'agent_id', 'slot_key', 'state', 'lease_until', 'lease_token', 'json'],
    'reply_review_decisions': ['run_id', 'review_id', 'json'],
    'document_reviews': ['asset_id', 'revision', 'json'],
    'document_review_events': ['asset_id', 'revision', 'request_id', 'json'],
    'builder_tool_gap_jobs': ['id', 'session_id', 'decision_id', 'input_sha256', 'json'],
    'reusable_nodes': ['id', 'version', 'json'],
    'builder_verifications': ['id', 'session_id', 'input_json', 'result_json'],
    'preprocessing_batches': ['id', 'source_path', 'contract_version', 'provenance', 'human_review', 'summary_json'],
    'preprocessing_examples': ['batch_id', 'example_id', 'scenario_group', 'split', 'content_hash', 'raw_json', 'clean_json',
                               'target_json', 'adapted_json', 'compatibility', 'issues_json'],
}
# child table: (child column, parent table, parent column)
PARENTS = {
    'reply_review_decisions': ('run_id', 'agent_tests', 'id'),
    'document_reviews': ('asset_id', 'builder_assets', 'id'),
    'document_review_events': ('asset_id', 'builder_assets', 'id'),
    'library_asset_placements': ('asset_id', 'builder_assets', 'id'),
    'preprocessing_examples': ('batch_id', 'preprocessing_batches', 'id'),
}


def load_env():
    path = ROOT / os.environ.get('A4B_ENV_FILE', '.env')
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.split(' #', 1)[0].strip().strip('"\''))


def workspaces(data_dir, include_local=False):
    def has_database(folder):
        return any((folder / name).is_file() for name in ('agent-builder.sqlite', 'agent-checkpoints.sqlite'))

    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise ValueError(f'데이터 볼륨을 찾을 수 없습니다: {data_dir}')
    central = data_dir / 'teams.sqlite'
    if central.exists():
        # Central memberships use actor-scoped RLS rather than workspace RLS.
        # Do not report a successful cutover that leaves copied team files orphaned.
        try:
            connection = read_only(central)
            try:
                for table in ('teams', 'team_members', 'team_invitations'):
                    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() and connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                        raise ValueError('중앙 팀 저장소 teams.sqlite에 팀 정보가 있어 이전을 중단합니다. 원본 teams.sqlite와 teams 폴더를 보존하고 중앙 팀 정보 이전을 먼저 준비하세요. 팀 자료는 아직 복사하지 않았습니다.')
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise ValueError('중앙 팀 저장소 teams.sqlite를 확인할 수 없어 이전을 중단합니다. 원본 파일을 보존하고 저장소 상태를 확인하세요.') from error
    found = []
    users = data_dir / 'users'
    if users.is_dir():
        for folder in sorted(users.iterdir()):
            if folder.is_dir() and has_database(folder):
                if not WORKSPACE.fullmatch(folder.name):
                    raise ValueError(f'업무 DB가 있는 폴더의 회원 ID를 확인하세요: {folder.name}')
                found.append((folder.name.lower(), folder))
    teams = data_dir / 'teams'
    if teams.is_dir():
        for folder in sorted(teams.iterdir()):
            if folder.is_dir() and has_database(folder):
                if not WORKSPACE.fullmatch(folder.name):
                    raise ValueError(f'업무 DB가 있는 폴더의 팀 ID를 확인하세요: {folder.name}')
                found.append(('team:' + folder.name.lower(), folder))
    if has_database(data_dir):
        if not include_local:
            raise ValueError('루트 작업 공간의 SQLite 자료도 이전하려면 --include-local을 지정하세요.')
        found.append(('local', data_dir))
    if len({workspace for workspace, _ in found}) != len(found):
        raise ValueError('같은 회원 ID를 가리키는 SQLite 폴더가 여러 개입니다.')
    return found


def read_only(path):
    return sqlite3.connect(f'file:{Path(path).as_posix()}?mode=ro', uri=True)


def source_rows(connection, table):
    """Rows in SQLite insertion order, with orphaned children separated out."""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return [], 0
    columns = TABLES[table]
    rows = connection.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY rowid').fetchall()
    if table == 'library_folders':
        # A folder can be moved below a newer folder, so insertion order no longer
        # guarantees that its parent has been imported before the immediate FK check.
        pending = rows
        rows, imported = [], set()
        while pending:
            ready = [row for row in pending if row[2] is None or (row[1], row[2]) in imported]
            if not ready:
                raise ValueError('자료함 폴더의 부모 연결을 확인하세요. 폴더를 누락한 채 이전할 수 없습니다.')
            rows.extend(ready)
            imported.update((row[1], row[0]) for row in ready)
            pending = [row for row in pending if (row[1], row[0]) not in imported]
    if table not in PARENTS:
        return rows, 0
    column, parent, key = PARENTS[table]
    known = {row[0] for row in connection.execute(f'SELECT {key} FROM {parent}')} if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (parent,)).fetchone() else set()
    index = columns.index(column)
    kept = [row for row in rows if row[index] in known]
    return kept, len(rows) - len(kept)


def python_checkpoints(path):
    """CheckpointTuples from SqliteSaver, oldest first."""
    from langgraph.checkpoint.sqlite import SqliteSaver
    connection = read_only(path)
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone():
            return []
        return list(reversed(list(SqliteSaver(connection).list(None))))
    finally:
        connection.close()


def node_checkpoints(path):
    if not path.exists():
        return []
    connection = read_only(path)
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_checkpoints'").fetchone():
            return []
        return connection.execute('SELECT thread_id,payload FROM agent_checkpoints ORDER BY rowid').fetchall()
    finally:
        connection.close()


def survey(folder):
    """Counts per table in one workspace folder, without touching PostgreSQL."""
    counts, orphans = {}, {}
    business = folder / 'agent-builder.sqlite'
    if business.exists():
        connection = read_only(business)
        try:
            for table in TABLES:
                rows, skipped = source_rows(connection, table)
                counts[table] = len(rows)
                if skipped:
                    orphans[table] = skipped
        finally:
            connection.close()
        counts['checkpoints (Python LangGraph)'] = len(python_checkpoints(business))
    counts['agent_checkpoints (Node)'] = len(node_checkpoints(folder / 'agent-checkpoints.sqlite'))
    return counts, orphans


def copy_rows(target, workspace, table, columns, rows):
    destination = 'a4b.workflow_generation_requests' if table=='builder_generation_requests' else f'a4b_builder.{table}'
    fields = ['workspace_id', *columns]
    sql = (f'INSERT INTO {destination}({",".join(fields)}) '
           f'VALUES({",".join(["%s"] * len(fields))}) ON CONFLICT DO NOTHING')
    # PostgreSQL json preserves the original text but has no equality operator.
    comparisons = [field + ('::text' if field.endswith('json') else '') for field in fields]
    inserted = 0
    for row in rows:
        values = (workspace, *row)
        if target.execute(sql, values).rowcount:
            inserted += 1
            continue
        conditions = [f'{field} IS NULL' if value is None else f'{field}=%s' for field, value in zip(comparisons, values)]
        existing = target.execute(f'SELECT 1 FROM {destination} WHERE ' + ' AND '.join(conditions),
                                  [value for value in values if value is not None]).fetchone()
        if not existing:
            raise ValueError(f'기존 PostgreSQL 행이 SQLite 원본과 다릅니다: {workspace}/{table}')
    return inserted


def copy_workspace(conninfo, workspace, folder):
    """Copies one workspace in one transaction (plus idempotent checkpoint writes). Returns inserted counts."""
    import psycopg
    inserted = defaultdict(int)
    business = folder / 'agent-builder.sqlite'
    with psycopg.connect(conninfo) as target:
        with target.transaction():
            # Row-level security: this connection may only write the folder's own workspace.
            target.execute("SELECT set_config('a4b.workspace_id',%s,true)", (workspace,))
            if business.exists():
                source = read_only(business)
                try:
                    for table, columns in TABLES.items():
                        rows, _ = source_rows(source, table)
                        inserted[table] = copy_rows(target, workspace, table, columns, rows)
                finally:
                    source.close()
            inserted['agent_checkpoints (Node)'] = copy_rows(target, workspace, 'agent_checkpoints', ['thread_id', 'payload'],
                                                             node_checkpoints(folder / 'agent-checkpoints.sqlite'))
    if business.exists():
        inserted['checkpoints (Python LangGraph)'] = copy_python_checkpoints(conninfo, workspace, business)
    return dict(inserted)


def copy_python_checkpoints(conninfo, workspace, business):
    """Replays SqliteSaver checkpoints through PostgresSaver so its own format and blobs are used."""
    import psycopg
    from psycopg.rows import dict_row
    from infrastructure.persistence.checkpoints import postgres_saver
    tuples = python_checkpoints(business)
    if not tuples:
        return 0
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('a4b.workspace_id',%s,false)", (workspace,))
        existing = {(r['thread_id'], r['checkpoint_ns'], r['checkpoint_id']) for r in
                    connection.execute('SELECT thread_id,checkpoint_ns,checkpoint_id FROM a4b_builder.checkpoints')}
        saver = postgres_saver(connection)
        count = 0
        for item in tuples:
            conf = item.config['configurable']
            key = (conf['thread_id'], conf.get('checkpoint_ns', ''), conf['checkpoint_id'])
            parent = item.parent_config or {'configurable': {'thread_id': conf['thread_id'], 'checkpoint_ns': conf.get('checkpoint_ns', '')}}
            writes = defaultdict(list)
            for task_id, channel, value in item.pending_writes or []:
                writes[task_id].append((channel, value))
            # Existing checkpoints are rewritten too: put/put_writes are upserts, so a rerun
            # after an interrupted copy restores pending writes (e.g. __interrupt__) it missed.
            # One transaction per checkpoint keeps a checkpoint and its writes together.
            with connection.transaction():
                saved = saver.put(parent, item.checkpoint, item.metadata, dict(item.checkpoint.get('channel_versions', {})))
                for task_id, values in writes.items():
                    saver.put_writes(saved, values, task_id)
            count += key not in existing
        return count


def import_volume(conninfo, data_dir, include_local=False, confirm_empty=False):
    import psycopg
    receipt = data_dir / '.a4b-sqlite-import'
    receipt.unlink(missing_ok=True)
    receipt_id = str(uuid.uuid4())
    temporary = receipt.with_suffix('.' + receipt_id + '.tmp')
    try:
        # mtime is the start of the source scan, on the same host clock as Docker's FinishedAt.
        temporary.write_text(receipt_id + '\n', encoding='ascii')
        found = workspaces(data_dir, include_local)
        if not found and not confirm_empty:
            raise ValueError('이전할 SQLite DB가 없습니다. 빈 볼륨이 맞으면 --confirm-empty를 지정하세요.')
        if found and confirm_empty:
            raise ValueError('SQLite 자료가 있어 --confirm-empty를 사용할 수 없습니다.')
        for workspace, folder in found:
            print(json.dumps({'workspace_id': workspace, 'inserted': copy_workspace(conninfo, workspace, folder)}, ensure_ascii=False))
        with psycopg.connect(conninfo) as connection:
            connection.execute('INSERT INTO a4b_builder.sqlite_imports(id,workspace_count) VALUES(%s,%s)', (receipt_id, len(found)))
        # Publish only after every workspace and the database receipt are committed.
        temporary.replace(receipt)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='Copy SQLite workspaces into PostgreSQL (a4b_builder).')
    parser.add_argument('--data-dir', default=None, help='EASYFLOW_DATA_DIR (default: env or ./data)')
    parser.add_argument('--dry-run', action='store_true', help='print SQLite row counts only; no PostgreSQL connection')
    parser.add_argument('--include-local', action='store_true', help="also copy the root folder (login-free 'local' workspace)")
    parser.add_argument('--confirm-empty', action='store_true', help='initialize this mounted volume only if it contains no legacy databases')
    args = parser.parse_args()
    load_env()
    data_dir = Path(args.data_dir or os.environ.get('EASYFLOW_DATA_DIR') or ROOT / 'data')
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    if args.dry_run:
        found = workspaces(data_dir, args.include_local)
        totals = defaultdict(int)
        for workspace, folder in found:
            counts, orphans = survey(folder)
            print(json.dumps({'workspace_id': workspace, 'rows': counts, 'skipped_orphans': orphans}, ensure_ascii=False))
            for table, count in counts.items():
                totals[table] += count
        print(json.dumps({'workspaces': len(found), 'total_rows': dict(totals), 'dry_run': True}, ensure_ascii=False))
        return
    from psycopg.conninfo import make_conninfo
    from infrastructure.persistence.db import postgres_conninfo
    params = postgres_conninfo()
    params.pop('options', None)  # The SQL below names the a4b_builder schema; PostgresSaver gets it via search_path.
    conninfo = make_conninfo(**{**params, 'application_name': 'a4b-builder-sqlite-import', 'options': '-c search_path=a4b_builder'})
    import_volume(conninfo, data_dir, args.include_local, args.confirm_empty)


if __name__ == '__main__':
    main()
