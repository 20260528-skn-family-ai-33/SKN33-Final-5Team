"""Atomic library removal; retain immutable versions and execution history."""
import json
from infrastructure.persistence.ids import now


def remove(storage, kind, id, version):
    if kind not in ('agent', 'node') or type(version) is not int or version < 1:
        raise ValueError('삭제할 작업과 버전을 확인하세요.')
    if kind == 'agent':
        from workflow_engine.scheduling.schedules import Schedules
        Schedules(storage)  # Initialize schema before acquiring the write lock.
    else:
        from workflow_engine.library.node_library import init, default_nodes
        init(storage)
        if any(node['id'] == id for node in default_nodes()):
            raise ValueError('기본 작업은 삭제할 수 없습니다.')
    table, key = ('agent_versions', 'agent_id') if kind == 'agent' else ('reusable_nodes', 'id')
    storage.begin_write()
    try:
        row = storage.db.execute(f'SELECT version,json FROM {table} WHERE {{ws}} AND {key}=? ORDER BY version DESC LIMIT 1', (id,)).fetchone()
        if not row:
            raise ValueError('삭제할 작업을 찾을 수 없습니다.')
        value = json.loads(row[1])
        if value.get('deleted_at'):
            storage.db.commit()
            return {'deleted': id}
        if row[0] != version:
            raise ValueError('다른 곳에서 작업이 변경됐습니다. 목록을 새로고침하고 다시 시도하세요.')
        if kind == 'agent':
            if storage.db.execute('SELECT 1 FROM agent_tests WHERE {ws} AND agent_id=? AND '+storage.json_text('json','status')+" IN ('running','needs_input') LIMIT 1", (id,)).fetchone():
                raise ValueError('실행 중이거나 답변을 기다리는 작업은 완료한 뒤 삭제하세요.')
            if storage.db.execute("SELECT id FROM builder_schedule_jobs WHERE {ws} AND agent_id=? AND state='running' LIMIT 1", (id,)).fetchone():
                raise ValueError('예약 실행이 진행 중입니다. 완료한 뒤 삭제하세요.')
            marker = json.dumps({'agent_id': id, 'removed': True, 'removed_at': now()}, ensure_ascii=False)
            storage.db.execute('INSERT INTO builder_schedules({wsc}agent_id,json,next_at,active) VALUES({wsv}?,?,NULL,0) ON CONFLICT({wsc}agent_id) DO UPDATE SET json=excluded.json,next_at=NULL,active=0', (id, marker))
            for job_id, body in storage.db.execute("SELECT id,json FROM builder_schedule_jobs WHERE {ws} AND agent_id=? AND state='queued'", (id,)).fetchall():
                job = json.loads(body)
                job.update(error='워크플로우가 삭제되어 예약 실행을 취소했습니다.', finished_at=now())
                storage.db.execute("UPDATE builder_schedule_jobs SET state='cancelled',lease_until=NULL,lease_token=NULL,json=? WHERE {ws} AND id=?", (json.dumps(job, ensure_ascii=False), job_id))
                storage.db.execute("UPDATE builder_source_processed SET state='failed',updated_at=? WHERE {ws} AND job_id=? AND state='pending'", (now(), job_id))
            for source in ('builder_data_sources', 'builder_library_sources'):
                storage.db.execute(f'DELETE FROM {source} WHERE {{ws}} AND agent_id=?', (id,))
            from infrastructure.persistence.workflow_folders import organization
            folders = organization(storage)
            if id in folders['assignments']:
                folders['assignments'].pop(id)
                revision = folders.pop('revision') + 1
                storage.db.execute('UPDATE workflow_organization SET revision=?,json=? WHERE {ws} AND id=?', (revision, json.dumps(folders, ensure_ascii=False), 'folders'))
        # A tombstone version prevents stale editors from restoring the removed entry.
        value.update(version=version + 1, deleted_at=now())
        storage.db.execute(f'INSERT INTO {table}({{wsc}}{key},version,json) VALUES({{wsv}}?,?,?)', (id, version + 1, json.dumps(value, ensure_ascii=False)))
        if kind == 'agent' and storage.business:
            storage.business.archive_workflow(id)
        result = {'deleted': id}
        if kind == 'agent':result['organization'] = organization(storage)
        storage.db.commit()
        return result
    except Exception:
        storage.db.rollback()
        raise
