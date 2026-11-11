from copy import deepcopy

import pytest

from agent_builder.design.execution_mode import (
    effective_execution_mode,execution_mode_context,filter_execution_questions,
    infer_execution_intent,requested_schedule,validate_execution_mode,
)


def state(*messages,**extra):
    return {'messages':[{'role':'user','content':text} for text in messages],**extra}


@pytest.mark.parametrize('text,frequency,hour,minute',[
    ('매일 오전 9시에 메일을 요약해줘.','daily',9,0),
    ('매주 월요일 오후 3시 20분에 재고 보고서를 만들어줘.','weekly',15,20),
    ('매월 말일 오전 10시에 매출을 정리해줘.','monthly',10,0),
    ('매시간 15분에 새 자료를 확인해줘.','hourly',None,15),
])
def test_requested_recurrence_builds_native_schedule_across_business_domains(text,frequency,hour,minute):
    current=state(text,execution_mode=None)
    intent=infer_execution_intent(current)
    assert intent['mode']=='scheduled' and intent['source']=='request'
    spec=requested_schedule(current)
    assert spec['frequency']==frequency and spec['hour']==hour and spec['minute']==minute
    assert spec['timezone']=='Asia/Seoul'


def test_new_sessions_with_no_scheduling_request_need_no_mode_selection():
    current=state('업로드한 거래표에서 높은 금액의 행만 정리해줘.',execution_mode=None)
    assert effective_execution_mode(current)=='manual'
    assert requested_schedule(current) is None
    assert execution_mode_context(current)['source']=='default'
    assert filter_execution_questions([{'key':'schedule.time','text':'실행 시간은?'}],current)==[]


@pytest.mark.parametrize('text',['정기적으로 재고 보고서를 만들어줘.','주기적으로 새 자료를 확인해줘.','이 작업을 자동 실행하고 싶어.'])
def test_explicit_repeat_intent_without_a_frequency_asks_for_the_missing_schedule(text):
    from agent_builder.design.requirement_clarification import missing_schedule_questions
    current=state(text,execution_mode=None)
    assert effective_execution_mode(current)=='scheduled'
    assert requested_schedule(current) is None
    assert [question['key'] for question in missing_schedule_questions(current)]==['schedule.frequency']


def test_request_schedule_overrides_a_legacy_creation_selection():
    current=state('매일 오전 9시에 문서를 요약해줘.',execution_mode='manual')
    assert effective_execution_mode(current)=='scheduled'
    assert requested_schedule(current)['hour']==9


def test_cancel_and_later_readd_apply_in_user_message_order():
    cancelled=state('매일 오전 9시에 보고서를 만들어줘.','예약은 추가하지 않고 워크플로우만 저장합니다.')
    assert effective_execution_mode(cancelled)=='manual'
    assert requested_schedule(cancelled) is None
    restarted=state('매일 오전 9시에 보고서를 만들어줘.','예약 취소해줘.','매주 금요일 오후 2시에 다시 예약해줘.')
    assert effective_execution_mode(restarted)=='scheduled'
    spec=requested_schedule(restarted)
    assert spec['frequency']=='weekly' and spec['weekday']==4 and spec['hour']==14


def test_cancelling_a_cancel_is_not_a_cancellation():
    current=state('매일 오전 9시에 보고서를 만들어줘.','예약 취소하지 마.')
    assert effective_execution_mode(current)=='scheduled'


def test_latest_clock_update_preserves_frequency_but_replaces_the_old_clock():
    current=state('매일 오전 9시에 보고서를 만들어줘.','오후 6시 30분으로 바꿔줘.')
    spec=requested_schedule(current)
    assert spec['frequency']=='daily' and spec['hour']==18 and spec['minute']==30


@pytest.mark.parametrize('text',[
    '지난 일주일 동안 수신한 메일을 요약해줘.',
    '매일 받은 메일 중 특정 제목의 메일만 골라줘.',
    '매주 월요일 오전 9시 회의를 캘린더에 등록해줘.',
    '마감 시간이 오전 9시인 행만 정리해줘.',
])
def test_business_dates_query_periods_and_calendar_events_are_not_workflow_triggers(text):
    current=state(text,execution_mode=None)
    assert effective_execution_mode(current)=='manual'
    assert requested_schedule(current) is None


def test_complete_user_clock_is_not_asked_again_but_business_times_remain():
    current=state('매일 오전 9시에 자료를 정리해줘.',execution_mode=None)
    questions=[
        {'key':'schedule.frequency','text':'실행 주기는 무엇인가요?'},
        {'key':'schedule.time','text':'실행 시각은 몇 시인가요?'},
        {'key':'requirements.detail','text':'실행 시간은 언제인가요?'},
        {'key':'deadline','text':'업무 마감 시간은 언제인가요?'},
    ]
    original=deepcopy(questions)
    assert filter_execution_questions(questions,current)==[questions[-1]]
    assert questions==original


def test_a_schedule_key_does_not_hide_a_combined_business_time_question():
    combined={'key':'schedule.time','text':'실행 시각과 조회 기간을 알려주세요.'}
    manual=state('자료를 정리해줘.',execution_mode=None)
    assert filter_execution_questions([combined],manual)==[combined]
    assert filter_execution_questions([{'key':'schedule.time','text':'시각은?'}],manual)==[]


def test_an_incomplete_time_stays_a_question_without_inventing_nine_oclock():
    current=state('매일 오전에 자료를 정리해줘.',execution_mode=None)
    question={'key':'schedule.time','text':'실행 시각은 몇 시인가요?'}
    assert effective_execution_mode(current)=='scheduled'
    assert requested_schedule(current) is None
    assert filter_execution_questions([question],current)==[question]
    draft={'unsupported':[],'schedule':{'frequency':'daily','hour':9,'minute':0,'timezone':'Asia/Seoul','weekday':None}}
    pending=[]
    validate_execution_mode(current,draft,pending)
    assert draft['schedule'] is None and pending


def test_a_scheduled_query_can_still_reference_an_earlier_data_period():
    current=state('매일 오전 9시에 어제 받은 메일을 요약해줘.')
    assert effective_execution_mode(current)=='scheduled'
    assert requested_schedule(current)['hour']==9


def test_weekly_missing_weekday_is_asked_without_reasking_the_complete_clock():
    current=state('매주 오전 9시에 자료를 정리해줘.',execution_mode=None)
    questions=[{'key':'schedule.time','text':'실행 시각은?'},{'key':'schedule.weekday','text':'무슨 요일인가요?'}]
    assert filter_execution_questions(questions,current)==[questions[1]]


def test_confirming_without_schedule_wins_over_requested_recurrence():
    current=state('매일 오전 9시에 자료를 정리해줘.',execution_mode=None,include_schedule=False)
    assert effective_execution_mode(current)=='manual'
    assert requested_schedule(current) is None


def test_native_schedule_uses_evidence_instead_of_a_model_default():
    current=state('매일 오전 9시에 자료를 정리해줘.',execution_mode=None)
    draft={'unsupported':[],'schedule':{'frequency':'daily','hour':10,'minute':30,'timezone':'Asia/Seoul','weekday':None}}
    questions=[]
    assert validate_execution_mode(current,draft,questions)==[]
    assert draft['schedule']['hour']==9 and draft['schedule']['minute']==0
    assert not questions


def test_legacy_saved_schedule_validation_preserves_the_persisted_configuration():
    current=state('첨부 자료를 정리해줘.')
    draft={'unsupported':[],'schedule':{'frequency':'daily','hour':10,'minute':30,'timezone':'Asia/Seoul','weekday':None}}
    before=deepcopy(draft)
    assert validate_execution_mode(current,draft,[])==[] and draft==before
    assert validate_execution_mode({**current,'execution_mode':None},deepcopy(draft),[])


def test_a_legacy_scheduled_choice_still_requests_only_missing_values():
    current=state('자료를 정리해줘.',execution_mode='scheduled')
    draft={'unsupported':[],'schedule':None};questions=[]
    assert validate_execution_mode(current,draft,questions)==[]
    assert questions


def test_unsupported_recurrence_is_not_silently_rewritten_to_daily():
    current=state('매년 1월 1일 오전 9시에 자료를 정리해줘.',execution_mode=None)
    assert effective_execution_mode(current)=='scheduled'
    assert infer_execution_intent(current)['unsupported'] is True
    assert requested_schedule(current) is None
