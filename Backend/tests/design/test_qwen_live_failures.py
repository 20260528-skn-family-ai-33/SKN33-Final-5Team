"""Offline reproductions derived from live DEVELOPMENT failures, not live scores."""
from copy import deepcopy
import pytest

from agent_builder.capabilities.capability_check import _question_text
from agent_builder.design.requirement_ledger import grounded_schedule
from tests.conditions.test_condition_readiness import state, proposal, checked


@pytest.mark.parametrize('guessed',['first','last'])
def test_c05_deduplicate_without_retention_does_not_confirm_model_default(guessed):
    current=state('연결한 표에서 중복을 제거하고 JSON으로 저장해줘. 빈 값은 오류로 처리해.')
    raw=proposal(current,{'duplicate.field':None,'duplicate.policy':guessed,'missing.policy':'error','output.format':'json'},['deduplicate'])
    result=checked(current,raw)
    assert result['slots']['duplicate.policy']['status']=='missing'
    assert set(result['unresolved'])=={'duplicate.field','duplicate.policy'}


def test_c05_unknown_retention_remains_unknown_after_restart_then_answer_replaces_it():
    current=state('customer_id 중복을 제거해서 JSON으로 저장해줘.')
    current['resources']=[{'id':'table','kind':'table','columns':['customer_id']}]
    raw=proposal(current,{'duplicate.field':'customer_id','duplicate.policy':'first','output.format':'json'},['deduplicate'])
    first=checked(current,raw)
    assert first['decision']=='clarify'
    restored={**deepcopy(current),'condition_ledger':deepcopy(first),'pending_questions':first['questions']}
    restored['messages'] += [{'role':'assistant','content':first['questions'][0]['text']},
                             {'role':'user','content':'중복은 마지막 행만 남겨줘.'}]
    result=checked(restored,proposal(restored,{'duplicate.policy':'last'},['deduplicate']))
    assert result['decision']=='ready'
    assert result['slots']['duplicate.field']['value']=='customer_id'
    assert result['slots']['duplicate.policy']['value']=='last'
    assert result['questions']==[]


def test_c01_validated_request_timezone_can_ground_explicit_schedule():
    current=state('매일 오전 9시에 매출 보고서를 이메일로 보내줘.')
    current['user_timezone']='Asia/Seoul'
    result=grounded_schedule(current)
    assert (result['frequency'],result['hour'],result['minute'],result['timezone'])==('daily',9,0,'Asia/Seoul')


@pytest.mark.parametrize('zone',[None,'not-a-zone'])
def test_schedule_never_guesses_invalid_request_timezone(zone):
    current=state('매일 오전 9시에 보고서를 만들어줘.')
    current['user_timezone']=zone
    with pytest.raises(ValueError):grounded_schedule(current)


def test_explicit_schedule_timezone_wins_over_browser_timezone():
    current=state('한국 시간으로 매일 오전 9시에 표를 JSON으로 저장해줘.')
    current['user_timezone']='America/New_York'
    assert grounded_schedule(current)['timezone']=='Asia/Seoul'


@pytest.mark.parametrize('question',['분석할 자료 ID를 알려주세요.','사용할 파일 ID는 무엇인가요?'])
def test_c02_resource_identifier_question_uses_resource_connection_ui(question):
    result=_question_text(question)
    assert 'ID' not in result
    assert '자료 연결' in result


def test_business_column_id_is_not_a_resource_identifier():
    assert _question_text('고객 ID 열을 기준으로 집계할까요?')=='고객 ID 열을 기준으로 집계할까요?'


def test_c07_time_parts_share_one_question_leaving_room_for_weekday():
    current=state('한국 시간으로 매주 표를 JSON으로 저장해줘.')
    current['resources']=[{'id':'table','kind':'table','columns':['amount']}]
    raw=proposal(current,{'schedule.frequency':'weekly','schedule.timezone':'Asia/Seoul','output.format':'json'},['schedule'])
    result=checked(current,raw)
    assert {'schedule.hour','schedule.minute','schedule.weekday'}<=set(result['unresolved'])
    assert [q['key'] for q in result['questions']]==['schedule.hour','schedule.weekday']
    assert '분' in result['questions'][0]['text']


@pytest.mark.parametrize('key,value',[('schedule.minute','0'),('schedule.hour','9')])
def test_c08_unwritten_time_is_not_a_confirmed_model_default(key,value):
    current=state('한국 시간으로 매일 표를 JSON으로 저장해줘.')
    current['resources']=[{'id':'table','kind':'table','columns':['amount']}]
    raw=proposal(current,{'schedule.frequency':'daily','schedule.timezone':'Asia/Seoul',key:value,'output.format':'json'},['schedule'])
    result=checked(current,raw)
    assert result['slots'][key]['status']=='missing'
