"""Offline DEVELOPMENT reproductions from the public 9B baseline.

These fixtures are contract regressions, not additional model evaluation scores.
The scalar cases exercise a narrow literal-expression requirement contract while
the existing canonical operation validator continues to own execution safety.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_coverage import coverage_report
from agent_builder.design.requirement_generation import generation_requirements
from agent_builder.design.requirement_ledger import (
    extract_plan, grounded_schedule, ledger_fingerprint, make_requirement_check,
    plan_digest,
)
from contracts.models import AgentDraft
from tests.design.test_builder import draft, initial
from tests.workflow_engine.test_workflow import node
from workflow_engine.workflow import workflow_errors


def request_state(text):
    return {**initial(), 'messages': [{'role': 'user', 'content': text}]}


def extraction(*specs):
    return {'tasks': [dict(source_message=0, description='요청 업무', spec=spec)
                      for spec in specs], 'unresolved': []}


class Responses:
    """Deterministic extraction responses; this never creates a provider client."""

    def __init__(self, *responses):
        self.responses = responses
        self.calls = []

    def invoke(self, messages):
        self.calls.append(deepcopy(messages))
        return {'content': json.dumps(self.responses[len(self.calls) - 1], ensure_ascii=False)}


@pytest.mark.parametrize('zone', ['UTC', 'America/New_York'])
def test_c01_request_timezone_preserves_fixed_seoul_schedule_and_written_clock(zone):
    state = request_state('매일 오전 9시에 보고서를 만들어줘.')
    state['user_timezone'] = zone
    schedule = grounded_schedule(state)
    assert (schedule['frequency'], schedule['timezone'], schedule['hour'], schedule['minute']) == ('daily', 'Asia/Seoul', 9, 0)
    plan = extract_plan(extraction({'kind': 'schedule', 'schedule': schedule}), state)
    assert plan['tasks'][0]['spec']['schedule'] == schedule
    assert state['user_timezone'] == zone


@pytest.mark.parametrize('zone', [None, '', 'invalid-zone'])
def test_c01_unknown_request_timezone_uses_fixed_seoul_instead_of_process_default(monkeypatch, zone):
    monkeypatch.setenv('A4B_DEFAULT_TIMEZONE', 'UTC')
    state = request_state('매일 오전 9시에 보고서를 만들어줘.')
    state['user_timezone'] = zone
    schedule = grounded_schedule(state)
    assert (schedule['frequency'], schedule['timezone'], schedule['hour'], schedule['minute']) == ('daily', 'Asia/Seoul', 9, 0)
    assert state['user_timezone'] == zone


def test_c01_known_timezone_does_not_supply_an_unwritten_execution_time():
    state = request_state('매일 보고서를 만들어줘.')
    state['user_timezone'] = 'Asia/Seoul'
    with pytest.raises(ValueError):
        grounded_schedule(state)


@pytest.mark.parametrize('user_request', [
    '한국 시간과 UTC 기준으로 매일 오전 9시에 보고서를 만들어줘.',
    'Invalid/Zone 시간대로 매일 오전 9시에 보고서를 만들어줘.',
])
def test_c01_conflicting_or_invalid_explicit_zone_preserves_fixed_seoul_and_written_clock(user_request):
    state = request_state(user_request)
    state['user_timezone'] = 'Asia/Seoul'
    schedule = grounded_schedule(state)
    assert (schedule['frequency'], schedule['timezone'], schedule['hour'], schedule['minute']) == ('daily', 'Asia/Seoul', 9, 0)
    assert state['messages'][0]['content'] == user_request


def test_c01_timezone_edits_preserve_fixed_seoul_and_written_execution_time():
    state = request_state('한국 시간으로 매일 오전 9시에 보고서를 만들어줘.')
    state['user_timezone'] = 'Asia/Seoul'
    original = grounded_schedule(state)
    state['messages'].append(dict(role='user', content='Invalid/Zone 시간대로 바꿔줘.'))
    assert grounded_schedule(state) == original
    state['messages'].append(dict(role='user', content='UTC 시간대로 해줘.'))
    schedule = grounded_schedule(state)
    assert schedule == original
    assert (schedule['frequency'], schedule['timezone'], schedule['hour'], schedule['minute']) == ('daily', 'Asia/Seoul', 9, 0)


def test_r07_invalid_rename_formula_can_repair_to_column_rename():
    state = request_state('연결한 표의 amount 열 이름을 revenue로 바꾸고 JSON으로 저장해줘.')
    state['resources'] = [dict(id='synthetic-sales', name='매출 표', kind='table', columns=['amount'])]
    invalid = extraction(
        dict(kind='compute', formula="rename(source='synthetic-sales', target='revenue')", output_column='revenue'),
        dict(kind='export', format='json'),
    )
    correct = extraction(dict(kind='rename', source='amount', target='revenue'),
                         dict(kind='export', format='json'))
    transport = Responses(invalid, correct)
    result = make_requirement_check('fixture', transport, mode='enforce')(state)
    assert len(transport.calls) == 2
    assert result['requirement_route'] == 'plan'
    tasks = result['requirement_ledger']['tasks']
    assert tasks[0]['spec'] == dict(kind='rename', source='amount', target='revenue')
    assert tasks[1]['depends_on'] == [tasks[0]['id']]
    assert tasks[1]['spec'] == dict(kind='export', format='json')
    assert result['trace'][-1]['model_calls'] == 2


def test_r07_repeated_invalid_rename_formula_does_not_become_ready():
    state = request_state('amount 열 이름을 revenue로 바꿔줘.')
    invalid = extraction(dict(kind='compute', formula='rename(amount, revenue)', output_column='revenue'))
    transport = Responses(invalid, invalid)
    result = make_requirement_check('fixture', transport, mode='enforce')(state)
    assert len(transport.calls) == 2
    assert result['requirement_route'] == 'stop'
    assert result['requirement_ledger'] is None


def scalar_state(formula='(12 + 8) * 3', variables=None, prefix=''):
    variables = {} if variables is None else variables
    request = prefix + formula + '을 계산해서 결과를 보여줘.'
    state = request_state(request)
    raw_plan = extraction(dict(kind='scalar_calculate', formula=formula, variables=variables))
    ledger = extract_plan(raw_plan, state)
    calculation = node('calc', 'scalar_calculate', operation='evaluate',
                       arguments_json=json.dumps(dict(formula=formula, variables=variables)))
    raw = {**draft(), 'name': '산술 결과 확인', 'purpose': request, 'instructions': request,
           'tool_ids': ['scalar_calculate'], 'document_ids': [],
           'requirements': [dict(description='산술식 계산', source_id='user', quote=request)],
           'workflow': dict(entry='calc', nodes=[calculation], edges=[])}
    state.update(requirement_required=True, requirement_ledger=ledger,
                 draft=AgentDraft.model_validate(raw).model_dump())
    state.update(requirement_fingerprint=ledger_fingerprint(state), requirement_digest=plan_digest(ledger))
    return state


@pytest.mark.parametrize('formula', ['(12 + 8) * 3', '(7 - 2) / 5', '2.5 * -4'])
def test_r10_literal_scalar_requirement_matches_registered_operation(formula):
    state = scalar_state(formula)
    candidate = state['draft']
    assert workflow_errors(candidate['workflow'], candidate['tool_ids'], []) == []
    report = coverage_report(state, candidate)
    assert report['issues'] == []
    assert report['tasks'][0]['status'] == 'covered'
    assert report['tasks'][0]['node_ids'] == ['calc']
    assert validate_node(state)['status'] == 'ready'


@pytest.mark.parametrize('formula', ['(12 + 8) + 3', '12 + 8 * 3', '(12 + 8) * 4'])
def test_r10_scalar_extraction_cannot_change_requested_arithmetic(formula):
    state = request_state('(12 + 8) * 3을 계산해서 결과를 보여줘.')
    with pytest.raises(ValueError):
        extract_plan(extraction(dict(kind='scalar_calculate', formula=formula, variables={})), state)


def test_r10_scalar_extraction_cannot_invent_variable_assignments():
    state = request_state('x * 3을 계산해서 결과를 보여줘.')
    with pytest.raises(ValueError):
        extract_plan(extraction(dict(kind='scalar_calculate', formula='x * 3', variables={'x': 12})), state)


@pytest.mark.parametrize('user_request,formula,variables', [
    ('표의 qty * price를 계산한 amount 열을 만들어줘.', 'qty * price', {'qty': 2, 'price': 3}),
    ('표의 각 행에 (12 + 8) * 3을 계산한 result 열을 만들어줘.', '(12 + 8) * 3', {}),
])
def test_r10_table_column_calculation_is_not_a_scalar_requirement(user_request, formula, variables):
    state = request_state(user_request)
    state['resources'] = [dict(id='sales', name='매출 표', kind='table', columns=['qty', 'price'])]
    with pytest.raises(ValueError):
        extract_plan(extraction(dict(kind='scalar_calculate', formula=formula, variables=variables)), state)


@pytest.mark.parametrize('actual_formula', ['(12 + 8) + 3', '12 + 8 * 3'])
def test_r10_coverage_rejects_valid_operation_with_wrong_arithmetic(actual_formula):
    state = scalar_state()
    state['draft']['workflow']['nodes'][0]['settings']['arguments_json'] = json.dumps(
        dict(formula=actual_formula, variables={}))
    assert workflow_errors(state['draft']['workflow'], ['scalar_calculate'], []) == []
    assert coverage_report(state, state['draft'])['issues']
    assert validate_node(state)['status'] != 'ready'


def test_r10_scalar_coverage_does_not_accept_a_table_compute_node():
    state = scalar_state()
    state['draft']['workflow']['nodes'] = [node('calc', 'calculate_columns',
        table_operation='compute', calculation_formula='(12 + 8) * 3', output_column='result')]
    state['draft']['tool_ids'] = ['calculate_columns']
    assert coverage_report(state, state['draft'])['issues']


def test_r10_coverage_does_not_accept_additional_unused_scalar_values():
    state = scalar_state()
    state['draft']['workflow']['nodes'][0]['settings']['arguments_json'] = json.dumps(
        dict(formula='(12 + 8) * 3', variables={'unrequested': 1}))
    assert coverage_report(state, state['draft'])['issues']


def legacy_v5_scalar_as_table_state():
    """Reproduce a saved pre-fix checkpoint, including its actual contract seal."""
    from agent_builder.capabilities.capability_gate import input_fingerprint

    state = request_state('(12 + 8) * 3을 계산해서 결과를 보여줘.')
    old_plan = {'tasks': [dict(id='r1', source_message=0,
        quote=state['messages'][0]['content'], description='산술식 계산', depends_on=[],
        spec=dict(kind='compute', formula='(12 + 8) * 3', output_column='result'))],
        'unresolved': []}
    old_fingerprint = hashlib.sha256(('a4b.requirements.v5.semantic-selection:'
        + input_fingerprint(state) + (state.get('request_meaning_digest') or '')).encode()).hexdigest()
    state.update(requirement_required=True, requirement_ledger=old_plan,
                 requirement_fingerprint=old_fingerprint, requirement_digest=plan_digest(old_plan))
    return state


def test_current_valid_requirement_cache_skips_extraction():
    state = request_state('amount 열 이름을 revenue로 바꿔줘.')
    ledger = extract_plan(extraction(dict(kind='rename', source='amount', target='revenue')), state)
    state.update(requirement_required=True, requirement_ledger=ledger,
                 requirement_fingerprint=ledger_fingerprint(state), requirement_digest=plan_digest(ledger))
    transport = Responses()
    result = make_requirement_check('fixture', transport, mode='enforce')(state)
    assert result['requirement_route'] == 'plan'
    assert transport.calls == []
    assert state['requirement_ledger'] == ledger


def test_r10_previous_contract_cache_reextracts_wrong_table_requirement():
    state = legacy_v5_scalar_as_table_state()
    transport = Responses(extraction(dict(kind='scalar_calculate', formula='(12 + 8) * 3', variables={})))
    result = make_requirement_check('fixture', transport, mode='enforce')(state)
    assert len(transport.calls) == 1
    assert result['requirement_route'] == 'plan'
    assert result['requirement_ledger']['tasks'][0]['spec']['kind'] == 'scalar_calculate'
    assert result['requirement_fingerprint'] != state['requirement_fingerprint']


def test_previous_contract_cache_cannot_constrain_generation_or_pass_coverage():
    state = legacy_v5_scalar_as_table_state()
    assert generation_requirements(state) == ([], [])
    report = coverage_report(state, {'workflow': {'nodes': [], 'edges': []}})
    assert report['issues']
    assert report['tasks'] == []


@pytest.mark.parametrize('prefix,formula,variables', [
    ('x=7일 때 ', 'x * 3', {'x': 7}),
    ('price=2.5, count=4일 때 ', 'price * count', {'price': 2.5, 'count': 4}),
])
def test_scalar_explicit_numeric_variables_reach_existing_validator(prefix, formula, variables):
    state = scalar_state(formula, variables, prefix)
    assert coverage_report(state, state['draft'])['issues'] == []
    assert validate_node(state)['status'] == 'ready'


@pytest.mark.parametrize('user_request,formula,variables', [
    ('x=2+3일 때 x * 4를 계산해줘.', 'x * 4', {'x': 2}),
    ('x=7일 때 x * 3을 계산해줘.', 'x * 3', {'x': 8}),
    ('x=7은 취소하고 x * 3을 계산해줘.', 'x * 3', {'x': 7}),
    ('(12 + 8) * 3 대신 2 + 3을 계산해줘.', '(12 + 8) * 3', {}),
    ('(12 + 8) * 3 + 4를 계산해줘.', '(12 + 8) * 3', {}),
])
def test_scalar_grounding_does_not_accept_partial_or_replaced_values(user_request, formula, variables):
    with pytest.raises(ValueError):
        extract_plan(extraction(dict(kind='scalar_calculate', formula=formula, variables=variables)),
                     request_state(user_request))


def test_scalar_does_not_reuse_an_earlier_table_column_assignment():
    state = request_state('x * 3을 계산해줘.')
    state['messages'].insert(0, dict(role='user', content='표의 x 열을 x=7로 채워줘.'))
    raw = extraction(dict(kind='scalar_calculate', formula='x * 3', variables={'x': 7}))
    raw['tasks'][0]['source_message'] = 1
    with pytest.raises(ValueError):
        extract_plan(raw, state)


@pytest.mark.parametrize('user_request,formula,variables', [
    ('x=3일 때 x * 2를 계산해줘.', '3', {}),
    ('x=3일 때 x * 2를 계산해줘.', 'x', {'x': 3}),
    ('설명에 나온 숫자는 3이고 실제로 2 + 4를 계산해줘.', '3', {}),
])
def test_scalar_does_not_promote_assignment_or_description_atoms_to_requested_expression(user_request, formula, variables):
    with pytest.raises(ValueError):
        extract_plan(extraction(dict(kind='scalar_calculate', formula=formula, variables=variables)),
                     request_state(user_request))


@pytest.mark.parametrize('formula', ['3', '-3', '(3)'])
def test_explicit_single_constant_calculation_remains_supported(formula):
    state = scalar_state(formula)
    assert validate_node(state)['status'] == 'ready'
