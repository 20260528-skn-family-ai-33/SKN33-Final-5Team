from copy import deepcopy
import json

import pytest

from agent_builder.design.requirement_ledger import extract_plan, validate_plan
from tests.conditions.test_condition_readiness import state


REQUEST = ('교대 문서를 요약해줘. 그 요약을 Markdown 파일로 저장하고, '
           '같은 요약을 Gmail 본문으로 day@example.invalid 에 제목 “업무 전달”로 보내줘.')


def source(text=REQUEST):
    current = state(text)
    current['resources'] = [dict(id='guide', name='교대.txt', kind='document')]
    return current


def packet():
    return {'tasks': [dict(source_message=0, description='문서 요약', spec=dict(kind='document_analysis'))],
            'unresolved': [dict(key='document_analysis.output_format', text='형식을 알려주세요.'),
                           dict(key='document_analysis.email_recipient', text='수신자를 알려주세요.')]}


@pytest.mark.parametrize(('mode', 'extra', 'calls'), [
    ('enforce', '', 0),
    ('off', '', 1),
    ('enforce', ' 보내기 전에 내 승인을 받아.', 1),
    ('enforce', ' 그리고 Gmail에서 이전 메일도 조회해.', 1),
])
def test_optional_meaning_defers_only_a_fully_owned_request(monkeypatch, mode, extra, calls):
    from agent_builder.design.request_meaning import make_meaning_node, sealed_meaning
    from tests.design.test_request_meaning import Extractor
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', mode)
    current = source(REQUEST + extra)
    transport = Extractor(OSError('ordinary meaning path reached'))
    update = make_meaning_node('fixture', transport=transport)(current)
    assert len(transport.messages) == calls
    assert sealed_meaning({**current, **update}) is None
    if not calls:
        assert update['request_meaning_model_calls'] == 0
        assert update['trace'][-1]['reason'] == 'document_summary_delivery_owned'


def test_two_consumers_bind_to_same_summary_and_questions_are_resolved():
    current = source()
    plan = extract_plan(packet(), current)
    tasks = {t['spec']['kind']: t for t in plan['tasks']}
    assert not plan['unresolved']
    assert tasks['email_send']['spec'] == dict(kind='email_send', to='day@example.invalid', subject='업무 전달', body=None)
    assert tasks['export']['spec']['format'] == 'md'
    assert tasks['export']['depends_on'] == tasks['email_send']['depends_on'] == [tasks['document_analysis']['id']]
    forged = deepcopy(plan)
    next(t for t in forged['tasks'] if t['spec']['kind']=='email_send')['spec']['to'] = 'other@example.invalid'
    with pytest.raises(ValueError):
        validate_plan(forged, current)


@pytest.mark.parametrize('text', [
    REQUEST.replace('보내줘', '보내지 마'),
    REQUEST.replace('같은 요약을', '원문을'),
    REQUEST.replace('day@example.invalid', 'day@example.invalid 와 night@example.invalid'),
    REQUEST + ' 단, 발송을 취소해.',
    REQUEST.replace('교대 문서를', '발송 전에 내 승인을 받고 교대 문서를'),
])
def test_ambiguous_or_unrequested_delivery_is_not_projected(text):
    plan = extract_plan(packet(), source(text))
    assert all(t['spec']['kind'] != 'email_send' for t in plan['tasks'])


def test_reordered_consumers_and_fields_preserve_the_same_summary_dependencies():
    current = source('교대 문서를 요약해줘. 제목 “업무 전달”로 day@example.invalid 에 '
                     '같은 요약을 Gmail 본문으로 보내고, 그 요약을 md로 저장해줘.')
    tasks = {t['spec']['kind']: t for t in extract_plan(packet(), current)['tasks']}
    assert tasks['email_send']['spec']['to'] == 'day@example.invalid'
    assert tasks['email_send']['depends_on'] == tasks['export']['depends_on'] == [tasks['document_analysis']['id']]


def summary_graph():
    from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest
    from tests.workflow_engine.test_workflow import bind, edge, node
    current = source()
    plan = extract_plan(packet(), current)
    current.update(requirement_required=True, requirement_ledger=plan,
                   requirement_fingerprint=ledger_fingerprint(current), requirement_digest=plan_digest(plan))
    nodes = [
        node('source', 'read_document', file_ids=['guide']),
        node('summary', 'compose_answer', [bind('documents', 'source', 'documents')], instructions=REQUEST),
        node('file', 'export_result', [bind('text', 'summary', 'text')], format='markdown'),
        node('send', 'email.send', [bind('body', 'summary', 'text')], operation='execute',
             arguments_json=json.dumps(dict(to='day@example.invalid', subject='업무 전달', body={'$binding': 'body'}))),
    ]
    draft = dict(document_ids=['guide'], workflow=dict(entry='source', nodes=nodes,
                 edges=[edge('source', 'summary'), edge('summary', 'file'), edge('file', 'send')]))
    return current, draft


def test_both_terminal_nodes_really_consume_the_named_document_summary():
    from agent_builder.design.requirement_coverage import coverage_report
    from tests.workflow_engine.test_workflow import bind
    current, draft = summary_graph()
    nodes = draft['workflow']['nodes']
    assert not coverage_report(current, draft)['issues']
    from agent_builder.design.nodes.validation import validate_node
    from tests.design.test_builder import draft as fixture
    full_draft = {**fixture(), **draft, 'tool_ids':[n['tool'] for n in nodes], 'name':'교대 인수인계 요약',
                  'requirements':[dict(description='요약 저장 및 전달', source_id='user', quote=REQUEST)],
                  'purpose':'교대 문서 요약 저장 및 전달', 'instructions':REQUEST}
    checked = validate_node({**current, 'draft':full_draft})
    assert checked['status'] == 'ready', checked['issues']
    nodes[-2]['bindings'] = [bind('text', 'source', 'text')]
    assert coverage_report(current, draft)['issues']
    nodes[-2]['bindings'] = [bind('text', 'summary', 'text')]
    draft['workflow']['edges'].pop()
    assert coverage_report(current, draft)['issues']


@pytest.mark.parametrize('change,repair', [
    ({}, True),
    ({'to': 'conflicting@example.invalid'}, False),
    ({'subject': '다른 제목'}, False),
    ({'bcc': ['archive@example.invalid']}, False),
    ({'body': '별도로 작성된 본문'}, False),
])
def test_summary_projection_preserves_literal_delivery_and_other_effects(change, repair):
    from agent_builder.design.requirement_generation import bind_confirmed_summary
    from tests.workflow_engine.test_workflow import bind
    state, draft = summary_graph()
    send = draft['workflow']['nodes'][-1]
    args = dict(to='day@example.invalid', subject='업무 전달', body='$binding', attachments=None)
    send['settings']['arguments_json'] = json.dumps({**args, **change})
    send['bindings'] = [bind('to', 'summary', 'text'), bind('subject', 'summary', 'text')]
    state['draft'] = draft
    original = deepcopy(draft)
    result = bind_confirmed_summary(state, draft)
    assert draft == original
    if repair:
        sent = result['workflow']['nodes'][-1]
        assert sent['bindings'] == [bind('body', 'summary', 'text')]
        assert json.loads(sent['settings']['arguments_json']) == {
            'to':args['to'], 'subject':args['subject'], 'body':{'$binding':'body'}}
        assert bind_confirmed_summary(state, result) is None
        assert bind_confirmed_summary({**state, 'requirement_digest':'stale'}, draft) is None
    else:
        assert result is None




def test_sealed_summary_send_constrains_only_mail_arguments_and_body_port(monkeypatch):
    from jsonschema import Draft202012Validator
    from contracts.models import AgentDraft
    from agent_builder.design.requirement_generation import constrain_request_settings
    from agent_builder.design.operation_steps import schema_for
    from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest
    from workflow_engine.catalog.canonical import operation_variants

    current = source(REQUEST.replace('day@example.invalid', 'editor@example.invalid').replace('업무 전달', '편집 검토'))
    ledger = extract_plan(packet(), current)
    current.update(requirement_required=True, requirement_ledger=ledger, requirement_digest=plan_digest(ledger),
                   requirement_fingerprint=ledger_fingerprint(current))
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    schema = AgentDraft.model_json_schema()
    variants = operation_variants(schema['$defs']['WorkflowNode'], schema['$defs']['Binding'], {'email.send','read_document'}, [])
    schema['$defs']['WorkflowNode'] = {'anyOf':variants}
    wire = schema_for(constrain_request_settings(schema, current))
    send = next(v for v in wire['$defs']['WorkflowNode']['anyOf'] if v['properties']['tool']['enum'] == ['email.send'])
    assert all(v['properties']['tool']['enum'] != ['email.send'] for v in wire['$defs']['FirstBusinessStep']['anyOf'])
    assert any(v['properties']['tool']['enum'] == ['read_document'] for v in wire['$defs']['WorkflowNode']['anyOf'])
    arguments = Draft202012Validator(send['properties']['settings']['properties']['arguments_json'])
    args = dict(to='editor@example.invalid',subject='편집 검토', body={'$binding':'body'})
    assert arguments.is_valid(args)
    for changes in (dict(to='elsewhere@example.invalid'), dict(subject='다른 제목'), dict(body='$binding'),
                    dict(body={'$binding':'to'}), dict(bcc=['extra@example.invalid'])):
        assert not arguments.is_valid({**args, **changes})
    bindings = Draft202012Validator(send['properties']['bindings'])
    body = dict(input='body', step=3, output='text')
    assert bindings.is_valid([body])
    for invalid in ([], [dict(body,input='to')], [dict(body,output='documents')], [dict(body,input='subject'),body]):
        assert not bindings.is_valid(invalid)
    # An internally rehashed ledger still needs a grounded summary dependency.
    detached = deepcopy(ledger)
    next(t for t in detached['tasks'] if t['spec']['kind'] == 'email_send')['depends_on'] = []
    current.update(requirement_ledger=detached, requirement_digest=plan_digest(detached))
    assert constrain_request_settings(schema, current) == schema
    current.update(requirement_ledger=ledger, requirement_digest=plan_digest(ledger))
    # No old address may be imposed by stale checkpoint data.
    current['requirement_fingerprint'] = 'stale'
    assert constrain_request_settings(schema,current) == schema
