"""Optional verifier gate. Never replaces native validation or user approval."""
from copy import deepcopy
import hashlib
import json
import os
import uuid

from a4b_verifier import VerificationInput, verify
from a4b_verifier.adapters import draft_from_backend, load_registry
from ..registry import TOOLS


def current_tools():
    reference = {t.id: t.model_dump() for t in load_registry()}
    result = []
    for tool in TOOLS:
        if tool['id'] not in reference:
            raise ValueError('새 도구의 검증 범위를 먼저 정의하세요: ' + tool['id'])
        item = reference[tool['id']]
        item.update(description=tool['description'], input_ports=tool['input_ports'],
                    output_ports=tool['output_ports'], enabled=True)
        if tool['id'] == 'compose_answer':
            item['required_input_ports'] = ['documents']
        result.append(item)
    return result


def resource_document(asset):
    """Map actual parser output without pretending partial data is complete."""
    aliases={'completed':'ready','succeeded':'ready','error':'failed'}
    statuses=[aliases.get(value,value) for value in
              [asset.get('processing_status','ready'),asset.get('parser_status','ready')]]
    status=('failed' if 'failed' in statuses else 'ready' if all(value=='ready' for value in statuses) else 'partial')
    warnings = ' '.join(asset.get('warnings', []))
    metadata=asset.get('parser_metadata')
    if metadata is None:metadata={}
    if not isinstance(metadata,dict):raise ValueError('파서 메타데이터는 객체여야 합니다.')
    supplement=metadata.get('supplement_report')
    if supplement is None:supplement={}
    if not isinstance(supplement,dict):raise ValueError('본문 보완 보고서는 객체여야 합니다.')
    if status!='failed' and (any(text in warnings for text in ['일부 변환 오류', '텍스트가 없는 페이지'])
                             or supplement.get('errors') or supplement.get('deferred_pages')):
        status = 'partial'
    doc = dict(id=asset['id'], version=str(asset.get('version') or asset.get('hash') or '1'),
               parse_status=status, chunks=[], tables=[])
    if status == 'failed':
        return doc
    if asset['kind'] == 'table':
        doc['tables'] = [dict(id='table-1', columns=asset['columns'], sample_rows=asset.get('preview', [])[:20])]
    else:
        pages = asset.get('pages') or [asset.get('text', '')]
        for page, text in enumerate(pages, 1):
            for start in range(0, len(text), 1400):
                chunk = text[start:start + 1800]
                if chunk.strip():
                    doc['chunks'].append(dict(id=f'p{page}-{start}', text=chunk,
                                              locator=f'page={page};offset={start}'))
    if not doc['chunks'] and not doc['tables']:
        doc['parse_status'] = 'partial'
    # Contract validation rejects over-budget content; never silently drop pages.
    return doc


def make_verification_node(storage, verifier=None, mode=None):
    def verification_node(state):
        setting = mode or os.getenv('A4B_VERIFIER_MODE', 'off')
        if setting not in ('off', 'shadow', 'enforce'):
            raise ValueError('A4B_VERIFIER_MODE는 off/shadow/enforce이어야 합니다.')
        if setting == 'off':
            return {'verification_route': None, 'verification_enforced': False}
        messages = [dict(id=f'm{i}', role=m['role'], content=m['content'])
                    for i, m in enumerate(state['messages']) if m['role'] in ('user', 'assistant')]
        users = [m for m in messages if m['role'] == 'user']
        fingerprint = hashlib.sha256(json.dumps(users, ensure_ascii=False).encode()).hexdigest()
        previous = (state.get('verification_last_candidate') if fingerprint != state.get('verification_user_fingerprint')
                    else state.get('verification_previous_draft'))
        draft = state.get('draft')
        request_id = str(uuid.uuid4())
        payload = None
        try:
            def adapt(raw, label):
                if not raw or not raw.get('workflow'):
                    return None
                snapshot=deepcopy(raw)
                kinds={a['id']:a['kind'] for a in state.get('resources', [])}
                for node in snapshot['workflow']['nodes']:
                    if node['tool'] in ('read_table','read_document') and not node['settings'].get('file_ids'):
                        kind='table' if node['tool']=='read_table' else 'document'
                        node['settings']['file_ids']=[i for i in raw.get('document_ids',[]) if kinds.get(i)==kind]
                version=hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
                return draft_from_backend(snapshot, draft_id=state['session_id'], version=version)
            previous_adapted = adapt(previous, 'previous')
            candidate_adapted = adapt(draft, 'candidate')
            # Reflection needs both drafts; without a previous one there is nothing to compare,
            # so don't ask for it (otherwise a valid first completed draft is blocked in enforce).
            has_pair = previous_adapted is not None and candidate_adapted is not None
            payload = VerificationInput.model_validate(dict(
                contract_version='a4b.verifier.v1', request_id=request_id,
                user_request=users[0]['content'], messages=messages,
                latest_user_answer_id=users[-1]['id'] if len(users) > 1 and has_pair else None,
                documents=[resource_document(a) for a in state.get('resources', [])],
                tools=current_tools(), previous_draft=previous_adapted,
                candidate_draft=candidate_adapted))
            result = (verifier or verify)(payload).model_dump(mode='json')
        except (ValueError, TypeError, KeyError) as error:
            result = dict(request_id=request_id, route='manual_review', execution_allowed=False,
                          model_output=None, findings=[dict(code='integration_contract_error', severity='review',
                          detail='검증 입력 계약을 확인하세요: ' + str(error)[:400])], error_code='integration_contract_error')
        storage.db.execute('CREATE TABLE IF NOT EXISTS builder_verifications '
                           '(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, input_json TEXT, result_json TEXT NOT NULL)')
        storage.db.execute('INSERT INTO builder_verifications VALUES(?,?,?,?)',
                           (request_id, state['session_id'], payload.model_dump_json() if payload else None,
                            json.dumps(result, ensure_ascii=False)))
        storage.db.commit()
        route = result['route']
        detail = [f['detail'] for f in result['findings']]
        output = result.get('model_output') or {}
        detail += [i['detail'] for i in output.get('issues', [])]
        detail += [m['detail'] for m in output.get('missing_conditions', [])]
        if output.get('summary'):
            detail.append(output['summary'])
        update = dict(verification=result, verification_route=route,
                      verification_enforced=setting == 'enforce',
                      verification_last_candidate=deepcopy(draft),
                      verification_previous_draft=deepcopy(previous), verification_user_fingerprint=fingerprint,
                      trace=state.get('trace', []) + [dict(node='verification', label='Qwen 분류·검증',
                      status='completed' if route == 'continue_review' else 'needs_input',
                      detail=f'{setting}: {route} · ' + ' / '.join(detail))])
        if setting == 'enforce' and route != 'continue_review':
            update['issues'] = list(dict.fromkeys(state.get('issues', []) + detail + ['검증 경로: ' + route]))
            update['confirmed'] = False
            if route == 'repair_plan' and draft:
                update['draft'] = {**draft, 'questions': [], 'question_options': []}
        return update
    return verification_node
