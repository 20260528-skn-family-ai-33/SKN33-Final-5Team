"""One durable outcome per design request; clarification is never a failure.

Stage entries commit before model calls. A separate worker can therefore close a
timed-out request without needing the interrupted worker's in-memory state.
"""
import json
import hashlib
import os
import re
from uuid import UUID

from infrastructure.persistence.ids import uid, now

STAGES = {
    'resources': 'request_analysis', 'attachment_gate': 'request_analysis',
    'capability_check': 'request_analysis', 'condition_check': 'request_analysis',
    'requirements': 'request_analysis', 'generation_gate': 'design',
    'conversation': 'design', 'template_plan': 'design',
    'condition_audit': 'validation', 'validate': 'validation',
    'verification': 'validation', 'supervisor': 'validation', 'save': 'save',
}


def table_name(storage):
    # The PostgreSQL search_path is a4b_builder; generation history is business data.
    return 'a4b.workflow_generation_requests' if storage.dialect=='postgres' else 'builder_generation_requests'


def redact(text, api_key=''):
    text = str(text or '')
    secrets = [api_key, *[value for key, value in os.environ.items()
                         if any(part in key for part in ('API_KEY', 'PASSWORD', 'DATABASE_URL', 'SECRET', 'TOKEN'))]]
    for secret in sorted(set(filter(None, secrets)), key=len, reverse=True):
        text = text.replace(secret, '[보호된 값]')
    text = re.sub(r'(?i)\bBearer\s+[^\s,;]+', 'Bearer [보호된 값]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{8,}', '[보호된 키]', text)
    text = re.sub(r'(?i)postgres(?:ql)?://[^\s]+', '[보호된 DB 주소]', text)
    return text.replace('\0', '\ufffd')


def error_info(error, stage, api_key=''):
    name = type(error).__name__
    causes = []
    cause = error
    for _ in range(6):
        if cause is None or cause in causes:
            break
        causes.append(cause)
        cause = cause.__cause__ or cause.__context__ or getattr(cause, 'reason', None)
        if not isinstance(cause, BaseException):
            break
    names = [type(cause).__name__ for cause in causes]
    if stage == 'save':
        code, detail = 'save_failed', '워크플로우를 저장하지 못했습니다.'
    elif any('Timeout' in item or item == 'timeout' for item in names):
        code, detail = 'model_timeout', '모델 응답 시간이 초과됐습니다.'
    elif any(item in ('ValidationError', 'DraftResponseError', 'JSONDecodeError', 'StructuredOutputError',
                     'StructuredOutputValidationError', 'MultipleStructuredOutputsError') for item in names):
        code, detail = 'invalid_model_response', '모델 응답이 필요한 JSON·설정 형식을 충족하지 못했습니다.'
    elif any('Connection' in item or item == 'URLError' for item in names):
        code, detail = 'model_connection_failed', '모델 서버에 연결하지 못했습니다.'
    elif name in ('AuthenticationError', 'PermissionDeniedError'):
        code, detail = 'model_auth_failed', '모델 연결 계정 또는 접근 권한을 확인하세요.'
    elif name == 'RateLimitError':
        code, detail = 'model_rate_limited', '모델 API 사용 한도 또는 동시 요청 제한에 도달했습니다.'
    elif name == 'GraphRecursionError':
        code, detail = 'generation_step_limit', '설계·검증 반복이 단계 수 제한 안에 완료되지 못했습니다.'
    elif isinstance(error, (ValueError, TypeError, KeyError)) and stage in ('design', 'request_analysis'):
        code = 'invalid_model_response' if stage == 'design' else 'invalid_request'
        detail = '모델 응답 형식을 확인하세요.' if stage == 'design' else '요청 또는 대화 상태를 확인하세요.'
    else:
        code, detail = 'generation_failed', '워크플로우 생성 요청을 완료하지 못했습니다.'
    # Keep provider bodies, exception reprs and model-generated payloads out of history.
    return dict(reason_code=code, reason=redact(detail, api_key), error_type=name)


class GenerationHistory:
    def __init__(self, storage, request_id, api_key=''):
        self.storage = storage
        self.db = storage.db
        self.id = request_id
        self.api_key = api_key
        self.failure = None
        self.table = table_name(storage)

    @classmethod
    def start(cls, storage, data):
        storage.session(data['id'])  # Ownership comes from the authenticated workspace.
        request_id = str(UUID(data.get('generation_request_id') or uid()))
        audit = cls(storage, request_id, data.get('api_key', ''))
        stamp = now()
        record = dict(request_id=request_id, session_id=data['id'], action=data['action'],
                      session_revision=data.get('revision'),
                      request_hash=hashlib.sha256(str(data.get('content','')).encode()).hexdigest(),
                      request_text=redact(data.get('content', ''), audit.api_key)[:4000],
                      status='running', stage='request_analysis', node='request',
                      reason_code=None, reason=None, error_type=None, workflow_id=None,
                      started_at=stamp, updated_at=stamp, finished_at=None, events=[])
        storage.begin_write()
        try:
            old = audit.read(required=False)
            if old:
                keys = ('session_id', 'action', 'session_revision', 'request_hash')
                if any(old[key] != record[key] for key in keys) or old['status'] != 'running':
                    raise ValueError('이미 처리된 생성 요청입니다. 새 요청 ID를 사용하세요.')
            else:
                record['events'].append(dict(at=stamp, stage='request_analysis', node='request', status='started'))
                audit.db.execute(f'INSERT INTO {audit.table}({{wsc}}id,session_id,json) VALUES({{wsv}}?,?,?)',
                                 (request_id, data['id'], json.dumps(record, ensure_ascii=False)))
            audit.db.commit()
        except Exception:
            audit.db.rollback()
            raise
        return audit

    def read(self, required=True):
        row = self.db.execute(f'SELECT json FROM {self.table} WHERE {{ws}} AND id=?', (self.id,)).fetchone()
        if row:
            return json.loads(row[0])
        if required:
            raise ValueError('생성 요청 이력을 찾을 수 없습니다.')

    def _write(self, record):
        self.db.execute(f'UPDATE {self.table} SET json=? WHERE {{ws}} AND id=?',
                        (json.dumps(record, ensure_ascii=False), self.id))

    def change(self, status=None, stage=None, node=None, event_status=None, **fields):
        self.storage.begin_write()
        try:
            record = self.read()
            if record['status'] != 'running':
                self.db.commit()
                return record
            record.update(fields, updated_at=now())
            if stage:
                record['stage'] = stage
            if node:
                record['node'] = node
            if status:
                record.update(status=status, finished_at=record['updated_at'])
            event = dict(at=record['updated_at'], stage=record['stage'], node=record['node'],
                         status=event_status or status or 'started')
            event.update({k: record.get(k) for k in ('reason_code', 'reason', 'error_type') if record.get(k)})
            record['events'].append(event)
            self._write(record)
            self.db.commit()
            return record
        except Exception:
            self.db.rollback()
            raise

    def enter(self, node):
        # Interrupt nodes do not advance past the stage that asked the question.
        if node not in STAGES:
            return
        record = self.change(stage=STAGES[node], node=node, reason_code=None, reason=None, error_type=None)
        if record['status'] != 'running':
            raise ValueError('생성 요청이 이미 종료됐습니다. 새 요청으로 다시 시도하세요.')

    def error(self, error):
        self.db.rollback()
        record = self.read()
        self.failure = dict(stage=record['stage'], node=record['node'], **error_info(error, record['stage'], self.api_key))
        if self.failure['reason_code']=='invalid_request' and record['node'] in ('capability_check','condition_check','requirements'):
            self.failure.update(reason_code='invalid_model_response',reason='요청 분석 모델의 응답이 필요한 형식을 충족하지 못했습니다.')
        self.change(event_status='error', **self.failure)

    def observed(self, node, state):
        outcome = self.outcome(state)
        if node == 'verification' and (state.get('verification') or {}).get('error_code'):
            result = state['verification']
            self.change(event_status='error' if state.get('verification_enforced') else 'warning',
                        reason_code=result['error_code'], reason='검증기를 완료하지 못했습니다.', error_type=None)
        elif outcome['status'] in ('failed', 'blocked', 'waiting_input'):
            fields = {k: v for k, v in outcome.items() if k != 'status'}
            self.change(event_status=outcome['status'], **fields)

    def outcome(self, state, next_nodes=()):
        def result(status, code, reason, **extra):
            return dict(status=status, reason_code=code, reason=redact(reason, self.api_key)[:2000], error_type=None, **extra)
        if state.get('status') == 'saved' and state.get('saved_agent_id'):
            return result('succeeded', 'workflow_saved', '워크플로우 저장을 완료했습니다.', stage='save', workflow_id=state['saved_agent_id'])
        if state.get('status') == 'validation_failed':
            if self.failure:
                return dict(status='failed', **self.failure)
            for key in ('capability_assessment', 'condition_ledger'):
                item = state.get(key) or {}
                enforced = state.get('capability_enforced') if key=='capability_assessment' else state.get('condition_enforced')
                if not enforced:
                    continue
                if item.get('decision') == 'system_error':
                    return result('failed', 'analysis_failed', ' / '.join(item.get('issues') or state.get('issues') or []) or '요청 분석을 완료하지 못했습니다.', stage='request_analysis')
                if item.get('decision') == 'manual_review':
                    return result('blocked', 'analysis_review_required', ' / '.join(state.get('issues') or []) or '요청의 지원 범위와 조건에 대한 검토가 필요합니다.', stage='request_analysis')
            verification = state.get('verification') or {}
            if state.get('verification_enforced') and verification.get('error_code'):
                return result('failed', verification['error_code'], '검증기 호출 또는 응답 처리에 실패했습니다.', stage='validation')
            output = (verification.get('model_output') or {}) if state.get('verification_enforced') else {}
            if state.get('verification_enforced') and verification.get('route')=='unsupported':
                return result('blocked', 'unsupported_tools', ' / '.join(output.get('unsupported_actions') or []) or output.get('summary') or '검증 단계에서 지원하지 않는 도구 사용이 확인됐습니다.', stage='validation')
            details = list(state.get('issues') or [])
            if state.get('verification_enforced'):
                details += [f.get('detail', '') for f in verification.get('findings', [])]
                details += [item.get('detail', '') for item in output.get('issues', []) + output.get('missing_conditions', [])]
                if not details and output.get('summary'):details.append(output['summary'])
            details = list(dict.fromkeys(filter(None,details)))
            return result('blocked', 'verification_failed', ' / '.join(details) or '설정·근거 검증을 통과하지 못했습니다.', stage='validation')
        if state.get('status') == 'unsupported':
            reasons = ((state.get('capability_assessment') or {}).get('missing_actions')
                       or (state.get('draft') or {}).get('unsupported') or state.get('issues') or [])
            stage = 'request_analysis' if (state.get('capability_assessment') or {}).get('decision') == 'missing_capability' else 'design'
            return result('blocked', 'unsupported_tools', ' / '.join(map(str, reasons)) or '요청에 필요한 도구가 지원되지 않습니다.', stage=stage)
        if 'await_capability' in next_nodes or 'await_input' in next_nodes or state.get('status') == 'needs_clarification':
            questions = [q.get('text', '') for q in state.get('pending_questions', [])] or (state.get('draft') or {}).get('questions', [])
            analysis = 'await_capability' in next_nodes or state.get('condition_enforced') or any(
                q.get('source') in ('capability', 'receipt', 'condition') for q in state.get('pending_questions', []))
            stage = 'validation' if state.get('verification_enforced') and state.get('verification_route')=='ask_user' else 'request_analysis' if analysis else 'design'
            return result('waiting_input', 'clarification_required', ' / '.join(questions) or '추가 정보에 대한 답변을 기다립니다.', stage=stage)
        if 'await_approval' in next_nodes or state.get('status') == 'ready':
            return result('waiting_confirmation', 'confirmation_required', '설계 검증을 마쳤고 사용자의 저장 확인을 기다립니다.', stage='validation')
        return result('failed', 'generation_incomplete', '생성 요청이 완료 상태에 도달하지 못했습니다.')

    def finish(self, state, next_nodes=(), result_revision=None):
        fields = {'result_revision': result_revision} if result_revision is not None else {}
        return self.change(**self.outcome(state, next_nodes), **fields)

    def fail(self, error):
        self.db.rollback()  # A rejected save must not roll back its own diagnostic record.
        if not self.failure:
            self.error(error)
        return self.change(status='failed', **self.failure)

    def published_in_transaction(self, state, revision):
        """Publish the outcome and session revision in the same database transaction."""
        record=self.read()
        if record['status']=='running':
            record.update(self.outcome(state, getattr(self,'result_next_nodes',())))
            stamp=now()
            record.update(updated_at=stamp, finished_at=stamp)
            event=dict(at=stamp, stage=record['stage'], node=record['node'], status=record['status'])
            event.update({key:record[key] for key in ('reason_code','reason','error_type') if record.get(key)})
            record['events'].append(event)
        elif record['status'] not in ('succeeded','waiting_input','waiting_confirmation'):
            raise ValueError('종료된 생성 요청의 결과를 반영할 수 없습니다.')
        record['result_revision']=revision
        self._write(record)

    def saved_in_transaction(self, workflow_id, workflow_version=None):
        """The workflow and its successful outcome commit together, before worker exit."""
        record = self.read()
        if record['status'] != 'running':
            raise ValueError('생성 요청이 이미 종료되어 저장할 수 없습니다.')
        stamp = now()
        if workflow_version is not None:record['workflow_version']=workflow_version
        record.update(status='succeeded', stage='save', node='save', reason_code='workflow_saved',
                      reason='워크플로우 저장을 완료했습니다.', error_type=None, workflow_id=workflow_id,
                      updated_at=stamp, finished_at=stamp)
        record['events'].append(dict(at=stamp, stage='save', node='save', status='succeeded', reason_code='workflow_saved'))
        self._write(record)


def history(storage, session_id):
    storage.session(session_id)
    return [json.loads(row[0]) for row in storage.db.execute(
        f'SELECT json FROM {table_name(storage)} WHERE {{ws}} AND session_id=? ORDER BY {{seq}} DESC LIMIT 50', (session_id,))]
