"""A closed, read-only Gmail summary recipe grounded in registered operation contracts.

This recognizes the complete request, not a Gmail keyword. Filters, delivery,
other channels, attachments, recurrence and additional tasks stay with the planner.
Account authorization remains an execution prerequisite and is never inferred here.
"""
import json
import re

from contracts.models import AgentDraft, NodeSettings


_REQUEST = re.compile(
    r'(?:지메일|gmail)(?:에서|의)?(?:최근|최신)(10|[1-9])(?:개|건)(?:의)?'
    r'(?:메일|이메일)(?:의|을|를)?(?:내용|본문)?(?:을|를)?'
    r'요약(?:해서정리|하고정리|정리)?해(?:줘|주세요|주라)', re.I)
RECIPE = 'gmail_recent_summary.v1'


def requested_count(text):
    if not isinstance(text,str) or len(text)>2000:
        return None
    compact = re.sub(r'\s+', '', text.strip().rstrip('.!~'))
    match = _REQUEST.fullmatch(compact)
    return int(match[1]) if match else None


def gmail_summary_request(state):
    if state.get('base_agent_id') or state.get('required_node_refs') or state.get('resources'):
        return None
    users = [m['content'] for m in state.get('messages', []) if m.get('role') == 'user']
    if not users:
        return None
    count = requested_count(users[-1])
    # Do not silently erase earlier independent work or a changed instruction.
    if count is None or any(requested_count(text) != count for text in users):
        return None
    from workflow_engine.catalog.canonical import operation_spec, arguments_spec, ports
    from workflow_engine.catalog.registry import TOOLS
    try:
        spec = operation_spec('email.list', 'read_messages')
        args = arguments_spec('email.list', spec)
        _, outputs = ports('email.list', spec)
    except ValueError:
        return None
    summary = next((tool for tool in TOOLS if tool['id'] == 'compose_answer'), {})
    if (set(args) != {'query', 'max_results'} or any(args.values())
            or outputs.get('documents') != 'documents'
            or summary.get('inputs', {}).get('documents') != 'documents'):
        return None
    return dict(request=users[-1], count=count, source_message=len(users)-1)


def gmail_summary_assessment(state):
    request = gmail_summary_request(state)
    if not request:
        return None
    return dict(decision='existing_tools', tool_ids=['email.list', 'compose_answer'],
                missing_conditions=[], missing_actions=[], user_quotes=[request['request']],
                reason='등록된 Gmail 본문 묶음 읽기의 documents 출력과 근거 요약 입력을 연결할 수 있습니다. '
                       '계정 읽기 권한과 모델 연결은 실행 전에 확인하며 설계 시 기능 부재로 취급하지 않습니다.',
                registered_workflow=RECIPE, model_calls=0, allows_execution=False, allows_generation=False,
                runtime_prerequisites=['Gmail 읽기 권한 연결', '요약 모델 연결'],
                scope='catalog assessment only; not condition READY')


def gmail_summary_plan(state):
    request = gmail_summary_request(state)
    if not request:
        return None
    return dict(tasks=[dict(id='r1', source_message=request['source_message'], quote=request['request'],
                           description=f"최근 메일 최대 {request['count']}건 읽기와 원문 요약",
                           depends_on=[], spec=dict(kind='gmail_summary', max_results=request['count'], request=request['request']))],
                unresolved=[])


def gmail_summary_reply(state):
    request = gmail_summary_request(state)
    if not request:
        return None
    from agent_builder.conditions.condition_readiness import input_issues
    if input_issues(state):
        return None
    from agent_builder.capabilities.capability_gate import input_fingerprint
    from agent_builder.design.requirement_ledger import validate_plan, ledger_fingerprint, plan_digest
    count, quote = request['count'], request['request']
    explanation = (f'최근 메일을 최대 {count}건 읽고 제목·보낸 사람·본문의 핵심 내용을 메일별로 요약합니다. '
                   '메일이 적으면 실제 조회된 메일만 정리하고, 내용이 없는 경우에는 없다고 안내합니다. '
                   '실행할 때 서비스 연결의 Gmail 읽기 권한과 AI 사용 가능 상태를 확인합니다.')
    defaults = NodeSettings(file_ids=[], query=None, fields=[], instructions=None, format=None,
                            question=None, condition_field=None, condition_operator=None).model_dump()
    nodes = [
        dict(id='mail', tool='email.list', label=f'최근 메일 {count}건 읽기', bindings=[],
             settings={**defaults, 'operation':'read_messages', 'arguments_json':json.dumps({'max_results':count})}),
        dict(id='summary', tool='compose_answer', label='메일 내용 요약',
             bindings=[dict(input='documents', node_id='mail', output='documents')],
             settings={**defaults, 'instructions':quote}),
    ]
    draft = AgentDraft(name=f'최근 메일 {count}건 요약', purpose=quote, instructions=explanation,
                       tool_ids=['email.list', 'compose_answer'], document_ids=[],
                       requirements=[dict(description='최근 메일 본문 조회와 요약', source_id='user', quote=quote)],
                       questions=[], unsupported=[], explanation=explanation,
                       execution_plan=dict(filters=[], sort=[], match='all', missing_policy='keep',
                                           duplicate_policy='keep', duplicate_field=None, output_format='json'),
                       workflow=dict(entry='mail', nodes=nodes,
                                     edges=[dict(source='mail', target='summary', branch='next')])).model_dump()
    plan = validate_plan(gmail_summary_plan(state), state)
    return dict(draft=draft, confirmed=False, saved_agent_id=None, pending_questions=[],
                issues=[], semantic_issues=[], condition_enforced=False, condition_required=False,
                condition_template=False, condition_ledger=None,
                capability_enforced=True, capability_assessment=gmail_summary_assessment(state),
                capability_fingerprint=input_fingerprint(state), capability_questions=[], capability_deferred=False,
                requirement_required=True, requirement_ledger=plan, requirement_fingerprint=ledger_fingerprint(state),
                requirement_digest=plan_digest(plan), gap_condition_review=None,
                messages=state['messages']+[dict(role='assistant', content=explanation)],
                trace=state.get('trace', [])+[dict(node='gmail_summary', label='메일 요약 설계', status='completed',
                    detail='요청 전체·조회 건수·등록 문서 입출력을 확인해 읽기와 요약을 연결했습니다.', model_calls=0)])
