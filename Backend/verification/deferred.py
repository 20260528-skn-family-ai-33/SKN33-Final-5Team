"""Fill in verdicts recorded by A4B_VERIFIER_MODE=defer. Run when no design work is active."""
import json
from datetime import datetime, timezone
from pathlib import Path

from infrastructure.persistence.db import storage_kind
from verification.workflow import VerificationInput, verify


def pending_ids(db):
    rows = db.execute("SELECT id, result_json FROM builder_verifications WHERE {ws} AND input_json IS NOT NULL").fetchall()
    return [id for id, result in rows if json.loads(result).get('route') == 'pending']


def run_pending(directory, verifier=None, limit=None):
    """Verify stored inputs in place. Returns per-record routes; never touches session state."""
    path = Path(directory) / 'agent-builder.sqlite'
    if storage_kind() == 'sqlite' and not path.exists():
        raise FileNotFoundError('설계 DB를 찾지 못했습니다: ' + str(path))
    from infrastructure.persistence.storage import Storage
    storage = Storage(directory)
    db = storage.db
    try:
        if not db.table_exists('builder_verifications'):
            return []
        done = []
        for id in pending_ids(db)[:limit]:
            raw = db.execute('SELECT input_json FROM builder_verifications WHERE {ws} AND id=?', (id,)).fetchone()[0]
            try:
                result = (verifier or verify)(VerificationInput.model_validate_json(raw)).model_dump(mode='json')
            except (ValueError, TypeError, KeyError) as error:
                result = dict(request_id=id, route='manual_review', execution_allowed=False, model_output=None,
                              findings=[dict(code='integration_contract_error', severity='review',
                              detail='검증 입력 계약을 확인하세요: ' + str(error)[:400])], error_code='integration_contract_error')
            result.update(deferred=True, verified_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
            db.execute('UPDATE builder_verifications SET result_json=? WHERE {ws} AND id=?', (json.dumps(result, ensure_ascii=False), id))
            db.commit()
            done.append({'id': id, 'route': result['route']})
        return done
    finally:
        storage.close()
