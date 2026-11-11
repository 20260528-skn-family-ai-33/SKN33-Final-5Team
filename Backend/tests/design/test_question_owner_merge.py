"""Typed question evidence keeps both legacy and current ownership contracts."""
import pytest

from agent_builder.design.nodes.await_input import await_input_node


@pytest.mark.parametrize('source',['requirement','requirements'])
def test_typed_requirement_reply_keeps_identity_and_never_enters_condition_channel(monkeypatch,source):
    question={'id':'1'*24,'key':'format','source':source,'text':'파일 형식을 선택해주세요.',
              'options':['CSV','JSON'],'response':{'type':'single_choice'}}
    current={'messages':[],'condition_enforced':True,'issues':[],
             'pending_questions':[question],'draft':{'questions':[question['text']]}}
    monkeypatch.setattr('agent_builder.design.nodes.await_input.interrupt',lambda _:{
        'type':'message','content':'파일 형식 → CSV','question_answers':[{'question_id':question['id'],'value':'CSV'}]})
    result=await_input_node(current)
    assert 'condition_answers' not in result
    [event]=result['requirement_answers']
    assert event['value']=='CSV' and event['question_ids']==[question['id']]
    assert event['candidate_keys']==['format'] and event['questions'][0]['source']==source


def test_free_text_spanning_requirement_and_condition_owners_is_not_attributed_twice(monkeypatch):
    pending=[{'id':'1'*24,'key':'format','source':'requirements','text':'파일 형식은?'},
             {'id':'2'*24,'key':'filter.value','source':'condition','text':'금액 기준은?'}]
    current={'messages':[],'condition_enforced':True,'issues':[],
             'pending_questions':pending,'draft':{'questions':[question['text'] for question in pending]}}
    monkeypatch.setattr('agent_builder.design.nodes.await_input.interrupt',lambda _:{'type':'message','content':'조건을 바꿀게요.'})
    result=await_input_node(current)
    assert result['messages']==[{'role':'user','content':'조건을 바꿀게요.'}]
    assert 'condition_answers' not in result and 'requirement_answers' not in result
