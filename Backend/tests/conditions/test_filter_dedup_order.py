"""Issue #43: selection and duplicate retention do not commute."""
from copy import deepcopy
import json

import pytest

from agent_builder.capabilities.capability_gate import require_save_ready
from agent_builder.conditions.condition_check import audit_conditions
from agent_builder.design.condition_templates import compile_conditions
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.table_step_order import filter_dedup_order
from infrastructure.persistence.storage import Storage
from tests.conditions.test_condition_readiness import checked, proposal, state, workflow_draft
from tests.workflow_engine.test_workflow import bind, edge, node
from workflow_engine.runner import run_workflow


FILTER_FIRST = '비용 100만원 초과만 선별한 뒤 거래ID별 마지막 행을 선택해 JSON으로 저장해줘. 빈 비용은 오류.'
DEDUP_FIRST = '거래ID별 마지막 행을 선택한 뒤 비용 100만원 초과만 선별해 JSON으로 저장해줘. 빈 비용은 오류.'
ROWS = [{'거래ID': 'A', '비용': 2000000}, {'거래ID': 'A', '비용': 100}]


def session(request=FILTER_FIRST):
    s = state(request)
    s['resources'] = [dict(id='table', name='거래.csv', kind='table', columns=['거래ID', '비용'], profile={})]
    s['condition_ledger'] = checked(s, proposal(s, {
        'filter.field': '비용', 'filter.operator': 'gt', 'filter.value': '1000000',
        'duplicate.field': '거래ID', 'duplicate.policy': 'last',
        'missing.policy': 'error', 'output.format': 'json',
    }))
    assert s['condition_ledger']['decision'] == 'ready'
    s.update(condition_enforced=True, confirmed=True)
    return s


def draft_for(s, order='merged'):
    d = workflow_draft(s)
    plan = d['execution_plan']
    plan.update(duplicate_policy='last', duplicate_field='거래ID')
    d['document_ids'] = ['table']
    nodes = [node('read', 'read_table', file_ids=['table'])]
    if order == 'merged':
        steps = [('apply', plan)]
    else:
        steps = [('filter', {**plan, 'duplicate_policy': 'keep', 'duplicate_field': None}),
                 ('dedup', {**plan, 'filters': [], 'missing_policy': 'keep'})]
        if order == 'dedup_first':
            steps.reverse()
    for identifier, step_plan in steps:
        nodes.append(node(identifier, 'filter_sort', [bind('table', nodes[-1]['id'], 'table')],
                          execution_plan=deepcopy(step_plan)))
    nodes.append(node('out', 'write_result', [bind('table', nodes[-1]['id'], 'table')], format='json'))
    d['workflow'] = dict(entry='read', nodes=nodes,
                         edges=[edge(a['id'], b['id']) for a, b in zip(nodes, nodes[1:])])
    return d


def requirements(s, filter_first=True):
    specs = [dict(kind='filter', filters=deepcopy(workflow_draft(s)['execution_plan']['filters']), match='all'),
             dict(kind='policy', missing_policy='error', duplicate_policy='last', duplicate_field='거래ID')]
    if not filter_first:
        specs.reverse()
    specs.append(dict(kind='export', format='json'))
    return dict(tasks=[dict(id=f'r{i+1}', description=spec['kind'], spec=spec, source_message=0,
                            quote=s['messages'][0]['content'], depends_on=[f'r{i}'] if i else [])
                       for i, spec in enumerate(specs)], unresolved=[])


def seal(s, ledger):
    s.update(requirement_required=True, requirement_ledger=ledger,
             requirement_fingerprint=ledger_fingerprint(s), requirement_digest=plan_digest(ledger))


def test_requirement_extraction_preserves_deduplication_dependency():
    s = session()
    raw = requirements(s)
    for task in raw['tasks']:
        for key in ('id', 'depends_on', 'quote'):
            task.pop(key)
    ledger = extract_plan(raw, s)
    assert [t['depends_on'] for t in ledger['tasks']] == [[], ['r1'], ['r2']]


@pytest.mark.parametrize('order', ['merged', 'dedup_first'])
@pytest.mark.parametrize('enforced', [True, False])
def test_validation_and_save_reject_reversed_order_without_requirement_mode(order, enforced, monkeypatch):
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    s = session()
    s['condition_enforced'] = enforced
    s['draft'] = draft_for(s, order)
    assert validate_node(s)['status'] != 'ready'
    with pytest.raises(ValueError, match='순서'):
        require_save_ready(s)


def test_requirement_coverage_checks_order_inside_a_shared_node():
    s = session()
    seal(s, requirements(s))
    assert coverage_report(s, draft_for(s))['issues']
    assert not coverage_report(s, draft_for(s, 'filter_first'))['issues']
    reverse = session(DEDUP_FIRST)
    seal(reverse, requirements(reverse, filter_first=False))
    assert not coverage_report(reverse, draft_for(reverse))['issues']


@pytest.mark.parametrize(('prompt', 'expected'), [(FILTER_FIRST, ROWS[:1]), (DEDUP_FIRST, [])])
@pytest.mark.parametrize('requirement_mode', [False, True])
def test_template_audit_save_and_execution_preserve_requested_order(tmp_path, prompt, expected, requirement_mode):
    s = session(prompt)
    if requirement_mode:
        seal(s, requirements(s, filter_first=prompt == FILTER_FIRST))
    s.update(compile_conditions(s))
    s.update(audit_conditions(s))
    validated = validate_node(s)
    assert validated['issues'] == []
    assert validated['status'] == 'ready'
    s.update(validated, confirmed=True)
    store = Storage(tmp_path)
    try:
        store.add_asset(s['resources'][0], ROWS)
        identifier = store.save_agent(s)
        result = run_workflow(store, identifier, '실행', '', file_ids=['table'])
        assert result['status'] == 'succeeded'
        assert result['rows'] == expected
        assert json.loads(result['artifact']['content']) == expected
    finally:
        store.close()


def test_bound_generation_reserves_a_separate_deduplication_step():
    from agent_builder.design.requirement_generation import generation_requirements
    s = session()
    seal(s, requirements(s))
    assert generation_requirements(s)[1] == ['read_table', 'filter_sort', 'filter_sort', 'write_result']


def test_sealed_dependencies_support_ordering_without_literal_sequence_words():
    s = session('비용 100만원 초과인 행 중에서 거래ID별 마지막 행을 JSON으로 저장해줘. 빈 비용은 오류.')
    seal(s, requirements(s))
    assert filter_dedup_order(s) == 'filter_first'
    assert validate_node({**s, 'draft': draft_for(s)})['status'] != 'ready'
    s.update(compile_conditions(s))
    assert validate_node(s)['status'] == 'ready'


def test_data_binding_cannot_bypass_the_filter_even_with_correct_edges():
    s = session()
    d = draft_for(s, 'filter_first')
    d['workflow']['nodes'][2]['bindings'] = [bind('table', 'read', 'table')]
    assert any('순서' in issue for issue in validate_node({**s, 'draft': d})['issues'])


def test_new_task_and_quoted_examples_do_not_reuse_old_order():
    s = session()
    s['messages'].extend([dict(role='assistant', content='설계했습니다.'),
                          dict(role='user', content='새 작업: 고객 문의를 검수해줘.')])
    assert filter_dedup_order(s) is None
    s['messages'] = [dict(role='user', content=f'예시 문장은 “{FILTER_FIRST}”야. 파일을 읽어줘.')]
    assert filter_dedup_order(s) is None


@pytest.mark.parametrize('sequence', ['선별한 뒤', '선별한 후', '선별한 다음', '선별하고 나서'])
def test_explicit_sequence_is_preserved_across_unrelated_followups(sequence):
    s = session(FILTER_FIRST.replace('선별한 뒤', sequence))
    s['messages'].extend([dict(role='assistant', content='조건을 반영했습니다.'),
                          dict(role='user', content='그리고 JSON으로 저장해줘.')])
    assert filter_dedup_order(s) == 'filter_first'


def test_latest_explicit_order_replaces_the_previous_order():
    s = session()
    s['messages'].append(dict(role='user', content='아니, ' + DEDUP_FIRST))
    assert filter_dedup_order(s) == 'dedup_first'
    assert validate_node({**s, 'draft': draft_for(s)})['status'] == 'ready'


def test_uncertain_order_is_not_guessed():
    s = session('비용 100만원 초과 선별 후 중복 제거 또는 중복 제거 후 선별. 거래ID 마지막 행. 빈 비용은 오류. JSON으로 저장.')
    assert filter_dedup_order(s) == 'ambiguous'
    assert validate_node({**s, 'draft': draft_for(s)})['status'] != 'ready'


@pytest.mark.parametrize('prompt', ['선별 후보와 중복 항목을 확인해줘.', '필터 뒤집기와 중복 제거를 검토해줘.'])
def test_words_containing_sequence_syllables_do_not_set_an_order(prompt):
    assert filter_dedup_order(state(prompt)) is None


def test_direct_planner_can_generate_distinct_filter_and_dedup_plans(monkeypatch):
    from agent_builder.design.nodes.direct_conversation import make_direct_conversation
    from tests.design.test_direct_conversation import Fake
    jsonschema = pytest.importorskip('jsonschema')
    monkeypatch.setenv('A4B_PLANNER_PROMPT', 'v8')
    s = session()
    candidate = draft_for(s, 'filter_first')
    transport = Fake(candidate)
    result = make_direct_conversation('', 'fixture', model_factory=lambda: transport)(s)
    assert validate_node({**s, **result})['status'] == 'ready'
    validator = jsonschema.Draft202012Validator({'$ref': '#/$defs/ExecutionPlan', '$defs': transport.schema['$defs']})
    for n in candidate['workflow']['nodes'][1:3]:
        validator.validate(n['settings']['execution_plan'])


def test_deep_planner_schema_allows_two_independent_transform_nodes(monkeypatch):
    from agent_builder.design.nodes import conversation
    jsonschema = pytest.importorskip('jsonschema')
    s = session()
    s['node_library'] = []
    captured = {}
    class Captured(Exception):
        pass
    def capture(**kwargs):
        captured.update(kwargs['response_format'].schema)
        raise Captured()
    monkeypatch.setattr(conversation, 'create_deep_agent', capture)
    with pytest.raises(Captured):
        conversation.make_conversation('', 'fixture', lambda: object())(s)
    validator = jsonschema.Draft202012Validator({'$ref': '#/$defs/ExecutionPlan', '$defs': captured['$defs']})
    for n in draft_for(s, 'filter_first')['workflow']['nodes'][1:3]:
        validator.validate(n['settings']['execution_plan'])
    native = next(v for v in captured['$defs']['WorkflowNode']['anyOf']
                  if v['properties']['tool']['enum'] == ['filter_sort'])
    assert 'enum' not in native['properties']['id']
