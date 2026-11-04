"""Build the explicitly requested receipt workbook from the two confirmed user turns."""
import re

from agent_builder.capabilities.capability_gate import input_fingerprint
from agent_builder.capabilities.capability_judgement import validate_assessment_result
from agent_builder.conditions.condition_readiness import input_issues
from agent_builder.conditions.input_resolution import answer_kind
from agent_builder.design.document_rows_template import _settings, is_request_change, wants_all_fields
from agent_builder.design.receipt_intake import receipt_document, receipt_workbook_request
from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest, validate_plan
from contracts.models import AgentDraft


def _selected_fields(answer):
    prefix = '영수증에서 추출할 항목:'
    text = answer.removeprefix(prefix).strip()
    if not answer.startswith(prefix):
        if is_request_change(text) or wants_all_fields(text) or answer_kind(text) != 'answer':
            return []
        if text.rstrip('.!~') in {'네','아니요','예','응','좋아요','확인','안녕'} or re.search(r'취소|대신|보내|전송|변경|추가해|저장해', text):
            return []
    fields = list(dict.fromkeys(field.strip() for field in text.split(',')))
    if not 1 <= len(fields) <= 40 or any(not field or len(field) > 100 or re.search(r'[\x00-\x1f]', field) for field in fields):
        return []
    return fields


def _sheet_selection(answer):
    from workflow_engine.executors.document.receipt_items import ITEM_FIELDS
    item_prefix = '영수증 품목 시트:'
    lines = answer.strip().splitlines()
    if not lines or not lines[-1].startswith(item_prefix):
        return _selected_fields(answer), []
    items = [part.strip() for part in lines.pop().removeprefix(item_prefix).split(',')]
    if (not items or len(set(items)) != len(items) or any(item not in ITEM_FIELDS for item in items)
            or len(lines) > 1 or (lines and not lines[0].startswith('영수증에서 추출할 항목:'))):
        return [], []
    fields = _selected_fields(lines[0]) if lines else []
    return (fields, items) if fields or not lines else ([], [])


def receipt_workbook(state):
    if not any(q.get('key') == 'receipt.fields' for q in state.get('pending_questions', [])):
        return None
    messages = state['messages']
    if messages[-1]['role'] != 'user':
        return None
    users = [m['content'] for m in messages if m['role'] == 'user']
    previous = next((i for i in reversed(range(len(users)-1)) if answer_kind(users[i]) != 'guidance'), None)
    if previous is None or not receipt_workbook_request(users[previous]):
        return None
    request, answer = users[previous], users[-1]
    files = [r['id'] for r in state.get('resources', []) if receipt_document(r)]
    fields, items = _sheet_selection(answer)
    if not (fields or items) or not files or input_issues(state) or state.get('base_agent_id') or state.get('required_node_refs'):
        return None
    nodes = [dict(id='read', tool='read_document', label='영수증 읽기', bindings=[], settings=_settings(file_ids=files))]
    tasks = [dict(id='r1', source_message=previous, quote=request, description='연결한 영수증 읽기',
                  depends_on=[], spec=dict(kind='read', file_ids=files))]
    sheets, bindings, extracts = [], [], []
    for name, node_id, mode, columns, port, kind in [
            ('영수증', 'extract', 'fields', fields, 'summary_table', 'extract_fields'),
            ('상품내역', 'items', 'receipt_items', items, 'item_table', 'receipt_items')]:
        if not columns:
            continue
        if not (fields and items):
            port = 'sheets'
        label = name + ' 항목 추출'
        nodes.append(dict(id=node_id, tool='extract_information', label=label,
                          bindings=[dict(input='documents', node_id='read', output='documents')],
                          settings=_settings(fields=columns, extraction_mode=mode)))
        sheets.append({'name': name, 'table': {'$binding': port}})
        bindings.append(dict(input=port, node_id=node_id, output='table'))
        task_id = f'r{len(tasks) + 1}'
        extracts.append(task_id)
        tasks.append(dict(id=task_id, source_message=len(users)-1, quote=answer, description=label,
                          depends_on=['r1'], spec=dict(kind=kind, fields=columns)))
    nodes.append(dict(id='excel', tool='create_spreadsheet', label='엑셀 파일 만들기', bindings=bindings,
                      settings=dict(operation='create', arguments_json={'sheets': sheets})))
    export = dict(kind='receipt_workbook', sheets=[s['name'] for s in sheets]) if items else dict(kind='export', format='xlsx')
    tasks.append(dict(id=f'r{len(tasks) + 1}', source_message=previous, quote=request, description='엑셀 파일 출력',
                      depends_on=extracts, spec=export))
    labels = ', '.join(fields)
    if len(labels) > 1000:
        labels = f'선택한 {len(fields)}개'
    description = (f'영수증 시트에는 영수증마다 {labels} 항목을 한 행으로 정리합니다. ' if fields else '')
    if items:
        description += f'상품내역 시트에는 각 영수증에서 확인한 {", ".join(items)} 항목을 상품별로 정리합니다. '
    description += '엑셀(.xlsx) 파일로 저장합니다.'
    tools = list(dict.fromkeys(n['tool'] for n in nodes))
    draft = AgentDraft(
        name='영수증 항목 엑셀 정리', purpose=request[:2000], instructions=description,
        tool_ids=tools, document_ids=files,
        requirements=[dict(description='영수증 엑셀 출력', source_id='user', quote=request[:2000]),
                      dict(description='추출할 항목', source_id='user', quote=answer[:2000])],
        questions=[], unsupported=[], explanation=description,
        execution_plan=dict(filters=[], sort=[], match='all', missing_policy='keep', duplicate_policy='keep',
                            duplicate_field=None, output_format='xlsx'),
        workflow=dict(entry='read', nodes=nodes, edges=[dict(source=a['id'], target=b['id'], branch='next')
                                                        for a,b in zip(nodes,nodes[1:])])).model_dump()
    ledger = validate_plan({'tasks': tasks, 'unresolved': []}, state)
    assessment = validate_assessment_result(dict(decision='existing_tools', tool_ids=tools, missing_actions=[],
        missing_conditions=[], reason='연결된 문서, 선택한 추출 항목, 명시한 엑셀 출력을 등록 도구로 처리합니다.',
        user_quotes=[answer], model_calls=0), state)
    return dict(draft=draft, pending_questions=[], confirmed=False, saved_agent_id=None, issues=[], semantic_issues=[],
                condition_enforced=False, condition_required=False, condition_template=False, condition_ledger=None,
                capability_enforced=True, capability_assessment=assessment, capability_fingerprint=input_fingerprint(state),
                capability_questions=[], capability_deferred=False, gap_condition_review=None,
                requirement_required=True, requirement_ledger=ledger, requirement_fingerprint=ledger_fingerprint(state),
                requirement_digest=plan_digest(ledger), messages=messages+[dict(role='assistant', content=description)],
                trace=state.get('trace', [])+[dict(node='receipt_workbook', label='영수증 엑셀 설계', status='completed',
                                                   detail='요청 원문·선택 항목·파일로 실행 단계를 구성했습니다.', model_calls=0)])
