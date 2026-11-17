"""Exercise extraction, constrained generation, decoding, coverage, save and real rows."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from tests.conditions.test_clarification_choices import compound_state, confirmed_single, judge, PARTIAL, BASE
from tests.design.test_condition_requirement_ownership import packet
from tests.design.test_beta_feedback import native_schema
from agent_builder.conditions.condition_readiness import compound_filter_state
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from agent_builder.design.requirement_generation import constrain_generation, constrain_ordered_steps
from agent_builder.design.condition_templates import compile_conditions
from agent_builder.design.operation_steps import schema_for, compile_workflow
from agent_builder.design.nodes.validation import validate_node
from infrastructure.persistence.storage import Storage
from workflow_engine.runner import run_workflow


def seal(s, values=None):
    for asset in s['resources']:
        asset.update(format='csv', profile={field: {'types': ['number']} for field in asset['columns'] if field != '상태'})
    l = judge(s, values or {**PARTIAL, 'output.format': ('json', 'JSON'),
                           'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')})
    assert l['decision'] == 'ready', l['questions']
    s.update(condition_ledger=l, condition_enforced=True, condition_required=True, pending_questions=[])
    # The extractor omits every predicate; production ownership must restore them.
    ledger = extract_plan(packet(dict(kind='export', format='json')), s)
    s.update(requirement_required=True, requirement_ledger=ledger)
    s['requirement_fingerprint'] = ledger_fingerprint(s)
    s['requirement_digest'] = plan_digest(ledger)
    return s


def generate(s):
    bounded = constrain_ordered_steps(schema_for(constrain_generation(native_schema(), s)), s)
    # A deterministic producer stands in for the model; every byte still passes the
    # actual ordered generation schema and decoder, rather than bypassing them.
    proposed = compile_conditions(s)['draft']
    nodes = proposed['workflow']['nodes']
    indices = {node['id']: index for index, node in enumerate(nodes, 1)}
    steps = [dict(tool=node['tool'], label=node['label'], settings=deepcopy(node['settings']),
                  bindings=[dict(input=b['input'], step=indices[b['node_id']], output=b['output'])
                            for b in node['bindings']]) for node in nodes]
    first = steps[0]; first.pop('bindings')
    wire = dict(first_step=first, next_steps={'step_' + str(index): step for index, step in enumerate(steps[1:], 2)})
    validator = Draft202012Validator({'$ref': '#/$defs/WorkflowDefinition', '$defs': bounded['$defs']})
    validator.validate(wire)
    if len(nodes) == 3:
        for mutation in ('drop', 'relation'):
            bad = deepcopy(wire)
            plan = bad['next_steps']['step_2']['settings']['execution_plan']
            if mutation == 'drop':
                plan['filters'] = plan['filters'][:-1]
            else:
                plan['match'] = 'any' if plan['match'] == 'all' else 'all'
            assert not validator.is_valid(bad)
    proposed['workflow'] = compile_workflow(wire)
    s.update(draft=proposed, confirmed=True)
    result = validate_node(s)
    assert result['status'] == 'ready', result['issues']
    s['draft'] = result['draft']
    return s


@pytest.mark.parametrize(('request_text', 'expected'), [
    ('비용이 10원 이상이고 수량이 3개 이상인 행', [(15, 4)]),
    ('비용이 10원 이상이면서 수량이 3개 이상인 행', [(15, 4)]),
    ('비용이 10원보다 적은 행 또는 수량이 3개보다 적은 행', [(15, 1), (5, 4)]),
    ('비용이 10원 이상이고 수량이 3개 미만인 행', [(15, 1)]),
])
def test_compound_extraction_generation_and_execution(tmp_path, request_text, expected):
    s = compound_state(request_text + '을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    s = generate(seal(s))
    rows = [{'비용': a, '수량': b} for a, b in [(15, 1), (15, 4), (5, 4)]]
    with_store(s, rows, expected, tmp_path)


def with_store(s, rows, expected, path):
    store = Storage(path)
    try:
        store.add_asset(s['resources'][0], rows)
        identifier = store.save_agent(s)
        result = run_workflow(store, identifier, '실행', '', file_ids=[s['resources'][0]['id']])
        assert result['status'] == 'succeeded'
        assert [(row['비용'], row['수량']) for row in result['rows']] == expected
    finally:
        store.close()


@pytest.mark.parametrize('numeric', [
    '비용이 10원 이상이고 수량이 3개 이상인 행',
    '비용이 10원 미만이거나 수량이 3개 미만인 행',
])
def test_explicit_text_condition_is_generated_and_enforced(tmp_path, numeric):
    s = compound_state(numeric + '. 그리고 상태가 완료인 행만 남겨줘. JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    s['resources'][0]['columns'].append('상태')
    s = generate(seal(s))
    rows = [dict(비용=a, 수량=b, 상태=status) for a, b, status in [
        (15, 4, '완료'), (15, 1, '완료'), (5, 4, '완료'), (5, 4, '대기'), (15, 4, '대기')]]
    expected = [(15, 4)] if '이상' in numeric else [(15, 1), (5, 4)]
    with_store(s, rows, expected, tmp_path)


def test_added_group_preserves_previous_conditions_in_generation():
    s = compound_state(BASE + '. 빈 값은 오류, 중복은 유지해.')
    s['resources'][0]['columns'].append('적립금')
    seal(s)
    s['messages'].append(dict(role='user', content='할인이 5원 이상이고 적립금이 100원 이상인 조건도 추가해줘'))
    found = compound_filter_state(s)
    assert {r['field'] for r in found['rules']} == {'비용', '수량', '할인', '적립금'}
    s = generate(seal(s))
    filters = next(t['spec']['filters'] for t in s['requirement_ledger']['tasks'] if t['spec']['kind'] == 'filter')
    assert len(filters) == 4


def test_schedule_condition_does_not_reopen_numeric_filter():
    s = confirmed_single('예약 조건은 매일 오전 9시로 설정해줘')
    assert compound_filter_state(s)['pending'] is None
    l = judge(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('gte', '이상'),
                   'filter.value': ('10', '10원'), 'output.format': ('json', 'JSON')})
    assert 'filter.match' not in l['unresolved']


def test_partial_conditions_across_turns_are_preserved_through_generation(tmp_path):
    s = compound_state('비용이 낮고 수량이 10개보다 많은 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    assert compound_filter_state(s)['pending'] == 'complete'
    s['messages'].append(dict(role='user', content='비용은 5원 미만'))
    s = generate(seal(s, {'filter.field': ('비용', '비용'), 'filter.operator': ('lt', '미만'),
        'filter.value': ('5', '5원'), 'output.format': ('json', 'JSON'),
        'missing.policy': ('error', '오류'), 'duplicate.policy': ('keep', '유지')}))
    with_store(s, [dict(비용=3, 수량=1), dict(비용=3, 수량=11), dict(비용=7, 수량=11)], [(3, 11)], tmp_path)


def test_adding_a_different_boolean_group_requires_grouping_instead_of_dropping_rules():
    s = compound_state(BASE, '할인이 5원 미만이거나 적립금이 100원 미만인 조건도 추가해줘')
    s['resources'][0]['columns'].append('적립금')
    found = compound_filter_state(s)
    assert found['pending'] == 'rewrite'
    assert {rule['field'] for rule in found['rules']} == {'비용', '수량'}


def test_natural_comparators_on_the_same_column_are_not_reduced_to_the_last_one():
    s = compound_state('비용이 10원보다 큰 행 그리고 비용이 100원보다 작은 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    l = judge(s, {**PARTIAL, 'output.format': ('json', 'JSON')})
    assert l['decision'] != 'ready'
    assert l['slots']['filter.operator']['status'] == 'conflict'


def test_or_and_text_projection_does_not_discard_an_unowned_model_predicate():
    s = compound_state('비용이 10원 미만이거나 수량이 3개 미만인 행. 그리고 상태가 완료인 행만 남겨줘. JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    s['resources'][0]['columns'].append('상태')
    seal(s)
    wrong = dict(field='할인', operator='gt', value=999, compare_field=None, value_type='number')
    with pytest.raises(ValueError, match='추가 선별 조건'):
        extract_plan(packet(dict(kind='filter', filters=[wrong], match='all')), s)


def test_ambiguous_text_amendment_returns_validation_issues_instead_of_raising():
    from agent_builder.design.numeric_filter_proof import numeric_filter_issues
    from tests.conditions.test_clarification_choices import filter_draft
    s = compound_state(BASE + '. 상태가 완료인 행만 남겨줘.', '상태 기준은 나중에 정할게')
    s['resources'][0]['columns'].append('상태')
    rules = compound_filter_state(s)['rules']
    assert numeric_filter_issues(s, filter_draft(s, rules, 'all'))
