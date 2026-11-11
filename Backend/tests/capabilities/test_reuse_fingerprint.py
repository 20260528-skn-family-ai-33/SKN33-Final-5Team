from copy import deepcopy

import pytest

from agent_builder.capabilities.capability_gate import input_fingerprint, blocking_issue
from agent_builder.design.request_meaning import meaning_fingerprint
from agent_builder.design.requirement_ledger import ledger_fingerprint


def conversation():
    return {'model': 'model-a', 'user_timezone':'Asia/Seoul',
            'messages':[{'role':'user','content':'요약해줘'}, {'role':'assistant','content':'어느 쪽인가요?'},
                        {'role':'user','content':'두 개'}],
            'capability_answers':[{'turn':2,'kind':'answer','answer':'두 개',
                                   'question_ids':['q1'],'candidate_keys':['mail.limit']}],
            'temporal_context':{'user_message_anchors':[
                {'source_message':0,'reference_time':'2026-11-10T12:00:00Z','timezone':'Asia/Seoul'},
                {'source_message':1,'reference_time':'2026-11-11T12:00:00Z','timezone':'Asia/Seoul'}]}}


@pytest.mark.parametrize('change', ['model','question_id','slot','anchor','timezone'])
def test_identical_text_does_not_reuse_different_evidence(change):
    old = conversation()
    current = deepcopy(old)
    if change == 'model':
        current['model'] = 'model-b'
    elif change == 'question_id':
        current['capability_answers'][0]['question_ids'] = ['q2']
    elif change == 'slot':
        current['capability_answers'][0]['candidate_keys'] = ['output.count']
    elif change == 'anchor':
        current['temporal_context']['user_message_anchors'][0]['reference_time'] = '2026-11-09T12:00:00Z'
    else:
        current['user_timezone'] = 'Europe/Berlin'
    for fingerprint in (input_fingerprint, ledger_fingerprint, meaning_fingerprint):
        assert fingerprint(current) != fingerprint(old)


def test_previous_turn_excludes_later_question_event_and_calendar_anchor():
    extended = conversation()
    prior = deepcopy(extended)
    prior['messages'] = prior['messages'][:1]
    prior['capability_answers'] = []
    prior['temporal_context']['user_message_anchors'] = prior['temporal_context']['user_message_anchors'][:1]
    prefix = {**extended, 'messages':extended['messages'][:-1]}
    for fingerprint in (input_fingerprint, ledger_fingerprint, meaning_fingerprint):
        assert fingerprint(prefix) == fingerprint(prior)
    current = dict(extended, capability_assessment={'decision':'existing_tools'},
                   capability_fingerprint=input_fingerprint(prior))
    assert blocking_issue(current)


def test_capability_model_override_invalidates_support_and_requirements(monkeypatch):
    current = conversation()
    monkeypatch.setenv('A4B_CAPABILITY_MODEL','support-a')
    previous = input_fingerprint(current), ledger_fingerprint(current), meaning_fingerprint(current)
    monkeypatch.setenv('A4B_CAPABILITY_MODEL','support-b')
    assert previous[0] != input_fingerprint(current)
    assert previous[1] != ledger_fingerprint(current)
    assert previous[2] == meaning_fingerprint(current)


def test_fingerprint_tracks_effective_ollama_model_only_for_its_connection(monkeypatch):
    current = {**conversation(), 'credential_id':'local-model'}
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    monkeypatch.setenv('A4B_CAPABILITY_MODEL', 'support')
    monkeypatch.delenv('A4B_OLLAMA_MODEL_ALIASES', raising=False)
    def keys():
        return (meaning_fingerprint(current), input_fingerprint(current), ledger_fingerprint(current))
    original = keys()
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', '{"unrelated":"other"}')
    assert keys() == original
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', '{"support":"support-v2"}')
    changed_support = keys()
    assert changed_support[0] == original[0] and all(a != b for a,b in zip(changed_support[1:],original[1:]))
    monkeypatch.setenv('A4B_OLLAMA_MODEL_ALIASES', '{"model-a":"model-b","support":"support-v2"}')
    assert all(a != b for a,b in zip(keys(),changed_support))
    current['credential_id'] = 'external-credential'
    assert keys() == original
    current['credential_id'] = 'local-model'
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'vllm')
    assert keys() == original


def test_policy_contract_revision_invalidates_only_its_dependent_judgments(monkeypatch):
    current = conversation()
    previous = input_fingerprint(current), ledger_fingerprint(current), meaning_fingerprint(current)
    monkeypatch.setattr('agent_builder.capabilities.capability_gate.CAPABILITY_POLICY', 'revised')
    assert (previous[0],previous[1]) != (input_fingerprint(current),ledger_fingerprint(current))
    assert previous[2] == meaning_fingerprint(current)


@pytest.mark.parametrize('change', ['resource','old-fingerprint'])
def test_deferred_answer_reassesses_after_resource_or_checkpoint_change(change):
    from agent_builder.capabilities.capability_check import make_capability_check
    from tests.capabilities.test_capability_assessment import packet
    from tests.conditions.test_condition_readiness import Fake, state
    current = state('표를 정리해줘')
    assessed = make_capability_check('fixture', Fake([packet(current,'clarify',conditions=['어느 열인가요?'])]),
                                     'enforce')(current)
    current.update(assessed)
    question = current['capability_questions'][0]['id']
    turn = len(current['messages'])
    current['messages'].append({'role':'user','content':'일단 보류'})
    current['capability_answers'] = [dict(turn=turn, answer='일단 보류', kind='defer',
                                          question_ids=[question],candidate_keys=['capability.condition.0'])]
    if change == 'resource':
        current['resources'] = [dict(id='changed',kind='table',name='new.csv',columns=['team'])]
    else:
        current['capability_fingerprint'] = 'fingerprint-from-older-policy'
    transport = Fake([packet(current, 'clarify', conditions=['새 자료에서 어느 열인가요?'])])
    result = make_capability_check('fixture', transport, 'enforce')(current)
    assert transport.calls == 1
    assert result['capability_questions'][0]['id'] != question
