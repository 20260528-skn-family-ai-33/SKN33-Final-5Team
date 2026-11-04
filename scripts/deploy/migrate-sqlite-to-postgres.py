"""One-off copy of SQLite workspaces into the PostgreSQL business-data schema.

Walks <data>/users/<member ID>/ and copies each folder's agent-builder.sqlite (business
tables and Python LangGraph checkpoints) and agent-checkpoints.sqlite (Node assistant
checkpoints) with the folder name as workspace_id. Rows that already exist are skipped,
so running it twice inserts nothing new. Stop the app first so no worker is writing.

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
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend'))
WORKSPACE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

# Parents before children (foreign keys). Columns are the SQLite columns, minus rowid.
TABLES = {
    'registered_tools': ['id', 'version', 'module_path', 'schema_json'],
    'builder_sessions': ['id', 'json', 'revision'],
    'builder_assets': ['id', 'json', 'rows_json'],
    'agent_versions': ['agent_id', 'version', 'json'],
    'agent_tests': ['id', 'agent_id', 'json'],
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
    found = []
    users = Path(data_dir) / 'users'
    if users.is_dir():
        for folder in sorted(users.iterdir()):
            if folder.is_dir() and WORKSPACE.fullmatch(folder.name):
                found.append((folder.name, folder))
            elif folder.is_dir():
                print(f'건너뜀: 회원 ID 형식이 아닌 폴더 {folder.name}', file=sys.stderr)
    if include_local:
        found.append(('local', Path(data_dir)))
    return found


def read_only(path):
    return sqlite3.connect(f'file:{Path(path).as_posix()}?mode=ro', uri=True)


def source_rows(connection, table):
    """Rows in SQLite insertion order, with orphaned children separated out."""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return [], 0
    columns = TABLES[table]
    rows = connection.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY rowid').fetchall()
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
                        sql = (f'INSERT INTO a4b_builder.{table}(workspace_id,{",".join(columns)}) '
                               f'VALUES(%s{",%s" * len(columns)}) ON CONFLICT DO NOTHING')
                        for row in rows:
                            inserted[table] += target.execute(sql, (workspace, *row)).rowcount
                finally:
                    source.close()
            for thread_id, payload in node_checkpoints(folder / 'agent-checkpoints.sqlite'):
                inserted['agent_checkpoints (Node)'] += target.execute(
                    'INSERT INTO a4b_builder.agent_checkpoints(workspace_id,thread_id,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                    (workspace, thread_id, bytes(payload))).rowcount
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


def main():
    parser = argparse.ArgumentParser(description='Copy SQLite workspaces into PostgreSQL (a4b_builder).')
    parser.add_argument('--data-dir', default=None, help='EASYFLOW_DATA_DIR (default: env or ./data)')
    parser.add_argument('--dry-run', action='store_true', help='print SQLite row counts only; no PostgreSQL connection')
    parser.add_argument('--include-local', action='store_true', help="also copy the root folder (login-free 'local' workspace)")
    args = parser.parse_args()
    load_env()
    data_dir = Path(args.data_dir or os.environ.get('EASYFLOW_DATA_DIR') or ROOT / 'data')
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    found = workspaces(data_dir, args.include_local)
    if not found:
        sys.exit(f'옮길 작업 공간이 없습니다: {data_dir}')
    if args.dry_run:
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
    for workspace, folder in found:
        print(json.dumps({'workspace_id': workspace, 'inserted': copy_workspace(conninfo, workspace, folder)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
