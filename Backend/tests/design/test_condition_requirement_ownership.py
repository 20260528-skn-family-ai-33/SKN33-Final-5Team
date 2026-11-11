"""Offline requirement-ownership regressions from previously reviewed development traces."""
from copy import deepcopy
import json

import pytest

from agent_builder.design.requirement_ledger import (
    extract_plan, ledger_fingerprint, make_requirement_check, plan_digest, validate_plan,
)
from tests.conditions.test_condition_readiness import checked, proposal, state


TABLE = dict(id='sales', name='sales.json', kind='table', columns=['customer_id', 'amount'],
             version='version-1')
FIRST = '연결한 표에서 customer_id 중복은 첫 행만 남기고 JSON으로 저장해줘. 빈 값은 오류.'
COMBINED = '연결한 표에서 customer_id 중복은 첫 행만 남긴 후 amount가 15 이상인 행을 JSON으로 저장해줘. 빈 값은 오류.'


class Extraction:
    def __init__(self, *packets):
        self.packets = packets
        self.payloads = []

    def invoke(self, messages):
        self.payloads.append(json.loads(messages[1]['content']))
        index = min(len(self.payloads) - 1, len(self.packets) - 1)
        return {'content': json.dumps(self.packets[index], ensure_ascii=False)}


def packet(*specs, questions=()):
    return {'tasks': [dict(source_message=0, description='요청한 ' + spec['kind'], spec=spec)
                      for spec in specs], 'unresolved': list(questions)}


def policy(retention='first'):
    return dict(kind='policy', missing_policy='error', duplicate_policy=retention, duplicate_field='customer_id')


def filter_spec():
    return dict(kind='filter', filters=[dict(field='amount', operator='gte', value=15,
                                            compare_field=None, value_type='number')], match='all')


def validated_state(request=FIRST, with_filter=False):
    current = state(request)
    current['resources'] = [deepcopy(TABLE)]
    values = {'duplicate.field': 'customer_id', 'duplicate.policy': 'first',
              'missing.policy': 'error', 'output.format': 'json'}
    operations = ['deduplicate']
    if with_filter:
        values.update({'filter.field': 'amount', 'filter.operator': 'gte', 'filter.value': '15'})
        operations.append('filter')
    ledger = checked(current, proposal(current, values, operations))
    assert ledger['decision'] == 'ready'
    current.update(condition_enforced=True, condition_required=True, condition_ledger=ledger)
    return current


def answered_state():
    current = state('customer_id 중복을 제거해서 JSON으로 저장해줘. 빈 값은 오류.')
    current['resources'] = [deepcopy(TABLE)]
    missing = checked(current, proposal(current, {'duplicate.field': 'customer_id', 'duplicate.policy': None,
        'output.format': 'json', 'missing.policy': 'error'}, ['deduplicate']))
    assert missing['decision'] == 'clarify'
    current.update(condition_ledger=missing, pending_questions=missing['questions'])
    current['messages'] += [dict(role='assistant', content=missing['questions'][0]['text']),
                            dict(role='user', content='중복은 마지막 행만 남겨줘.')]
    ready = checked(current, proposal(current, {'duplicate.policy': 'last'}, ['deduplicate']))
    assert ready['decision'] == 'ready' and ready['validated_user_turn'] == 2
    current.update(condition_enforced=True, condition_required=True, condition_ledger=ready, pending_questions=[])
    return current


def specs(plan):
    return [task['spec'] for task in plan['tasks']]


def test_condition_owned_filter_policy_export_survive_incomplete_extraction():
    current = validated_state(COMBINED, with_filter=True)
    # Reproduces the observed accepted ledger: only an export and wrong keep
    # policy for a confirmed first-row deduplication + numeric filter request.
    raw = packet(dict(kind='export', format='json'), policy('keep'))
    ledger = extract_plan(raw, current)
    result = specs(ledger)
    assert policy() in result
    assert filter_spec() in result
    assert result.count(dict(kind='export', format='json')) == 1
    assert policy('keep') not in result
    assert ledger['unresolved'] == []


def test_validated_plan_rejects_persisted_conflict_with_authoritative_condition():
    current = validated_state(COMBINED, with_filter=True)
    candidate = extract_plan(packet(filter_spec(), policy(), dict(kind='export', format='json')), current)
    next(task['spec'] for task in candidate['tasks'] if task['spec']['kind']=='policy')['duplicate_policy'] = 'keep'
    with pytest.raises(ValueError):
        validate_plan(candidate, current)


def test_last_row_answer_is_not_reasked_and_its_source_turn_is_retained():
    current = answered_state()
    raw = packet(policy('last'), dict(kind='export', format='json'), questions=[
        {'key': 'duplicate.policy', 'text': '어느 행을 남길까요?'},
        {'key': 'missing.policy', 'text': '빈 값은 어떻게 처리할까요?'},
    ])
    active = Extraction(raw)
    result = make_requirement_check('fixture', active, 'enforce')(current)
    assert result['requirement_route'] == 'plan' and not result['pending_questions']
    assert policy('last') in specs(result['requirement_ledger'])
    if active.payloads:
        item = active.payloads[0]['confirmed_conditions']['slots']['duplicate.policy']
        assert item['value'] == 'last' and 'user:2:0' in item['evidence_ids']
        assert item['source_message'] == 1


def test_only_owned_question_is_removed_when_other_requested_work_needs_information():
    current = validated_state(FIRST + ' 결과는 담당자에게 이메일로 보내줘.')
    raw = packet(policy(), dict(kind='export', format='json'),
                 dict(kind='other', action='담당자에게 이메일 발송'), questions=[
                     {'key': 'duplicate.policy', 'text': '첫 행을 유지할까요?'},
                     {'key': 'delivery.recipient', 'text': '받는 사람의 이메일 주소는 무엇인가요?'},
                 ])
    result = make_requirement_check('fixture', Extraction(raw), 'enforce')(current)
    assert result['requirement_route'] == 'await_input'
    assert [q['text'] for q in result['pending_questions']] == ['받는 사람의 이메일 주소는 무엇인가요?']
    assert result['pending_questions'][0]['key'] == 'delivery.recipient'
    assert any(s.get('action') == '담당자에게 이메일 발송' for s in specs(result['requirement_ledger']))


def test_condition_projection_changes_cache_key_and_reextracts_prior_candidate():
    current = validated_state()
    prior = {**current, 'condition_enforced': False, 'condition_required': False}
    old = extract_plan(packet(policy('keep'), dict(kind='export', format='json')), prior)
    current.update(requirement_ledger=old, requirement_fingerprint=ledger_fingerprint(prior),
                   requirement_digest=plan_digest(old))
    assert ledger_fingerprint(current) != current['requirement_fingerprint']
    result = make_requirement_check('fixture', Extraction(packet(policy(), dict(kind='export', format='json'))),
                                    'enforce')(current)
    assert result['requirement_route'] == 'plan'
    assert policy() in specs(result['requirement_ledger'])


def test_cached_plan_with_current_digest_but_conflicting_condition_is_revalidated():
    current = validated_state()
    old = extract_plan(packet(policy(), dict(kind='export', format='json')), current)
    next(t['spec'] for t in old['tasks'] if t['spec']['kind']=='policy')['duplicate_policy'] = 'keep'
    current.update(requirement_ledger=old, requirement_fingerprint=ledger_fingerprint(current),
                   requirement_digest=plan_digest(old))
    active = Extraction(packet(policy(), dict(kind='export', format='json')))
    result = make_requirement_check('fixture', active, 'enforce')(current)
    if result['requirement_route'] == 'plan':
        assert policy() in specs(result['requirement_ledger'])
        assert policy('keep') not in specs(result['requirement_ledger'])
    else:
        assert result['requirement_route'] == 'stop' and not result.get('requirement_ledger')


@pytest.mark.parametrize('stale', ['shadow', 'new_user_turn', 'resource_changed', 'bad_evidence',
                                   'tampered_value', 'clarify'])
def test_stale_shadow_and_unverified_values_are_not_exposed_as_confirmed(stale):
    current = validated_state()
    if stale == 'shadow':
        current.update(condition_enforced=False, condition_required=False)
    elif stale == 'new_user_turn':
        current['messages'].append(dict(role='user', content='중복 정책을 바꿀게.'))
    elif stale == 'resource_changed':
        current['resources'][0]['version'] = 'version-2'
    elif stale == 'bad_evidence':
        item = current['condition_ledger']['slots']['duplicate.policy']
        item['evidence_ids'] = ['user:99:0']; item['evidence'] = []
    elif stale == 'tampered_value':
        current['condition_ledger']['slots']['duplicate.policy']['value'] = 'last'
    else:
        current['condition_ledger']['decision'] = 'clarify'
    raw = packet(dict(kind='export', format='json'), questions=['추가 조건은 무엇인가요?'])
    active = Extraction(raw)
    result = make_requirement_check('fixture', active, 'enforce')(current)
    if active.payloads:
        confirmed = active.payloads[0].get('confirmed_conditions') or {}
        if stale == 'tampered_value':
            assert confirmed.get('slots', {}).get('duplicate.policy', {}).get('value') != 'last'
        else:
            assert 'duplicate.policy' not in confirmed.get('slots', {})
    assert result['requirement_route'] != 'plan'


def test_identical_export_for_unambiguous_single_artifact_is_collapsed():
    current = state('같은 결과를 JSON 파일 하나로 저장해줘.')
    raw = packet(dict(kind='export', format='json'), dict(kind='export', format='json'))
    assert specs(extract_plan(raw, current)) == [dict(kind='export', format='json')]


@pytest.mark.parametrize('user_request,formats', [
    ('결과를 CSV와 JSON 두 파일로 각각 저장해줘.', ['csv', 'json']),
    ('동일한 결과를 JSON 파일 두 개로 각각 저장해줘.', ['json', 'json']),
])
def test_explicit_separate_outputs_are_not_removed(user_request, formats):
    current = state(user_request)
    raw = packet(*(dict(kind='export', format=fmt) for fmt in formats))
    assert [s['format'] for s in specs(extract_plan(raw, current))] == formats


def test_same_format_exports_before_and_after_processing_keep_distinct_inputs():
    current = state('원본을 JSON으로 저장하고 금액 열을 매출로 바꾼 결과도 JSON으로 저장해줘.')
    raw = packet(dict(kind='export', format='json'), dict(kind='rename', source='금액', target='매출'),
                 dict(kind='export', format='json'))
    result = extract_plan(raw, current)
    assert [s['kind'] for s in specs(result)] == ['export', 'rename', 'export']
    assert result['tasks'][0]['depends_on'] != result['tasks'][2]['depends_on']


def test_equal_format_without_a_proven_single_output_does_not_collapse_distinct_artifacts():
    current = state('서울 지점 결과와 부산 지점 결과를 JSON으로 저장해줘.')
    raw = packet(dict(kind='export', format='json'), dict(kind='export', format='json'))
    raw['tasks'][0]['description'] = '서울 지점 결과 저장'
    raw['tasks'][1]['description'] = '부산 지점 결과 저장'
    assert len(extract_plan(raw, current)['tasks']) == 2


def test_contract_failure_cannot_become_ready_merely_because_some_conditions_are_known():
    current = validated_state()
    invalid = {'tasks': [{'source_message': 0, 'description': '잘못된 업무',
                          'spec': {'kind': 'unregistered_kind'}}], 'unresolved': []}
    result = make_requirement_check('fixture', Extraction(invalid), 'enforce')(current)
    assert result['requirement_route'] == 'stop' and result['requirement_ledger'] is None


def test_materialized_policy_cites_authoritative_answer_instead_of_candidate_turn():
    current = answered_state()
    ledger = extract_plan(packet(policy('first')), current)
    task = next(t for t in ledger['tasks'] if t['spec']['kind'] == 'policy')
    assert task['spec']['duplicate_policy'] == 'last'
    assert task['source_message'] == 1 and task['quote'] == current['messages'][-1]['content']


def test_cached_resolved_question_requires_reconciliation_before_asking_user():
    current = validated_state()
    old = extract_plan(packet(), current)
    old['unresolved'] = [{'key':'missing.policy', 'text':'빈 값이 있으면 어떻게 할까요?'}]
    current.update(requirement_ledger=old, requirement_fingerprint=ledger_fingerprint(current),
                   requirement_digest=plan_digest(old))
    result = make_requirement_check('fixture', Extraction(packet()), 'enforce')(current)
    assert result['requirement_route'] == 'plan'
    assert not result.get('pending_questions') and result['requirement_ledger']['unresolved'] == []
