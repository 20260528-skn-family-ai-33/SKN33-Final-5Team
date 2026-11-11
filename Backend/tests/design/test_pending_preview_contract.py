"""Clarification previews survive strict decoding without becoming approved drafts."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from contracts.models import AgentDraft
from contracts.draft_outcome import DraftOutcomeError, outcome_schema, validate_generated_outcome
from agent_builder.capabilities.capability_gate import input_fingerprint
from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.nodes.save import make_save_node
from agent_builder.design.nodes.supervisor import supervisor_node
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest
from infrastructure.persistence.storage import Storage
from tests.design.test_deep_agent_conversation import draft, state
from tests.workflow_engine.test_workflow import node


def graph():
    return {'entry': 'read', 'nodes': [node('read', 'read_table')], 'edges': []}


@pytest.mark.parametrize('preview', [False, True])
def test_strict_clarification_accepts_a_preview_or_no_graph_without_mutation(preview):
    raw = draft(questions=['기준은 얼마인가요?'], workflow=graph() if preview else None,
                question_responses=[{'type': 'number'}])
    raw['requirements'] = []
    before = deepcopy(raw)
    validate_generated_outcome(raw)
    Draft202012Validator(outcome_schema(AgentDraft.model_json_schema())).validate(raw)
    assert raw == before


@pytest.mark.parametrize('workflow,questions,unsupported', [
    (None, [], []), ({}, ['기준은 얼마인가요?'], []),
    (graph(), [], ['전송 도구가 없습니다.']),
    (graph(), ['기준은 얼마인가요?'], ['전송 도구가 없습니다.']),
    (None, ['기준은 얼마인가요?'], ['전송 도구가 없습니다.']),
])
def test_preview_support_does_not_accept_empty_or_conflicting_outcomes(workflow, questions, unsupported):
    raw = draft(questions=questions, unsupported=unsupported, workflow=workflow,
                question_responses=[{'type': 'number'} for _ in questions])
    raw['requirements'] = []
    with pytest.raises(DraftOutcomeError):
        validate_generated_outcome(raw)
    assert not Draft202012Validator(outcome_schema(AgentDraft.model_json_schema())).is_valid(raw)


def test_a_preview_keeps_graph_bounds_and_requires_a_real_question():
    source = AgentDraft.model_json_schema()
    source['$defs']['WorkflowDefinition']['properties']['nodes']['maxItems'] = 1
    raw = draft(questions=['기준은 얼마인가요?'], workflow=graph(),
                question_responses=[{'type': 'number'}])
    raw['requirements'] = []
    schema = outcome_schema(source)
    assert Draft202012Validator(schema).is_valid(raw)
    raw['workflow']['nodes'].append(node('other', 'read_table'))
    assert not Draft202012Validator(schema).is_valid(raw)
    raw['workflow'] = graph()
    raw['questions'] = ['\u2003']
    with pytest.raises(DraftOutcomeError):
        validate_generated_outcome(raw)


@pytest.mark.parametrize('nonnull', [False, True])
def test_forbidden_questions_stay_forbidden_after_outcome_constraints(nonnull):
    source = AgentDraft.model_json_schema()
    for key in ('questions', 'question_options', 'unsupported'):
        source['properties'][key]['maxItems'] = 0
    if nonnull:
        source['properties']['workflow'] = {'$ref': '#/$defs/WorkflowDefinition'}
    validator = Draft202012Validator(outcome_schema(source))
    raw = draft(workflow=graph())
    raw['requirements'] = []
    assert validator.is_valid(raw)
    raw['questions'] = ['기준은 얼마인가요?']
    raw['question_options'] = [[]]
    assert not validator.is_valid(raw)
    raw['workflow'] = None
    assert not validator.is_valid(raw)


@pytest.mark.parametrize('ordered', [False, True])
def test_ollama_preview_passes_actual_wire_schema_but_cannot_approve_or_save(monkeypatch, tmp_path, ordered):
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setenv('A4B_PLANNER_MODE', 'deepagent')
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'off')
    monkeypatch.setenv('A4B_REQUIREMENT_GENERATION_MODE', 'baseline')
    original = state([{'role': 'user', 'content': '표에서 큰 값만 남겨줘'}])
    original['capability_assessment'] = {'decision': 'existing_tools',
                                       'tool_ids': ['read_table'] if ordered else ['read_table', 'condition']}
    original['capability_fingerprint'] = input_fingerprint(original)
    first = {'tool': 'read_table', 'label': '표 읽기', 'settings': {'file_ids': [], 'node_ref': None}}
    workflow = ({'first_step': first, 'next_steps': []} if ordered else
                {'entry': 'read', 'nodes': [{'id': 'read', **first, 'bindings': []}], 'edges': []})
    raw = draft(questions=['기준은 얼마인가요?'], workflow=workflow,
                question_responses=[{'type': 'number'}])
    calls = []

    def factory(**options):
        # Check the actual decoder contract rather than only AgentDraft's
        # permissive editable/stored contract.
        Draft202012Validator(options['response_format'].schema).validate(raw)
        class Agent:
            def invoke(self, payload, config):
                calls.append(payload)
                return {'structured_response': deepcopy(raw)}
        return Agent()

    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', factory)
    result = make_conversation(None, 'test', model_factory=lambda: object())(original)
    current = {**original, **result}
    checked = validate_node(current)
    assert len(calls) == 1 and result['trace'][-1]['response_relation_repairs'] == 0
    assert result['draft']['questions'] == raw['questions']
    assert result['draft']['tool_ids'] == ['read_table']
    assert len(result['draft']['workflow']['nodes']) == 1
    assert checked['status'] == 'needs_clarification'
    assert supervisor_node({**current, **checked})['route'] == 'await_input'
    assert result['confirmed'] is False and result['saved_agent_id'] is None
    storage = SimpleNamespace(save_agent=Mock())
    forged = {**current, 'confirmed': True, 'status': 'ready'}
    with pytest.raises(ValueError, match='질문'):
        make_save_node(storage)(forged)
    storage.save_agent.assert_not_called()
    store = Storage(tmp_path)
    try:
        session = store.new_session('fixture', 'fixture')
        with pytest.raises(ValueError, match='질문'):
            store.save_agent({**forged, 'session_id': session['id']})
        assert not store.agents()
    finally:
        store.close()


def test_actual_bounded_ready_decoder_still_forbids_pending_outcomes(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setenv('A4B_PLANNER_MODE', 'deepagent')
    monkeypatch.setenv('A4B_REQUIREMENT_MODE', 'enforce')
    monkeypatch.setenv('A4B_REQUIREMENT_GENERATION_MODE', 'bounded')
    request = '첨부 표를 JSON으로 저장해줘.'
    original = state([{'role': 'user', 'content': request}])
    original['resources'] = [{'id': 'source', 'name': '표.csv', 'kind': 'table', 'columns': ['qty']}]
    original['capability_assessment'] = {'decision': 'existing_tools', 'tool_ids': ['read_table', 'write_result']}
    original['capability_fingerprint'] = input_fingerprint(original)
    specs = [dict(kind='read', file_ids=['source']), dict(kind='export', format='json')]
    ledger = extract_plan(dict(tasks=[dict(source_message=0, description=s['kind'], spec=s) for s in specs], unresolved=[]), original)
    original.update(requirement_required=True, requirement_ledger=ledger,
                    requirement_fingerprint=ledger_fingerprint(original), requirement_digest=plan_digest(ledger))
    raw = draft(workflow={
        'first_step': {'tool': 'read_table', 'label': '표 읽기', 'settings': {'file_ids': ['source'], 'node_ref': None}},
        'next_steps': {'step_2': {'tool': 'write_result', 'label': 'JSON 저장',
                                 'settings': {'format': 'json', 'node_ref': None},
                                 'bindings': [{'input': 'table', 'step': 1, 'output': 'table'}]}}})
    raw.update(instructions=request, document_ids=['source'], tool_ids=['read_table', 'write_result'])
    raw['execution_plan']['output_format'] = 'json'

    def factory(**options):
        schema = options['response_format'].schema
        assert schema['properties']['questions']['maxItems'] == 0
        validator = Draft202012Validator(schema)
        validator.validate(raw)
        for kind in ('questions', 'unsupported'):
            pending = deepcopy(raw)
            pending[kind] = ['실제로 미정인 조건 또는 미지원 사유']
            if kind == 'questions':
                pending['question_options'] = [[]]
            assert not validator.is_valid(pending)
            pending['workflow'] = None
            assert not validator.is_valid(pending)
        class Agent:
            def invoke(self, payload, config):
                return {'structured_response': deepcopy(raw)}
        return Agent()

    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', factory)
    result = make_conversation(None, 'test', model_factory=lambda: object())(original)
    assert validate_node({**original, **result})['status'] == 'ready'
