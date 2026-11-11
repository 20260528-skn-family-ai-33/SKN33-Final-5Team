from copy import deepcopy

import pytest
from pydantic import ValidationError

from agent_builder.design.question_controls import project_question_controls
from agent_builder.design.service_connections import reconcile_connection_questions
from contracts.models import QuestionResponse
from service.worker import handle


@pytest.mark.parametrize('key,minimum,maximum,unit',[
    ('schedule.minute',0,59,'분'),('schedule.monthday',1,31,'일')])
def test_known_numeric_questions_expose_actual_bounds(key,minimum,maximum,unit):
    pending=[{'id':'existing-id','key':key,'text':'질문','options':[]}]
    result=project_question_controls({'pending_questions':pending,'draft':{'questions':['질문']}})
    response={'type':'number','minimum':minimum,'maximum':maximum,'step':1,'unit':unit}
    assert result['pending_questions'][0]['response']==response
    assert result['draft']['question_responses']==[response]
    assert result['pending_questions'][0]['id']=='existing-id'


def test_existing_choices_and_explicit_multiple_selection_are_distinct():
    state={'draft':{'questions':['형식?','항목?'], 'question_options':[['CSV','JSON'],['이름','날짜']],
                    'question_responses':[None,{'type':'multi_choice'}]}}
    original=deepcopy(state)
    result=project_question_controls(state)
    assert result['draft']['question_responses']==[{'type':'single_choice'},{'type':'multi_choice'}]
    assert state==original
    assert result['pending_questions']==[]


@pytest.mark.parametrize('pending_options', [['이름','날짜'], []])
def test_pending_inherits_declared_multiple_selection_before_fallback(pending_options):
    state={'pending_questions':[{'id':'existing-id','key':'fields','text':'항목?','options':pending_options}],
           'draft':{'questions':['항목?'],'question_options':[['이름','날짜']],
                    'question_responses':[{'type':'multi_choice'}]}}
    original=deepcopy(state)
    result=project_question_controls(state)
    assert result['pending_questions'][0]['response']=={'type':'multi_choice'}
    assert result['pending_questions'][0]['options']==['이름','날짜']
    assert result['pending_questions'][0]['id']=='existing-id'
    assert result['draft']['question_responses']==[{'type':'multi_choice'}]
    assert state==original


def test_pending_inherits_declared_numeric_response_before_text_fallback():
    response={'type':'number','minimum':0,'maximum':500,'unit':'건'}
    state={'pending_questions':[{'id':'existing-id','key':'limit','text':'몇 건?','options':[]}],
           'draft':{'questions':['몇 건?'],'question_responses':[response]}}
    result=project_question_controls(state)
    assert result['pending_questions'][0]['response']==response
    assert result['draft']['question_responses']==[response]


def test_pending_explicit_response_takes_priority_over_draft_declaration():
    state={'pending_questions':[{'id':'existing-id','key':'fields','text':'항목?',
                                 'options':['이름','날짜'],'response':{'type':'single_choice'}}],
           'draft':{'questions':['항목?'],'question_responses':[{'type':'multi_choice'}]}}
    result=project_question_controls(state)
    assert result['pending_questions'][0]['response']=={'type':'single_choice'}
    assert result['draft']['question_responses']==[{'type':'single_choice'}]


def test_numeric_threshold_does_not_invent_business_bounds():
    state={'condition_ledger':{'slots':{'filter.field':{'value':'가격'},'filter.operator':{'value':'gte'}}},
           'pending_questions':[{'id':'existing-id','key':'filter.value','text':'기준?','options':[]}]}
    response=project_question_controls(state)['pending_questions'][0]['response']
    assert response=={'type':'number'}


def test_unknown_question_text_is_not_guessed_or_assigned_an_id():
    state={'draft':{'questions':['몇 개의 파일과 어떤 금액을 사용할까요?']}}
    result=project_question_controls(state)
    assert result['draft']['question_responses']==[{'type':'text'}]
    assert 'id' not in result['draft']


def test_declared_numeric_input_preserves_zero_and_fractional_step():
    declared={'type':'number','minimum':0,'maximum':1,'step':0.01,'unit':'비율'}
    result=project_question_controls({'draft':{'questions':['비율?'],'question_responses':[declared]}})
    assert result['draft']['question_responses']==[declared]


@pytest.mark.parametrize('invalid',[
    {'type':'number','minimum':5,'maximum':0}, {'type':'number','step':0},
    {'type':'number','maximum':float('inf')}, {'type':'unknown'}])
def test_bad_response_metadata_falls_back_to_text(invalid):
    with pytest.raises(ValidationError):QuestionResponse.model_validate(invalid)
    result=project_question_controls({'draft':{'questions':['질문'],'question_responses':[invalid]}})
    assert result['draft']['question_responses']==[{'type':'text'}]


def test_choice_metadata_without_options_remains_answerable():
    result=project_question_controls({'draft':{'questions':['질문'],'question_responses':[{'type':'multi_choice'}]}})
    assert result['draft']['question_responses']==[{'type':'text'}]


def test_removed_connection_question_keeps_other_metadata_aligned():
    draft={'questions':['Gmail 계정을 연결해 주세요.','한도?'], 'question_options':[[],[]],
           'question_responses':[{'type':'text'},{'type':'number','minimum':1,'maximum':500}]}
    reconciled=reconcile_connection_questions(draft,{'gmail_connected':True})
    assert reconciled['questions']==['한도?']
    assert reconciled['question_responses']==[{'type':'number','minimum':1,'maximum':500}]


def test_generated_numeric_control_is_public_and_survives_session_reload(tmp_path):
    from tests.design.test_direct_conversation import draft
    session=handle({'directory':str(tmp_path),'action':'new_session','model':'gpt-4.1-mini',
                    'credential_id':'local','execution_mode':'manual'})
    response={'type':'number','minimum':0,'maximum':500,'step':1,'unit':'건'}
    def conversation(state):
        return {'draft':{**draft(),'workflow':None,'document_ids':[],
                         'questions':['한 번에 몇 건을 처리할까요?'],'question_responses':[response]}}
    result=handle({'directory':str(tmp_path),'action':'message','id':session['id'],'revision':0,
                   'content':'입력 표를 JSON으로 저장해줘'},conversation=conversation)
    reopened=handle({'directory':str(tmp_path),'action':'session','id':session['id']})
    assert result['draft']['question_responses']==[response]
    assert reopened['draft']['question_responses']==[response]
    assert reopened['pending_questions']==[]
