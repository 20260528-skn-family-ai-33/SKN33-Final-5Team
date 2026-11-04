"""The upload library is scoped to the current builder workspace."""
import json
from documents.resources.document_resources import supports


def asset_usages(store, ids):
    """Resolve one page of uploads without returning workflow or execution payloads."""
    usages = {id: [] for id in ids}
    if not usages:
        return usages
    values = ','.join('(?)' for _ in ids)
    # Immutable snapshots can also refer to an input in nested settings, dependencies or evidence.
    quoted_id = """'%"' || wanted.id || '"%'"""
    run_kind = f"CASE WHEN {store.json_text('r.json', 'status')}='running' THEN 'running' ELSE 'run' END"
    refs = [
        f"""SELECT wanted.id, {kind}, r.agent_id, r.agent_id, NULL
            FROM wanted JOIN {table} r ON CAST(r.json AS TEXT) LIKE {quoted_id} WHERE {{ws:r}}"""
        for kind, table in [("'workflow'", 'agent_versions'), (run_kind, 'agent_tests')]
    ]
    if store.db.table_exists('builder_data_sources'):
        refs.append("""SELECT wanted.id, 'schedule', r.agent_id, r.agent_id, NULL
                       FROM wanted JOIN builder_data_sources r ON r.asset_id=wanted.id WHERE {ws:r}""")
    refs.append(f"""SELECT wanted.id, 'draft', s.id, {store.json_text('s.json', 'agent_id')},
                           {store.json_text('s.json', 'draft_name')}
                    FROM wanted JOIN builder_sessions s ON {store.json_text('s.json', 'file_ids')} LIKE {quoted_id}
                    WHERE {{ws:s}} AND NOT EXISTS (
                        SELECT 1 FROM agent_versions v WHERE {{ws:v}} AND {store.json_text('v.json', 'session_id')}=s.id)""")
    workflow_name = store.json_text(store.json_text('v.json', 'config'), 'name')
    query = f"""WITH wanted(id) AS (VALUES {values}),
                     refs(asset_id,kind,ref_id,agent_id,title) AS ({' UNION '.join(refs)})
                SELECT refs.asset_id,refs.kind,refs.ref_id,COALESCE(NULLIF(refs.title,''),{workflow_name})
                FROM refs LEFT JOIN agent_versions v ON {{ws:v}} AND v.agent_id=refs.agent_id
                    AND v.version=(SELECT MAX(version) FROM agent_versions WHERE {{ws}} AND agent_id=refs.agent_id)
                ORDER BY refs.asset_id,refs.kind,refs.ref_id"""
    for id, kind, ref_id, name in store.db.execute(query, tuple(ids)):
        usages[id].append({'kind': kind, 'id': ref_id, 'name': name or ('이름 없는 설계' if kind == 'draft' else '이름 없는 워크플로우')})
    return usages


def assets(c):
    store = c.storage
    fields = ('name', 'format', 'created_at', 'size_bytes', 'original_path', 'pending_delete')
    columns = ','.join(store.json_text('json', field) for field in fields)
    before = c.data.get('before')
    bound = ' AND {seq} < ?' if before is not None else ''
    rows = store.db.execute(
        'SELECT {seq},id,' + columns + ' FROM builder_assets WHERE {ws} AND (' +
        store.json_text('json','deleted') + ' IS NULL OR ' + store.json_text('json','pending_delete') +
        ' IS NOT NULL)' + bound + ' ORDER BY {seq} DESC LIMIT 51',
        (before,) if before is not None else ()).fetchall()
    uses = asset_usages(store, [row[1] for row in rows[:50]])
    return {'assets': [
        dict(id=id, name=name, format=fmt, created_at=created, size_bytes=int(size) if size is not None else None,
             has_original=bool(original), pending_delete=bool(pending), usages=uses[id])
        for _, id, name, fmt, created, size, original, pending in rows[:50]
    ], 'next': str(rows[49][0]) if len(rows) > 50 else None}


def preview(c):
    asset = c.storage.asset(c.data['id'])
    return {'text': (asset.get('text') or '')[:6000], 'columns': asset.get('columns', [])[:12],
            'rows': [{key: '' if row.get(key) is None else str(row[key])[:300] for key in asset.get('columns', [])[:12]}
                     for row in asset.get('preview', [])[:3]]}


def original_registered(c):
    store = c.storage
    row = store.db.execute('SELECT 1 FROM builder_assets WHERE {ws} AND ' +
                           store.json_text('json', 'original_path') + '=? LIMIT 1', (c.data['original'],)).fetchone()
    return {'registered': bool(row)}


def prepare_delete(c):
    store, id = c.storage, c.data['id']
    db = store.db
    store.begin_write()
    try:
        row = db.execute('SELECT json FROM builder_assets WHERE {ws} AND id=?', (id,)).fetchone()
        if not row:
            db.commit()
            return {}
        asset = json.loads(row[0])
        if 'pending_delete' in asset:
            db.commit()
            return asset['pending_delete']
        if asset.get('deleted'):
            db.commit()
            return {}

        uses = asset_usages(store, [id])[id]
        for use in uses:
            if use['kind'] == 'schedule':
                raise ValueError(f"‘{use['name']}’의 예약 데이터 소스를 교체한 후 삭제하세요.")
            if use['kind'] == 'running':
                raise ValueError(f"‘{use['name']}’ 실행 중입니다. 실행이 끝난 후 삭제하세요.")

        sessions = db.execute('SELECT id,json,revision FROM builder_sessions WHERE {ws} AND CAST(json AS TEXT) LIKE ?',
                              ('%"' + id + '"%',)).fetchall()
        for sid, body, rev in sessions:
            session = json.loads(body)
            if id in session['file_ids']:
                session['file_ids'] = [file_id for file_id in session['file_ids'] if file_id != id]
                result = db.execute('UPDATE builder_sessions SET json=?,revision=revision+1 WHERE {ws} AND id=? AND revision=?',
                                    (json.dumps(session, ensure_ascii=False), sid, rev))
                if not result.rowcount:
                    raise ValueError('설계 대화가 변경됐습니다. 삭제를 다시 시도하세요.')

        cleanup = {'directory': (asset.get('parser_metadata') or {}).get('analysis_artifact_directory')}
        path = asset.get('original_path')
        # Older uploads with identical content share a key. A pending row has already released its reference.
        if path and not db.execute('SELECT 1 FROM builder_assets WHERE {ws} AND id<>? AND ' +
                                   store.json_text('json', 'original_path') + '=? LIMIT 1', (id, path)).fetchone():
            cleanup['original'] = path
            if asset.get('original_bucket'):
                cleanup['bucket'] = asset['original_bucket']
        marker = {key: asset[key] for key in ('id', 'name', 'format', 'size_bytes', 'created_at') if key in asset}
        if any(use['kind'] == 'workflow' for use in uses):
            # Keep only the contract for immutable saved versions. Replacement files supply all actual data.
            marker.update(deleted=True, kind=asset.get('kind'), columns=asset.get('columns', []),
                          input_kinds=[k for k in ('table','document') if supports(asset,k)])
        marker['pending_delete'] = cleanup
        for table in ('document_review_events', 'document_reviews'):
            db.execute('DELETE FROM ' + table + ' WHERE {ws} AND asset_id=?', (id,))
        db.execute("UPDATE builder_assets SET json=?,rows_json='[]' WHERE {ws} AND id=?",
                   (json.dumps(marker, ensure_ascii=False), id))
        if store.business:
            store.business.archive_document(id)
        db.commit()
        return cleanup
    except Exception:
        db.rollback()
        raise


def finish_delete(c):
    store = c.storage
    id = c.data['id']
    store.begin_write()
    try:
        row = store.db.execute('SELECT json FROM builder_assets WHERE {ws} AND id=? AND ' +
                               store.json_text('json','pending_delete') + ' IS NOT NULL', (id,)).fetchone()
        if row:
            marker = json.loads(row[0])
            if marker.get('deleted'):
                schema = {k: marker[k] for k in ('id','name','kind','columns','input_kinds','deleted') if k in marker}
                store.db.execute('UPDATE builder_assets SET json=? WHERE {ws} AND id=?',
                                 (json.dumps(schema, ensure_ascii=False), id))
            else:
                store.db.execute('DELETE FROM builder_assets WHERE {ws} AND id=?', (id,))
        store.db.commit()
    except Exception:
        store.db.rollback()
        raise
    return {'deleted': True}
