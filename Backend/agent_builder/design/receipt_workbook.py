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
    fields = _selected_fields(answer)
    if not fields or not files or input_issues(state) or state.get('base_agent_id') or state.get('required_node_refs'):
        return None
    nodes = [
        dict(id='read', tool='read_document', label='영수증 읽기', bindings=[], settings=_settings(file_ids=files)),
        dict(id='extract', tool='extract_information', label='선택한 영수증 항목 추출',
             bindings=[dict(input='documents', node_id='read', output='documents')],
             settings=_settings(fields=fields, extraction_mode='fields')),
        dict(id='excel', tool='create_spreadsheet', label='엑셀 파일 만들기',
             bindings=[dict(input='sheets', node_id='extract', output='table')],
             settings=dict(operation='create', arguments_json={'sheets': [{'name': '영수증', 'table': {'$binding': 'sheets'}}]})),
    ]
    labels = ', '.join(fields)
    if len(labels) > 1000:
        labels = f'선택한 {len(fields)}개'
    description = '영수증마다 ' + labels + ' 항목을 한 행으로 정리해 엑셀(.xlsx) 파일을 만듭니다.'
    tools = [n['tool'] for n in nodes]
    draft = AgentDraft(
        name='영수증 항목 엑셀 정리', purpose=request[:2000], instructions=description,
        tool_ids=tools, document_ids=files,
        requirements=[dict(description='영수증 엑셀 출력', source_id='user', quote=request[:2000]),
                      dict(description='추출할 항목', source_id='user', quote=answer[:2000])],
        questions=[], unsupported=[], explanation=description,
        execution_plan=dict(filters=[], sort=[], match='all', missing_policy='keep', duplicate_policy='keep',
                            duplicate_field=None, output_format='xlsx'),
        workflow=dict(entry='read', nodes=nodes, edges=[dict(source='read', target='extract', branch='next'),
                                                        dict(source='extract', target='excel', branch='next')])).model_dump()
    tasks = [
        dict(id='r1', source_message=previous, quote=request, description='연결한 영수증 읽기', depends_on=[],
             spec=dict(kind='read', file_ids=files)),
        dict(id='r2', source_message=len(users)-1, quote=answer, description='선택한 항목 추출', depends_on=['r1'],
             spec=dict(kind='extract_fields', fields=fields)),
        dict(id='r3', source_message=previous, quote=request, description='엑셀 파일 출력', depends_on=['r2'],
             spec=dict(kind='export', format='xlsx')),
    ]
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
