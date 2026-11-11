import pytest

from agent_builder.design.question_answers import question_answers
from agent_builder.design.nodes.await_input import await_input_node


def state():
    questions=[
        dict(id='1'*24,key='quantity',text='몇 건을 처리할까요?',source='requirements',options=[],response={'type':'number','minimum':0,'maximum':20,'step':1}),
        dict(id='2'*24,key='format',text='결과 형식을 선택해주세요.',source='requirements',options=['CSV','JSON'],response={'type':'single_choice'}),
        dict(id='3'*24,key='fields',text='포함할 항목을 선택해주세요.',source='requirements',options=['이름','날짜'],response={'type':'multi_choice'}),
        dict(id='4'*24,key='preference',text='선택할 선호가 있나요?',source='requirements',required=False,options=[],response={'type':'text'}),
    ]
    return {'pending_questions':questions,'draft':{'questions':[q['text'] for q in questions]},'messages':[{'role':'user','content':'자료를 정리해줘.'}], 'issues':[]}


def test_multiple_typed_answers_and_optional_skip_are_recorded_without_changing_user_text(monkeypatch):
    current=state();content='몇 건을 처리할까요? → 0\n결과 형식을 선택해주세요. → CSV'
    answers=[{'question_id':'1'*24,'value':0},{'question_id':'2'*24,'value':'CSV'},
             {'question_id':'4'*24,'value':None,'skip':True}]
    monkeypatch.setattr('agent_builder.design.nodes.await_input.interrupt',lambda payload:{'type':'message','content':content,'question_answers':answers})
    result=await_input_node(current)
    assert result['messages'][-1]['content']==content
    events=result['requirement_answers']
    assert events[0]['value']==0 and events[0]['answer']=='0'
    assert events[1]['questions'][0]['key']=='format' and events[1]['value']=='CSV'
    assert events[2]['skipped'] is True and events[2]['value'] is None
    assert all(event['message_content']==content and event['turn']==1 for event in events)


def test_multiple_selection_can_include_an_explicit_custom_format():
    result=question_answers([{'question_id':'3'*24,'value':['이름','담당자'],'custom':True}],state())
    assert result[0]['value']==['이름','담당자']


@pytest.mark.parametrize('answer',[
    {'question_id':'1'*24,'value':True},
    {'question_id':'1'*24,'value':21},
    {'question_id':'1'*24,'value':0.5},
    {'question_id':'1'*24,'value':None,'skip':True},
    {'question_id':'9'*24,'value':'CSV'},
    {'question_id':'2'*24,'value':'PDF'},
    {'question_id':'3'*24,'value':['이름','담당자']},
])
def test_invalid_or_stale_answers_are_not_applied(answer):
    with pytest.raises(ValueError):question_answers([answer],state())


def test_a_question_cannot_be_answered_twice_in_one_submission():
    with pytest.raises(ValueError):question_answers([{'question_id':'1'*24,'value':0}]*2,state())
