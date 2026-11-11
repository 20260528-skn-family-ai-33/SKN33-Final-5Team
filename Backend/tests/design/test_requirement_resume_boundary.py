import pytest

from agent_builder.capabilities.capability_gate import require_ready
from agent_builder.design.nodes import await_input
from agent_builder.design.requirement_ledger import extract_plan, ledger_fingerprint, plan_digest


def test_gate_rechecks_evidence_in_self_consistent_checkpoint():
    state = {'messages': [{'role': 'user', 'content': '표를 JSON으로 저장해줘.'}]}
    ledger = extract_plan({'tasks': [{'source_message': 0, 'description': 'JSON 저장',
                          'spec': {'kind': 'export', 'format': 'json'}}], 'unresolved': []}, state)
    ledger['tasks'][0]['quote'] = '실제 대화에 없는 문장'
    state.update(requirement_required=True, requirement_ledger=ledger,
                 requirement_digest=plan_digest(ledger), requirement_fingerprint=ledger_fingerprint(state))
    with pytest.raises(ValueError):
        require_ready(state)


def test_answer_to_one_of_two_owners_is_bound_only_to_that_question(monkeypatch):
    condition = dict(id='cond', key='duplicate.policy', text='어느 행을 남길까요?')
    requirement = dict(id='req', key='compute.output_column', source='requirement', text='새 열 이름은 무엇인가요?')
    state = dict(condition_enforced=True, messages=[], pending_questions=[condition, requirement],
                 issues=[], draft={'questions': [condition['text'], requirement['text']]})
    monkeypatch.setattr(await_input, 'interrupt', lambda _: dict(type='message', content='net', question_id='req'))
    result = await_input.await_input_node(state)
    assert 'condition_answers' not in result
    [event] = result['requirement_answers']
    assert event['question_ids'] == ['req']
    assert event['candidate_keys'] == ['compute.output_column']
    assert event['answer'] == event['message_content'] == 'net' and event['turn'] == 0
    [snapshot] = event['questions']
    assert {key: snapshot[key] for key in requirement} == requirement
    assert snapshot['response']['type'] == 'text'
    # This was a free-text reply to the requirement owner, not an invented
    # typed value or a response to the condition owner's pending question.
    assert 'value' not in event
    assert condition['id'] not in event['question_ids']
    assert 'response' not in requirement


def test_condition_judge_does_not_treat_unknown_requirement_answer_as_its_own(monkeypatch):
    from agent_builder.conditions import condition_check
    from tests.conditions.test_condition_readiness import state

    current = state()
    current['pending_questions'] = [dict(id='req', key='compute.output_column', source='requirement',
                                         text='새 열 이름을 정했나요?', options=[])]
    current['messages'].append(dict(role='user', content='아직 미정'))
    seen = []

    def judge(incoming, transport):
        seen.extend(incoming.get('pending_questions', []))
        raise OSError('fixture')

    monkeypatch.setattr(condition_check, 'judge_conditions', judge)
    condition_check.make_condition_check(transport=object(), mode='enforce')(current)
    assert seen == []
