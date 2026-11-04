"""A repair must not turn a processing request into an approved read-only flow."""
from copy import deepcopy

import pytest

from agent_builder.capabilities.capability_gate import input_fingerprint
from agent_builder.design.nodes.save import make_save_node
from agent_builder.design.nodes.supervisor import supervisor_node
from agent_builder.design.nodes.validation import validate_node
from contracts.models import AgentDraft
from infrastructure.persistence.storage import Storage
from tests.design.test_builder import initial, draft
from tests.workflow_engine.test_workflow import node, bind, edge


def processing_request(nodes=None, edges=None):
    request = '첨부 표의 amount에 2를 곱한 total 열을 만들고 JSON으로 저장해줘.'
    state = initial()
    state['messages'] = [{'role': 'user', 'content': request}]
    raw = draft()
    raw.update(name='매출 계산', instructions=request, purpose=request,
               requirements=[dict(description='계산 후 저장', source_id='user', quote=request)],
               workflow=dict(entry='read', nodes=nodes or [node('read', 'read_table')], edges=edges or []))
    raw['tool_ids'] = list(dict.fromkeys(n['tool'] for n in raw['workflow']['nodes']))
    raw['execution_plan'].update(missing_policy='keep')
    state.update(draft=AgentDraft.model_validate(raw).model_dump(), capability_enforced=True,
                 capability_assessment=dict(decision='existing_tools',
                     tool_ids=['read_table', 'calculate_columns', 'write_result'],
                     missing_conditions=[], missing_actions=[], reason='계산 후 저장 요청', user_quotes=[request]))
    state['capability_fingerprint'] = input_fingerprint(state)
    return state


@pytest.mark.parametrize('save_copy', [False, True])
def test_reading_or_saving_original_table_does_not_fulfil_calculation(save_copy):
    nodes = [node('read', 'read_table')]
    if save_copy:
        nodes.append(node('out', 'write_result', [bind('table', 'read', 'table')], format='json'))
    state = processing_request(nodes, [edge('read', 'out')] if save_copy else [])
    result = validate_node(state)
    assert result['status'] != 'ready'
    assert any('처리 단계' in issue for issue in result['issues'])
    assert supervisor_node({**state, **result})['route'] == 'repair'
    assert supervisor_node({**state, **result, 'repair_count': 2})['route'] == 'invalid'


def test_confirming_an_old_incomplete_draft_cannot_bypass_the_guard(tmp_path):
    state = processing_request()
    # A model pass and a user confirmation cannot override a known missing processing step.
    state.update(confirmed=True, status='ready', verification_enforced=True,
                 verification_route='continue_review')
    store = Storage(tmp_path)
    try:
        with pytest.raises(ValueError):
            make_save_node(store)(state)
        with pytest.raises(ValueError):
            store.save_agent(state)
        assert not store.agents()
    finally:
        store.close()


def test_an_explicit_read_only_request_remains_supported():
    state = processing_request()
    state['messages'] = [{'role': 'user', 'content': '첨부 표를 읽어줘.'}]
    state['draft']['requirements'][0]['quote'] = '첨부 표를 읽어줘.'
    state['capability_assessment']['tool_ids'] = ['read_table']
    state['capability_fingerprint'] = input_fingerprint(state)
    assert validate_node(state)['status'] == 'ready'


def test_actual_processing_is_not_forced_to_use_the_assessed_tool_id():
    nodes = [node('read', 'read_table'), node('calc', 'calculate_columns',
        [bind('table', 'read', 'table')], table_operation='compute',
        calculation_formula='amount * 2', output_column='total')]
    state = processing_request(nodes, [edge('read', 'calc')])
    # The guard detects lost processing, rather than enforcing the assessment as a whitelist.
    state['capability_assessment']['tool_ids'] = ['read_table', 'agent_node']
    assert validate_node(state)['status'] == 'ready'


def test_questions_do_not_claim_a_finished_processing_flow():
    state = processing_request()
    state['draft']['questions'] = ['total 열 이름을 변경할까요?']
    assert validate_node(state)['status'] == 'needs_clarification'


def test_guard_does_not_reclassify_an_absent_or_stale_assessment():
    state = processing_request()
    stale = deepcopy(state)
    stale['messages'].append(dict(role='user', content='계산하지 말고 원문만 보여줘.'))
    assert not any('처리 단계' in x for x in validate_node(stale)['issues'])
    state.pop('capability_assessment')
    state['capability_enforced'] = False
    assert validate_node(state)['status'] == 'ready'
