"""Server-side generation claims. No generated code, keys, or user file contents."""
import json
import re
import time
import secrets
import os
from .storage import uid, now
from .tool_gap_choice import creation_intent


def public_job(job):
    return {k:v for k,v in job.items() if k!='lease_token'}


def initialize(storage):
    storage.db.execute('''CREATE TABLE IF NOT EXISTS builder_tool_gap_jobs(
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, decision_id TEXT NOT NULL UNIQUE,
        input_sha256 TEXT NOT NULL, json TEXT NOT NULL)''')
    storage.db.commit()


def lease_seconds():
    raw=os.getenv('A4B_TOOL_JOB_LEASE_S','900')
    if not raw.isdigit() or not 30<=int(raw)<=1800:
        raise ValueError('제작 작업 제한 시간은 30~1800초로 설정하세요.')
    return int(raw)


def claim(storage, state, session, input_sha256):
    if not isinstance(input_sha256, str) or not re.fullmatch('[a-f0-9]{64}', input_sha256):
        raise ValueError('검토 명세와 생성 설정의 해시를 확인하세요.')
    initialize(storage)
    storage.db.execute('BEGIN IMMEDIATE')
    try:
        # Re-read inside the transaction, not the stale session passed by the caller.
        expected_revision = session['revision']
        session = storage.session(session['id'])
        if session['revision'] != expected_revision:
            raise ValueError('대화가 변경됐습니다. 제작 선택을 다시 확인하세요.')
        intent = creation_intent(state, session)
        if intent is None:
            raise ValueError('현재 요청의 새 도구 제작 선택이 필요합니다.')
        old = storage.db.execute('SELECT json,input_sha256 FROM builder_tool_gap_jobs WHERE decision_id=?',
                                 (intent['decision_id'],)).fetchone()
        if old:
            if old[1] != input_sha256:
                raise ValueError('같은 제작 선택으로 다른 명세를 실행할 수 없습니다. 다시 검토하고 선택하세요.')
            storage.db.commit()
            return {'claimed': False, 'job': public_job(json.loads(old[0]))}
        job = {'protocol': 'a4b.tool-gap-job.v1', 'id': uid(), 'session_id': session['id'],
               'decision_id': intent['decision_id'], 'gap_id': intent['gap_id'],
               'state_fingerprint': intent['state_fingerprint'], 'revision': intent['revision'],
               'input_sha256': input_sha256, 'status': 'running', 'created_at': now(),
               'result': None, 'lease_token':uid(), 'lease_expires_at':time.time()+lease_seconds(), 'workflow_execution_allowed': False, 'automatic_registration': False}
        storage.db.execute('INSERT INTO builder_tool_gap_jobs VALUES(?,?,?,?,?)',
            (job['id'], session['id'], intent['decision_id'], input_sha256, json.dumps(job, ensure_ascii=False)))
        storage.db.commit()
        return {'claimed': True, 'job': public_job(job), 'lease_token':job['lease_token']}
    except Exception:
        storage.db.rollback()
        raise


def finish(storage, state, session, job_id, result, lease_token=None):
    initialize(storage)
    storage.db.execute('BEGIN IMMEDIATE')
    try:
        row = storage.db.execute('SELECT json FROM builder_tool_gap_jobs WHERE id=? AND session_id=?', (job_id, session['id'])).fetchone()
        if not row:
            raise ValueError('현재 대화의 도구 제작 작업을 찾지 못했습니다.')
        job = json.loads(row[0])
        if not isinstance(lease_token,str) or not secrets.compare_digest(lease_token,job.get('lease_token','')):
            raise ValueError('제작 작업의 소유권이 일치하지 않습니다.')
        if job.get('lease_expires_at',0)<time.time() and job['status']=='running':
            raise ValueError('제작 작업의 제한 시간이 지났습니다. 복구 후 다시 선택하세요.')
        if not isinstance(result, dict) or set(result) != {'status', 'task_id', 'model_calls', 'passed_cases', 'total_cases', 'response_seconds'}:
            raise ValueError('도구 제작 결과 규격을 확인하세요.')
        if result['status'] not in ('passed_lab_tests', 'lab_not_passed', 'generation_error', 'stale'):
            raise ValueError('도구 제작 결과 상태를 확인하세요.')
        if not isinstance(result['task_id'], str) or not re.fullmatch('[a-z][a-z0-9_-]{0,59}', result['task_id']):
            raise ValueError('도구 제작 업무 ID를 확인하세요.')
        for key in ('model_calls', 'passed_cases', 'total_cases'):
            if key == 'model_calls' and result[key] is None and result['status'] == 'generation_error':
                continue  # Coordinator exception: completed model-call count is unknown.
            if type(result[key]) is not int or result[key] < 0:
                raise ValueError('도구 제작 결과 수치를 확인하세요.')
        import math
        if type(result['response_seconds']) not in (int, float) or not math.isfinite(result['response_seconds']) or result['response_seconds'] < 0:
            raise ValueError('도구 제작 결과 시간을 확인하세요.')
        if (result['passed_cases'] > result['total_cases'] or
                result['status'] == 'passed_lab_tests' and (not result['total_cases'] or result['passed_cases'] != result['total_cases'])):
            raise ValueError('도구 제작 통과 수치가 일치하지 않습니다.')
        intent = creation_intent(state, storage.session(session['id']))
        current = bool(intent and intent['decision_id'] == job['decision_id'] and intent['revision'] == job['revision'])
        status = result['status'] if current else 'stale'
        if job['status'] != 'running':
            if job['result'] != result:
                raise ValueError('완료된 제작 결과를 바꿀 수 없습니다.')
            storage.db.commit()
            return public_job(job)
        job.update(status=status, result=result, finished_at=now(), current_state_revalidated=current)
        storage.db.execute('UPDATE builder_tool_gap_jobs SET json=? WHERE id=?', (json.dumps(job, ensure_ascii=False), job_id))
        storage.db.commit()
        return public_job(job)
    except Exception:
        storage.db.rollback()
        raise


def current_job(storage, state, session):
    # Read-only before any job exists; session polling does not migrate schemas.
    exists = storage.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='builder_tool_gap_jobs'").fetchone()
    if not exists:
        return None
    intent = creation_intent(state, session)
    if not intent:
        return None
    row = storage.db.execute('SELECT json FROM builder_tool_gap_jobs WHERE session_id=? AND decision_id=?',
                             (session['id'], intent['decision_id'])).fetchone()
    return public_job(json.loads(row[0])) if row else None


def recover(storage,session):
    initialize(storage)
    storage.db.execute('BEGIN IMMEDIATE')
    try:
        changed=[]
        rows=storage.db.execute('SELECT id,json FROM builder_tool_gap_jobs WHERE session_id=?',(session['id'],)).fetchall()
        for identifier,text in rows:
            job=json.loads(text)
            if job['status']=='running' and job.get('lease_expires_at',0)<time.time():
                job.update(status='interrupted',finished_at=now(),current_state_revalidated=False,
                           recovery_reason='제작 작업 제한 시간 초과. 새 선택 후 재시도 필요.')
                storage.db.execute('UPDATE builder_tool_gap_jobs SET json=? WHERE id=?',(json.dumps(job,ensure_ascii=False),identifier))
                changed.append(public_job(job))
        storage.db.commit()
        return {'recovered':changed,'automatic_retry':False}
    except Exception:
        storage.db.rollback();raise
