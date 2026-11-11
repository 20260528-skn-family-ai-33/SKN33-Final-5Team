"""User schedule facts survive every clarification path without reasking them."""
from copy import deepcopy

import pytest

from agent_builder.design.question_time import schedule_question_keys
from agent_builder.design.requirement_clarification import schedule_facts,missing_schedule_questions
from agent_builder.design.execution_mode import filter_execution_questions


def state(*texts):
    return {'messages':[{'role':'user','content':text} for text in texts]}


@pytest.mark.parametrize('user_text,expected',[
    ('매일 오전 9시 10분에 보고서를 만들어줘.',{'schedule.frequency':'daily','schedule.hour':9,'schedule.minute':10}),
    ('매주 화요일 오후 2시 35분에 파일을 정리해줘.',{'schedule.frequency':'weekly','schedule.weekday':1,'schedule.hour':14,'schedule.minute':35}),
    ('매월 15일 오후 3시 20분에 거래를 집계해줘.',{'schedule.frequency':'monthly','schedule.monthday':15,'schedule.hour':15,'schedule.minute':20}),
    ('매시간 10분에 알림을 정리해줘.',{'schedule.frequency':'hourly','schedule.minute':10}),
])
def test_complete_literals_across_recurrence_and_tasks_are_not_asked_again(user_text,expected):
    current=state(user_text)
    assert all(schedule_facts(current).get(key)==value for key,value in expected.items())
    assert missing_schedule_questions(current)==[]


def test_unknown_morning_still_asks_for_one_time_without_inventing_hour():
    current=state('매일 오전에 자료를 요약해줘.')
    facts=schedule_facts(current)
    assert 'schedule.hour' not in facts
    assert [question['key'] for question in missing_schedule_questions(current)]==['schedule.time']


@pytest.mark.parametrize('question',[
    {'key':'schedule.time','text':'실행할 시각은 몇 시인가요?'},
    {'key':'capability.condition.0','text':'실행 시각 (시간)에 대해 알려주세요.'},
    {'key':'execution_time','text':'매일 실행할 시각은 몇 시인가요?'},
])
def test_capability_answer_snapshots_preserve_time_after_pending_records_are_cleared(question):
    question={**question,'id':'actual-question','response':{'type':'time'}}
    content=question['text']+' → 10:25'
    current=state('매일 오전에 요약해줘.',content)
    current.update(pending_questions=[],capability_questions=[],capability_answers=[{
        'turn':1,'answer':content,'question_ids':['actual-question'],'questions':[question],
        'answers':[{'question_id':'actual-question','value':'10:25'}]}])
    original=deepcopy(current)
    assert schedule_facts(current)['schedule.hour']==10
    assert schedule_facts(current)['schedule.minute']==25
    assert missing_schedule_questions(current)==[]
    assert current==original


@pytest.mark.parametrize('change', ['stale_id','different_message'])
def test_stale_or_unbound_capability_values_are_not_schedule_evidence(change):
    question={'id':'actual','key':'schedule.time','text':'실행 시각','response':{'type':'time'}}
    content='실행 시각 → 값 입력'
    current=state('매일 오전에 보고서를 만들어줘.',content)
    event={'turn':1,'answer':content,'question_ids':['actual'],'questions':[question],
        'answers':[{'question_id':'actual','value':'10:25'}]}
    if change=='stale_id':event['answers'][0]['question_id']='stale'
    else:event['answer']='다른 원문'
    current['capability_answers']=[event]
    assert 'schedule.hour' not in schedule_facts(current)
    assert [question['key'] for question in missing_schedule_questions(current)]==['schedule.time']


def test_condition_batch_answer_keeps_time_and_weekday_bound_to_their_keys():
    content='실행 시각 → 입력값\n실행 요일 → 입력값'
    current=state('매주 실행해줘.',content)
    current['condition_answers']=[{'turn':1,'answer':content,'question_ids':['time','day'],
        'candidate_keys':['schedule.time','schedule.weekday'],'answers':[
            {'question_id':'time','key':'schedule.time','value':'07:45'},
            {'question_id':'day','key':'schedule.weekday','value':2}]}]
    facts=schedule_facts(current)
    assert (facts['schedule.hour'],facts['schedule.minute'],facts['schedule.weekday'])==(7,45,2)
    assert missing_schedule_questions(current)==[]


@pytest.mark.parametrize('business_edit',[
    '회의 시작 시각은 오후 6시로 등록해줘.',
    '조회 기간은 지난 3일이야.',
    '분석 대상 날짜는 15일이야.',
])
def test_business_followup_does_not_overwrite_existing_execution_clock_or_monthday(business_edit):
    current=state('매월 10일 오전 9시에 자료를 정리해줘.',business_edit)
    facts=schedule_facts(current)
    assert (facts['schedule.hour'],facts['schedule.minute'],facts['schedule.monthday'])==(9,0,10)


def test_explicit_time_change_updates_the_clock_without_asking_for_it_again():
    current=state('매일 오전 9시에 문서를 정리해줘.','오후 6시 20분으로 바꿔줘.')
    facts=schedule_facts(current)
    assert (facts['schedule.hour'],facts['schedule.minute'])==(18,20)
    assert missing_schedule_questions(current)==[]


@pytest.mark.parametrize('initial,answer,key,value',[
    ('매주 오전 9시에 실행해줘.','월요일로 해줘.','schedule.weekday',0),
    ('매월 오전 9시에 실행해줘.','15일로 해줘.','schedule.monthday',15),
    ('매월 오전 9시에 실행해줘.','말일로 해줘.','schedule.month_end',True),
])
def test_short_replies_keep_weekday_and_monthly_date_without_reasking(initial,answer,key,value):
    current=state(initial,answer)
    assert schedule_facts(current)[key]==value
    assert missing_schedule_questions(current)==[]


def test_a_weekday_in_an_output_filename_is_not_a_schedule_edit():
    current=state('매주 월요일 오전 9시에 실행해줘.','출력 파일 이름은 화요일 보고서로 해줘.')
    assert schedule_facts(current)['schedule.weekday']==0


def test_cancel_then_new_recurrence_does_not_reuse_cancelled_time():
    current=state('매일 오전 9시에 실행해줘.','예약 취소해줘.','매주 월요일에 다시 실행해줘.')
    facts=schedule_facts(current)
    assert facts['schedule.frequency']=='weekly' and facts['schedule.weekday']==0
    assert 'schedule.hour' not in facts and 'schedule.minute' not in facts


def test_a_negated_cancellation_preserves_the_confirmed_clock():
    current=state('매일 오전 9시에 실행해줘.','예약 취소하지 말고 요약 내용만 바꿔줘.')
    facts=schedule_facts(current)
    assert (facts['schedule.frequency'],facts['schedule.hour'],facts['schedule.minute'])==('daily',9,0)


def test_a_combined_unknown_business_window_cannot_be_dropped_due_to_its_native_key():
    question={'key':'schedule.time','text':'실행 시각과 조회 기간을 알려주세요.'}
    assert schedule_question_keys(question)==set()
    assert filter_execution_questions([question],state('매일 오전 9시에 실행해줘.'))==[question]


def test_question_keys_cover_all_requested_fields_and_exclude_business_clock():
    assert schedule_question_keys({'key':'schedule.time','text':'몇 시에 실행할까요?'})=={'schedule.hour','schedule.minute'}
    assert schedule_question_keys({'text':'실행 시각과 요일은 언제인가요?'})=={'schedule.hour','schedule.minute','schedule.weekday'}
    assert schedule_question_keys({'text':'회의 시작 시각은 몇 시인가요?'})==set()
    assert schedule_question_keys({'text':'조회할 메일의 시간 범위는 어떻게 되나요?'})==set()


def test_legacy_generic_time_question_is_removed_only_when_every_requested_field_is_known():
    question={'key':'capability.condition.0','text':'실행 시각과 요일은 언제인가요?'}
    incomplete=state('매주 오전 9시에 보고서를 만들어줘.')
    assert filter_execution_questions([question],incomplete)==[question]
    complete=state('매주 화요일 오전 9시에 보고서를 만들어줘.')
    assert filter_execution_questions([question],complete)==[]
