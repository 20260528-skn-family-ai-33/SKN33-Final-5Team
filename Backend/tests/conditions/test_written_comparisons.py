"""A drafted comparison keeps the operator the user wrote for that number, on every design path.

Live 2026-11-04 (RunPod, PR #27 head 963244f, requirement check off): "amount가 30 이상인 행만" was
drafted as gt 30 in 3 of 8 runs on the full-planner path; the run succeeded and the row with 30 was lost.
"""
import pytest
from agent_builder.conditions.condition_readiness import written_comparison_issues
from agent_builder.design.nodes.validation import validate_node
from tests.design.test_builder import initial, draft as base_draft

TEXT = 'qty와 price를 곱한 amount 열을 추가하고 amount가 30 이상인 행만 남겨서 amount가 큰 순서로 정렬한 뒤 JSON으로 저장해줘.'


def drafted(*filters):
    plan = {'filters': list(filters), 'sort': [], 'match': 'all', 'missing_policy': 'keep', 'duplicate_policy': 'keep',
            'duplicate_field': None, 'output_format': 'json'}
    return {'execution_plan': plan, 'workflow': {'nodes': [{'id': 'f', 'tool': 'filter_sort', 'settings': {'execution_plan': plan}, 'bindings': []}]}}


def flt(op, value, field='amount', compare_field=None):
    return {'field': field, 'operator': op, 'value': value, 'compare_field': compare_field, 'value_type': 'number'}


def state(text=TEXT):
    return {'messages': [{'role': 'user', 'content': text}]}


def test_changed_boundary_is_reported():
    issues = written_comparison_issues(state(), drafted(flt('gt', 30.0)))
    assert issues and "'이상'" in issues[0] and "'초과'" in issues[0]


@pytest.mark.parametrize('f', [flt('gte', 30.0), flt('gte', 30), flt('gt', 15.0), flt('gt', 30.0, compare_field='qty'),
                               flt('eq', 30.0), flt('not_empty', None)])
def test_matching_computed_or_non_numeric_filters_pass(f):
    assert written_comparison_issues(state(), drafted(f)) == []


def test_units_are_compared_as_written_amounts():
    s = state('비용이 100만원 이하인 행만 저장해줘.')
    assert written_comparison_issues(s, drafted(flt('lt', 1000000.0, field='비용')))
    assert written_comparison_issues(s, drafted(flt('lte', 1000000.0, field='비용'))) == []


def test_dropped_range_and_requests_without_numbers_are_left_alone():
    assert written_comparison_issues(state('비용 100만원 미만인 행은 빼고 저장해줘.'), drafted(flt('gte', 1000000.0, field='비용'))) == []
    assert written_comparison_issues(state('큰 거래만 저장해줘.'), drafted(flt('gt', 30.0))) == []


def test_validate_node_blocks_the_draft():
    s = {**initial(), 'messages': state()['messages'], 'resources': []}
    d = base_draft(); d['execution_plan'] = drafted(flt('gt', 30.0))['execution_plan']
    s['draft'] = d
    assert any('비교 방식' in i for i in validate_node(s)['issues'])


def test_unique_written_operator_is_applied_in_code():
    from agent_builder.conditions.condition_readiness import canonical_written_operators
    fixed, changes = canonical_written_operators(state(), drafted(flt('gt', 30.0)))
    assert changes == ['amount 30.0: 초과→이상']
    assert fixed['execution_plan']['filters'][0]['operator'] == 'gte'
    assert fixed['workflow']['nodes'][0]['settings']['execution_plan']['filters'][0]['operator'] == 'gte'
    assert written_comparison_issues(state(), fixed) == []


def test_ambiguous_or_unwritten_numbers_are_not_changed():
    from agent_builder.conditions.condition_readiness import canonical_written_operators
    two = state('amount가 30 이상이거나 30 미만인 행')
    assert canonical_written_operators(two, drafted(flt('gt', 30.0))) == (None, [])
    assert written_comparison_issues(two, drafted(flt('gt', 30.0)))
    assert canonical_written_operators(state(), drafted(flt('gt', 15.0))) == (None, [])


def test_audit_corrects_the_planner_draft_without_a_condition_ledger():
    from agent_builder.conditions.condition_check import audit_conditions
    s = {**state(), 'draft': drafted(flt('gt', 30.0)), 'trace': []}
    update = audit_conditions(s)
    assert update['draft']['execution_plan']['filters'][0]['operator'] == 'gte'
    assert '초과→이상' in update['trace'][-1]['detail']
    assert audit_conditions({**state(), 'draft': drafted(flt('gte', 30.0)), 'trace': []}) == {}
