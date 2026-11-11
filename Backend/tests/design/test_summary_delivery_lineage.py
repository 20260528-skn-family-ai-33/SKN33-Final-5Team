import json

import pytest

from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from tests.design.test_builder import draft as draft_fixture
from tests.design.test_summary_consumers import REQUEST, packet, source
from tests.workflow_engine.test_workflow import bind, edge, node


@pytest.mark.parametrize('rewrite_for, export_input, send_input, ready', [
    (None, 'text', 'body', True),
    (None, 'answer', 'body', True),
    ('mail', 'text', 'body', False),
    ('file', 'text', 'body', False),
    (None, 'text', 'to', False),
])
def test_summary_consumers_require_the_same_direct_output(rewrite_for, export_input, send_input, ready):
    state = source()
    plan = extract_plan(packet(), state)
    state.update(requirement_required=True, requirement_ledger=plan, requirement_fingerprint=ledger_fingerprint(state),
                 requirement_digest=plan_digest(plan))
    original = 'briefing'
    nodes = [
        node('handbook', 'read_document', file_ids=['guide']),
        node(original, 'compose_answer', [bind('documents', 'handbook', 'documents')], instructions=REQUEST),
    ]
    if rewrite_for:
        nodes.append(node('rewrite', 'compose_answer', [bind('documents', 'handbook', 'documents'),
                                                        bind('answer', original, 'text')], instructions='요약을 다시 작성해줘.'))
    nodes.extend([
        node('notify', 'email.send', [bind(send_input, 'rewrite' if rewrite_for == 'mail' else original, 'text')],
             operation='execute', arguments_json=json.dumps({
                 'to': 'day@example.invalid', 'subject': '업무 전달', 'body': {'$binding': send_input},
             })),
        node('download', 'export_result', [bind(export_input, 'rewrite' if rewrite_for == 'file' else original, 'text')],
             format='markdown'),
    ])
    draft = {**draft_fixture(), 'name': '교대 기록 전달', 'document_ids': ['guide'],
             'tool_ids': list(dict.fromkeys(n['tool'] for n in nodes)),
             'requirements': [dict(description='요약 전달', source_id='user', quote=REQUEST)],
             'workflow': dict(entry=nodes[0]['id'], nodes=nodes,
                              edges=[edge(a['id'], b['id']) for a, b in zip(nodes, nodes[1:])])}
    result = validate_node({**state, 'draft': draft})
    assert (result['status'] == 'ready') is ready, result['issues']
    if not ready:
        assert any('요구사항 검증' in issue for issue in result['issues'])
