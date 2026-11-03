"""Optional verifier gate. Never replaces native validation or user approval."""
from copy import deepcopy
import hashlib
import json
import os
import uuid

from verification.workflow import VerificationInput, verify
from verification.workflow.adapters import draft_from_backend, load_registry
from workflow_engine.catalog.registry import TOOLS
from documents.resources.document_resources import supports, analysis_of, chunks_for_asset, locator


# Verification scope for tools added after the delivered registry snapshot. The
# delivered registry.json stays byte-identical; these entries only declare the
# basic port/setting checks, while column semantics stay with native validation.
LOCAL_SCOPES = {
    'calculate_columns': dict(required_input_ports=['table'], required_settings=['table_operation']),
    'text_transform': dict(required_input_ports=['text'], required_settings=['text_find', 'text_replacement']),
    'base64_codec': dict(required_input_ports=['text'], required_settings=['codec_mode']),
    'text_hash': dict(required_input_ports=['text'], required_settings=[]),
    'date_transform': dict(required_input_ports=['text'], required_settings=['date_operation']),
    'text_statistics': dict(required_input_ports=['text'], required_settings=['statistics_unit']),
}


def current_tools():
    reference = {t.id: t.model_dump() for t in load_registry()}
    for id, scope in LOCAL_SCOPES.items():
        reference.setdefault(id, dict(id=id, check_scope='basic', **scope))
    result = []
    for tool in TOOLS:
        if tool['id'] not in reference:
            if not tool.get('operation_contracts'):raise ValueError('새 도구의 검증 범위를 먼저 정의하세요: ' + tool['id'])
            reference[tool['id']]=dict(id=tool['id'],check_scope='basic',required_input_ports=[],required_settings=[])
        item = reference[tool['id']]
        item.update(description=tool['description'], input_ports=tool['input_ports'],
                    output_ports=tool['output_ports'], enabled=True)
        if tool['id'] == 'compose_answer':
            item['required_input_ports'] = ['documents']
        result.append(item)
    return result



def project_operations(raw, *, strict=True):
    """Adapt service operation nodes without changing the delivered verifier package."""
    from workflow_engine.catalog.registry import uses_operation,node_ports
    from workflow_engine.executors.canonical_operation import validate_settings
    snapshot=deepcopy(raw);specs=[]
    nodes={n['id']:n for n in snapshot['workflow']['nodes']}
    for n in nodes.values():
        if not uses_operation(n):continue
        if strict:spec,_=validate_settings(n,[{'id':i} for i in snapshot.get('document_ids',[])])
        else:
            # An invalid historical draft is evidence of what was repaired,
            # never an executable candidate or a reason to reject a valid fix.
            from workflow_engine.catalog.canonical import operation_spec
            spec=operation_spec(n['tool'],n['settings'].get('operation'))
        identifier='operation_'+hashlib.sha256(json.dumps(n,sort_keys=True).encode()).hexdigest()[:24]
        ins,outs=node_ports(n)
        # The native validator already accepts JSON sinks. Project each actual bound
        # type so the unchanged verifier can compare its strict port contracts.
        for b in n['bindings']:
            if ins.get(b['input'])=='json' and b['node_id'] in nodes:
                ins[b['input']]=node_ports(nodes[b['node_id']])[1].get(b['output'],'json')
        from workflow_engine.catalog.canonical import contract
        scope=contract(n['tool'],spec)['scope']
        specs.append(dict(id=identifier,description=n['tool']+'.'+spec['id']+' · '+scope+' · text 출력은 앱 실행 결과 영역에 표시됩니다.',enabled=True,input_ports=ins,
            output_ports=outs,required_input_ports=[],required_settings=['operation','arguments_json'],check_scope='basic'))
        n['settings']['operation']=spec['id'];n['settings']['arguments_json']=n['settings'].get('arguments_json') or '{}'
    # Resolve source ports before changing any identity.
    for n,spec in zip([n for n in nodes.values() if uses_operation(n)],specs):n['tool']=spec['id']
    return snapshot,specs


def tools_for_drafts(*drafts):
    result={t['id']:t for t in current_tools()}
    used=set()
    for index,draft in enumerate(drafts):
        if not draft or not draft.get('workflow'):continue
        snapshot,specs=project_operations(draft,strict=index==len(drafts)-1)
        used.update(n['tool'] for n in snapshot['workflow']['nodes'])
        result.update({spec['id']:spec for spec in specs})
    names={t['id']:t['name'] for t in TOOLS}
    for identifier,item in result.items():
        if identifier in used:continue
        # Keep complete ID/name discovery; unused union ports cannot validate a
        # node and repeated contracts exceed the verifier's context budget.
        item.update(description=names.get(identifier,identifier),input_ports={},output_ports={},
                    required_input_ports=[],required_settings=[],check_scope='basic')
    return list(result.values())


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
    if status!='failed' and analysis_of(asset) and analysis_of(asset).get('review_required'):
        status='partial'
    doc = dict(id=asset['id'], version=str(asset.get('version') or asset.get('hash') or '1'),
               parse_status=status, chunks=[], tables=[])
    if status == 'failed':
        return doc
    if asset['kind'] == 'table':
        doc['tables'] = [dict(id='table-1', columns=asset['columns'], sample_rows=asset.get('preview', [])[:20])]
        if asset.get('name'):
            doc['chunks']=[dict(id='upload-metadata',text='파일명: '+asset['name']+'\n연결 자료 ID: '+asset['id'],locator='upload-metadata')]
    if analysis_of(asset) and supports(asset,'document'):
        doc['chunks']=[dict(id=c['source_id'],text=c['text'],locator=locator(c)) for c in chunks_for_asset(asset)]
    elif asset['kind'] != 'table':
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


def pending_result(request_id):
    """Placeholder stored by defer mode until the batch run fills in the verdict."""
    return dict(request_id=request_id, route='pending', execution_allowed=False, model_output=None,
                findings=[dict(code='deferred', severity='info', detail='판정은 일괄 검증 실행 후 기록됩니다.')],
                deferred=True)


def request_text_evidence(messages):
    """Plain request text is evidence, not an uploaded file or executable asset."""
    users=[m for m in messages if m['role']=='user']
    version=hashlib.sha256(json.dumps(users,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return dict(id='user-message-evidence',version=version,parse_status='ready',tables=[],
                chunks=[dict(id=m['id'],text=m['content'],locator='user-message:'+m['id']) for m in users])


def make_verification_node(storage, verifier=None, mode=None):
    def verification_node(state):
        setting = mode or os.getenv('A4B_VERIFIER_MODE', 'off')
        if setting not in ('off', 'shadow', 'defer', 'enforce'):
            raise ValueError('A4B_VERIFIER_MODE는 off/shadow/defer/enforce이어야 합니다.')
        if state.get('verification_required') or state.get('verification_enforced'):setting='enforce'
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
                assets={a['id']:a for a in state.get('resources', [])}
                for node in snapshot['workflow']['nodes']:
                    if node['tool'] in ('read_table','read_document') and not node['settings'].get('file_ids'):
                        kind='table' if node['tool']=='read_table' else 'document'
                        node['settings']['file_ids']=[i for i in raw.get('document_ids',[]) if i in assets and supports(assets[i],kind)]
                version=hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
                snapshot,_=project_operations(snapshot,strict=label!='previous')
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
                documents=([resource_document(a) for a in state.get('resources', [])]
                           if state.get('resources') else [request_text_evidence(messages)]),
                tools=tools_for_drafts(previous,draft), previous_draft=previous_adapted,
                candidate_draft=candidate_adapted))
            # defer records the same input as shadow but leaves the model call to
            # scripts/run-deferred-verifications.py, so the user does not wait for it.
            result = (pending_result(request_id) if setting == 'defer'
                      else (verifier or verify)(payload).model_dump(mode='json'))
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
                      verification_required=setting == 'enforce',
                      verification_last_candidate=deepcopy(draft),
                      verification_previous_draft=deepcopy(previous), verification_user_fingerprint=fingerprint,
                      trace=state.get('trace', []) + [dict(node='verification', label='Qwen 분류·검증',
                      status='completed' if route in ('continue_review', 'pending') else 'needs_input',
                      detail=f'{setting}: {route} · ' + ' / '.join(detail))])
        if setting == 'enforce' and route != 'continue_review':
            update['issues'] = list(dict.fromkeys(state.get('issues', []) + detail + ['검증 경로: ' + route]))
            update['confirmed'] = False
            if route == 'repair_plan' and draft:
                update['draft'] = {**draft, 'questions': [], 'question_options': []}
        return update
    return verification_node
