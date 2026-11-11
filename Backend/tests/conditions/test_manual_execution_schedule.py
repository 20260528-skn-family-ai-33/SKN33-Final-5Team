"""Request intent owns optional triggers, preserving business time filters."""
from copy import deepcopy

import pytest

from agent_builder.conditions.condition_readiness import schedule_literals
from tests.conditions.test_condition_readiness import checked, proposal, state


def manual_state(text):
    return {**state(text), 'execution_mode':'manual'}


@pytest.mark.parametrize('mode', ['manual', 'scheduled'])
def test_daily_morning_request_overrides_legacy_creation_choice(mode):
    current={**state('매일 오전에 비용이 100만원 초과인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'),
             'execution_mode':mode}
    result=checked(current,proposal(current))
    assert result['decision']=='clarify'
    assert 'schedule' in result['operations']
    assert 'schedule.hour' in result['unresolved']
    assert any(question['key'].startswith('schedule.') for question in result['questions'])


def test_manual_choice_ignores_model_proposed_schedule_slots_without_losing_filter():
    current=manual_state('비용이 100만원 초과인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    raw=proposal(current,operations=['filter','schedule'])
    raw['conditions'].extend([
        {'key':'schedule.frequency','status':'missing','value':None,'evidence_ids':[]},
        {'key':'schedule.hour','status':'missing','value':None,'evidence_ids':[]},
    ])
    result=checked(current,raw)
    assert result['decision']=='ready'
    assert result['operations']==['filter']
    assert result['slots']['filter.value']['value']==1000000
    assert not any(key.startswith('schedule.') for key in result['slots'])


def test_manual_choice_does_not_restore_schedule_from_a_previous_ledger():
    text='매일 오전에 비용이 100만원 초과인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.'
    scheduled={**state(text),'execution_mode':'scheduled'}
    old=checked(scheduled,proposal(scheduled))
    assert 'schedule' in old['operations']
    current={**deepcopy(scheduled),'execution_mode':'manual','condition_ledger':old,
             'pending_questions':old['questions']}
    current['messages'].append({'role':'user','content':'예약은 취소하고 직접 실행할게.'})
    result=checked(current,proposal(scheduled))
    assert result['decision']=='ready'
    assert result['operations']==['filter']
    assert not any(key.startswith('schedule.') for key in result['slots'])


@pytest.mark.parametrize('field', ['조회시간','마감시간'])
def test_business_time_filter_is_preserved_in_manual_creation(field):
    current=manual_state(f'{field}이 "09:00"인 행을 JSON으로 저장해줘. 빈 값은 오류, 중복은 유지해.')
    raw=proposal(current,{'filter.field':field,'filter.operator':'eq','filter.value':'09:00',
                          'missing.policy':'error','duplicate.policy':'keep','output.format':'json'})
    original=deepcopy(current)
    result=checked(current,raw)
    assert result['decision']=='ready'
    assert result['slots']['filter.field']['value']==field
    assert result['slots']['filter.value']['value']=='09:00'
    assert result['operations']==['filter']
    assert current==original


def test_literal_schedule_parser_remains_available_for_other_callers():
    literals=list(schedule_literals('매일 오전 9시 30분에 실행해줘.'))
    assert ('schedule.frequency','daily') in [(key,value) for key,value,_ in literals]
    assert ('schedule.hour',9) in [(key,value) for key,value,_ in literals]
    assert ('schedule.minute',30) in [(key,value) for key,value,_ in literals]
