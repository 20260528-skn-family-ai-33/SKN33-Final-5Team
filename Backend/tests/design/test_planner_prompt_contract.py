"""Capture the planner's actual model boundary without running a workflow."""
import json
from copy import deepcopy

import pytest

from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.planner_constraints import TABLE_PLAN_TOOLS
from agent_builder.design.requirement_ledger import ledger_fingerprint, plan_digest
from tests.conditions.test_condition_readiness import state
from tests.design.test_linear_plan import context


def capture(monkeypatch, current):
    seen = {}
    class Captured(Exception):
        pass
    class Agent:
        def invoke(self, payload, config):
            seen['context'] = json.loads(payload['messages'][0]['content'].split('\n', 1)[1])
            raise Captured
    def factory(**options):
        seen['system'] = options['system_prompt']
        return Agent()
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setenv('A4B_PLANNER_MODE', 'deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', factory)
    monkeypatch.setattr('ml.advice.tool_recommendation.recommend_for_state', lambda _: {'advisory_only': True})
    with pytest.raises(Captured):
        make_conversation(None, 'fixture', model_factory=object)(current)
    return seen


@pytest.mark.parametrize('closed', [True, False])
def test_only_closed_table_scope_omits_extension_examples_and_tool_recommendations(monkeypatch, closed):
    current = context()
    current.update(condition_enforced=closed,
                   capability_assessment={'decision':'existing_tools','tool_ids':['filter_sort']})
    result = capture(monkeypatch, current)
    payload = result['context']
    ids = {tool['id'] for tool in payload['supported_tools']}
    assert (ids == set(TABLE_PLAN_TOOLS)) is closed
    assert ('tool_selection_advice' in payload) is not closed
    assert ('json_transform.lookup(path=' in result['system']) is not closed
    assert 'filter_sort' in payload['native_settings_contract']
    assert ('aggregate_table' in payload['native_settings_contract']) is not closed
    assert payload['available_evidence']
    if not closed:
        assert 'email.send' in ids


@pytest.mark.parametrize('closed,verified', [(True, True), (True, False), (False, True)])
def test_task_to_execution_contract_preserves_order_provenance_and_unverified_tasks(monkeypatch, closed, verified):
    current = context() if closed else state('CSV와 JSON으로 저장해줘.')
    tasks = [dict(id='task'+str(n), source_message=0, description=fmt+' 저장', quote='CSV와 JSON으로 저장해줘.',
                  spec=dict(kind='export', format=fmt), depends_on=[]) for n,fmt in enumerate(('csv','json'))]
    ledger = {'tasks': tasks, 'unresolved': []}
    current.update(requirement_required=True, requirement_ledger=ledger,
                   requirement_fingerprint=ledger_fingerprint(current),
                   requirement_digest=plan_digest(ledger) if verified else 'stale')
    original = deepcopy(ledger)
    payload = capture(monkeypatch, current)['context']
    result = payload['request_requirements']['tasks']
    assert ledger == original
    for index, task in enumerate(result):
        assert {k:v for k,v in task.items() if k not in ('spec','execution_contract_index')} == {
            k:v for k,v in original['tasks'][index].items() if k not in ('quote','spec')}
        if verified and closed:
            assert 'spec' not in task
            assert payload['required_execution_contracts']['specs'][task['execution_contract_index']] == tasks[index]['spec']
        else:
            assert ('required_execution_contracts' in payload) is verified
            assert task['spec'] == tasks[index]['spec']


def test_closed_scope_records_a_valid_draft_without_calling_the_adviser(monkeypatch):
    from types import SimpleNamespace
    from tests.conditions.test_condition_readiness import workflow_draft
    current = context()
    current['capability_assessment'] = {'decision':'existing_tools','tool_ids':['filter_sort']}
    response = workflow_draft(current)
    response['requirements'] = [{'description':'사용자 조건','evidence_id':'e1'}]
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setenv('A4B_PLANNER_MODE', 'deepagent')
    def fail(_):
        pytest.fail('The closed scope already enumerates every admissible tool.')
    monkeypatch.setattr('ml.advice.tool_recommendation.recommend_for_state', fail)
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',
                        lambda **kwargs: SimpleNamespace(invoke=lambda *a: {'structured_response':response}))
    result = make_conversation(None, 'fixture', model_factory=object)(current)
    trace = result['trace'][-1]
    assert trace['status'] == 'completed'
    assert trace['tool_advice_status'] == 'off'
    assert trace['tool_advice_candidates'] == []
    assert result['draft']['questions'] == []
