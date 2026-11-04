"""Member-facing records in the existing a4b schema, in the worker's transaction."""
import hashlib
import json
import mimetypes
import os
from uuid import UUID, uuid5


def document_json(value):
    # PostgreSQL jsonb cannot represent NUL. Keep the original value in the
    # builder's lossless runtime record and use a replacement character in the report.
    def clean(item):
        if isinstance(item, str):
            return item.replace('\0', '\ufffd')
        if isinstance(item, dict):
            return {clean(k): clean(v) for k, v in item.items()}
        if isinstance(item, list):
            return [clean(v) for v in item]
        return item
    return json.dumps(clean(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class BusinessRecords:
    def __init__(self, connection, member):
        self.connection = connection
        self.member = member

    def open_session(self, session):
        workflow = session['agent_id'] or session['id']
        if not session['agent_id']:
            self.connection.execute(
                "INSERT INTO a4b.workflows (id,created_by,name) VALUES (%s,%s,%s)",
                (workflow, self.member, '새 업무 설계'))
        created = self.connection.execute("""
            INSERT INTO a4b.design_sessions (id,workflow_id,created_by,base_version_id)
            SELECT %s,w.id,w.created_by,v.id FROM a4b.workflows w
            LEFT JOIN a4b.workflow_versions v ON v.workflow_id=w.id AND v.version_no=%s
            WHERE w.id=%s AND w.created_by=%s AND (%s=0 OR v.id IS NOT NULL)
            """, (session['id'], session['base_version'], workflow, self.member, session['base_version']))
        if created.rowcount != 1:
            raise ValueError('이 회원의 워크플로 버전을 찾을 수 없습니다.')

    def update_design(self, session_id, state):
        changed = self.connection.execute("""
            UPDATE a4b.design_sessions SET messages_json=%s, draft_graph_json=%s,
                status=%s, revision=revision+1 WHERE id=%s AND created_by=%s
            """, (document_json(state.get('messages', [])), document_json(state.get('draft') or {}),
                  'closed' if state.get('status') == 'saved' else 'open', session_id, self.member))
        if changed.rowcount != 1:
            raise ValueError('이 회원의 설계 대화를 찾을 수 없습니다.')

    def save_version(self, agent):
        config = agent['config']
        graph = document_json(config.get('workflow') or config)
        workflow = self.connection.execute("""
            UPDATE a4b.workflows SET name=%s, description=%s WHERE id=%s AND created_by=%s RETURNING id
            """, (config.get('name') or '저장한 업무', config.get('purpose', ''), agent['id'], self.member))
        if not workflow.fetchone():
            raise ValueError('이 회원의 워크플로를 찾을 수 없습니다.')
        self.connection.execute("""
            INSERT INTO a4b.workflow_versions
              (workflow_id,version_no,graph_json,graph_schema_version,graph_hash,created_by)
            VALUES (%s,%s,%s,'a4b.builder.v1',%s,%s)
            """, (agent['id'], agent['version'], graph, digest(graph), self.member))

    def save_document(self, asset):
        meta = {key: asset[key] for key in ('kind','format','hash','parser_status','processing_status') if key in asset}
        self.connection.execute("""
            INSERT INTO a4b.documents (id,created_by,name,source_metadata_json,created_at)
            VALUES (%s,%s,%s,%s,%s)
            """, (asset['id'], self.member, asset['name'], document_json(meta), asset['created_at']))
        bucket = os.environ.get('A4B_S3_BUCKET')
        if bucket:
            self.connection.execute("""
                INSERT INTO a4b.document_versions (document_id,document_type_code,version_no,s3_bucket,
                    s3_key,content_hash,mime_type,size_bytes) VALUES (%s,'other',1,%s,%s,%s,%s,%s)
                """, (asset['id'], bucket, f"app/v1/users/{self.member}/{asset['original_path']}", asset['hash'],
                      mimetypes.guess_type(asset['name'])[0] or 'application/octet-stream', asset['size_bytes']))

    def save_run(self, run):
        # A failed domain run is immutable. A retry starts a distinct physical
        # run and retains the original builder id for tracking across attempts.
        failures = sum(a.get('status') == 'failed' for a in run.get('attempts', [])[:-1])
        run_id = run['id'] if not failures else str(uuid5(UUID(run['id']), f'retry:{failures}'))
        request = document_json({'request': run.get('request', ''), 'file_ids': run.get('file_ids', [])})
        self.connection.execute("""
            INSERT INTO a4b.workflow_runs (id,workflow_version_id,requested_by,idempotency_key,
                request_hash,input_json,runtime_version,created_at)
            SELECT %s,v.id,%s,%s,%s,%s,'a4b.builder.v1',%s FROM a4b.workflow_versions v
            WHERE v.workflow_id=%s AND v.version_no=%s AND v.created_by=%s
            ON CONFLICT (id) DO NOTHING
            """, (run_id, self.member, run_id, digest(request), request, run['created_at'],
                  run['agent_id'], run['agent_version'], self.member))
        previous = self.connection.execute("""
            SELECT r.status FROM a4b.workflow_runs r JOIN a4b.workflow_versions v ON v.id=r.workflow_version_id
            WHERE r.id=%s AND r.requested_by=%s AND v.workflow_id=%s AND v.version_no=%s FOR UPDATE OF r
            """, (run_id, self.member, run['agent_id'], run['agent_version'])).fetchone()
        if not previous:
            raise ValueError('이 회원의 실행 또는 워크플로 버전을 찾을 수 없습니다.')
        status = {'needs_input': 'waiting_input'}.get(run['status'], run['status'])
        if previous[0] in ('succeeded', 'failed', 'canceled'):
            if previous[0] != status:
                raise ValueError('종료한 실행 상태는 수정할 수 없습니다.')
            return
        state = {k: run[k] for k in ('answer', 'error', 'error_type', 'steps', 'question') if k in run}
        state['builder_run_id'] = run['id']
        state['checkpoint_thread_id'] = 'run:' + run['id']
        finished = run.get('updated_at') if status in ('succeeded','failed','canceled') else None
        self.connection.execute("""
            UPDATE a4b.workflow_runs SET status=%s,state_json=%s,state_revision=state_revision+1,
                started_at=coalesce(started_at,%s),finished_at=%s WHERE id=%s AND requested_by=%s
            """, (status, document_json(state), run['created_at'], finished, run_id, self.member))
        self.save_steps(run_id, run)
        if status == 'succeeded' and (run.get('answer') or run.get('artifact') or run.get('rows')):
            result = document_json({k: run[k] for k in ('answer','rows','artifact') if run.get(k)})
            self.connection.execute("""
                INSERT INTO a4b.outputs (run_id,output_key,output_type,schema_key,content_json,content_hash,created_by)
                VALUES (%s,'result','report','a4b.builder.result.v1',%s,%s,%s)
                """, (run_id, result, digest(result), self.member))

    def save_steps(self, run_id, run):
        graph = (run.get('config_snapshot') or {}).get('workflow') or {}
        node_keys = {node['id'] for node in graph.get('nodes', [])}
        visits = {}
        for step in run.get('steps', []):
            key = step.get('node')
            if key not in node_keys or step.get('status') not in ('completed', 'failed'):
                continue
            visits[key] = visits.get(key, 0) + 1
            failed = step['status'] == 'failed'
            observed = step.get('finished_at') or run.get('updated_at') or run['created_at']
            self.connection.execute("""
                INSERT INTO a4b.node_runs (run_id,node_key,attempt_no,status,input_json,output_json,
                    error_json,started_at,finished_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id,node_key,attempt_no) DO NOTHING
                """, (run_id, key, visits[key], 'failed' if failed else 'succeeded',
                      document_json(step.get('input_summary') or {}), document_json(step.get('output_summary') or {}),
                      document_json({'detail': step.get('detail')}) if failed else None,
                      step.get('started_at') or observed, observed))
