import pytest

from agent_builder.design.question_controls import project_question_controls
from agent_builder.design.question_answers import question_answers
from agent_builder.design.requirement_clarification import schedule_facts,missing_schedule_questions
from agent_builder.design.requirement_contracts import RequirementQuestion
from agent_builder.design.requirement_ledger import grounded_schedule
from contracts.models import QuestionResponse


def test_time_response_is_supported_and_legacy_clock_metadata_is_corrected():
    assert QuestionResponse(type='time').type=='time'
    for key,text in [('schedule.hour','몇 시에 실행할까요?'),('execution_time','시각을 입력해주세요.'),
                     ('custom.setting','몇 시에 실행할까요? 예: 09:00, 10:30')]:
        current={'pending_questions':[{'id':'1'*24,'key':key,'text':text,'response':{'type':'number'}}]}
        result=project_question_controls(current)
        assert result['pending_questions'][0]['response']=={'type':'time'}
        assert current['pending_questions'][0]['response']=={'type':'number'}
    assert RequirementQuestion(key='execution_time',text='실행 시각을 입력해주세요.',response={'type':'number'}).response.type=='time'


def test_business_quantities_remain_numbers():
    state={'pending_questions':[{'id':'1'*24,'key':'quantity','text':'몇 건을 처리할까요?',
                                 'response':{'type':'number','minimum':0}}]}
    assert project_question_controls(state)['pending_questions'][0]['response']=={'type':'number','minimum':0.0}


@pytest.mark.parametrize('value',['00:00','09:00','10:30','23:59'])
def test_time_answers_preserve_both_hour_and_minute(value):
    question={'id':'1'*24,'key':'schedule.time','text':'실행 시각은 언제인가요?','response':{'type':'time'}}
    current={'messages':[{'role':'user','content':'매일 실행해줘.'}], 'pending_questions':[question], 'execution_mode':'scheduled'}
    validated=question_answers([{'question_id':question['id'],'value':value}],current)
    assert validated[0]['value']==value
    message='실행 시각 → '+value
    current['messages'].append({'role':'user','content':message})
    current['requirement_answers']=[{'questions':[question],'answer':value,'value':value,'message_content':message,'turn':1}]
    facts=schedule_facts(current)
    hour,minute=map(int,value.split(':'))
    assert facts['schedule.hour']==hour and facts['schedule.minute']==minute
    assert missing_schedule_questions(current)==[]
    spec=grounded_schedule(current)
    assert (spec['hour'],spec['minute'],spec['timezone'])==(hour,minute,'Asia/Seoul')


@pytest.mark.parametrize('value',[9,'9','9:00','24:00','09:60','text',True])
def test_invalid_time_answers_are_actionable(value):
    state={'pending_questions':[{'id':'1'*24,'key':'schedule.time','text':'실행 시각', 'response':{'type':'time'}}]}
    with pytest.raises(ValueError,match='HH:MM'):question_answers([{'question_id':'1'*24,'value':value}],state)


def test_a_missing_daily_time_is_one_clock_control():
    state={'messages':[{'role':'user','content':'매일 오전에 보고서를 만들어줘.'}],'execution_mode':'scheduled'}
    questions=missing_schedule_questions(state)
    assert [question['key'] for question in questions]==['schedule.time']
    assert questions[0]['response']=={'type':'time'}


def test_fixed_timezone_does_not_turn_a_legacy_manual_request_into_a_reservation():
    assert missing_schedule_questions({'messages':[{'role':'user','content':'문서를 요약해줘.'}]})==[]


def test_confirmed_combined_time_is_not_asked_again():
    from agent_builder.design.requirement_contracts import validate_unanswered_questions
    current={'messages':[{'role':'user','content':'매일 오전 9시에 실행해줘.'}]}
    with pytest.raises(ValueError,match='이미 확인한 조건'):
        validate_unanswered_questions({'unresolved':[{'key':'schedule.time'}]},current)


def test_a_time_filter_answer_is_not_treated_as_a_workflow_reservation():
    message='09:30'
    current={'messages':[{'role':'user','content':message}],
             'requirement_answers':[{'questions':[{'key':'filter.time','text':'몇 시 이후의 기록을 볼까요? 예: 09:00',
                                                  'response':{'type':'time'}}],
                                     'answer':message,'value':message,'turn':0}]}
    facts=schedule_facts(current)
    assert 'schedule.hour' not in facts and 'schedule.minute' not in facts
