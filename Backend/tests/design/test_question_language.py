from copy import deepcopy

import pytest

from agent_builder.design.question_language import korean_clock_text, normalize_question_language, validate_question_language
from agent_builder.design.question_controls import project_question_controls
from agent_builder.design.question_language import validate_draft_question_language


@pytest.mark.parametrize('text,expected',[
    ('매일 메일을 정리할 구체적인 시각은 몇 시几分인가요? (예: 09:00, 10:30 등)',
     '매일 메일을 정리할 구체적인 시각은 몇 시 몇 분인가요? (예: 09:00, 10:30 등)'),
    ('몇 시幾分에 실행할까요?','몇 시 몇 분에 실행할까요?'),
    ('실행 時間은 几点인가요?','실행 시간은 몇 시인가요?'),
])
def test_mixed_clock_wording_becomes_korean(text,expected):
    assert korean_clock_text(text)==expected


def test_existing_question_copies_are_corrected_without_changing_user_text_or_answer_identity():
    text='매일 몇 시几分에 실행할까요? (예: 09:00)'
    state={'execution_mode':'scheduled','messages':[{'role':'user','content':'한자 열 時間도 원문으로 보존해줘.'},{'role':'assistant','content':'조건을 확인할게요.\n'+text}],
           'draft':{'questions':[text],'question_options':[[]],'question_responses':[{'type':'time'}]},
           'pending_questions':[{'id':'1'*24,'key':'schedule.time','text':text,'response':{'type':'time'},'options':[]}]}
    original=deepcopy(state);projected=normalize_question_language(state)
    assert state==original
    assert projected['messages'][0]==state['messages'][0]
    assert '几' not in projected['messages'][1]['content']
    controls=project_question_controls(projected)
    assert controls['pending_questions'][0]['id']=='1'*24
    assert controls['pending_questions'][0]['text']==controls['draft']['questions'][0]
    assert controls['pending_questions'][0]['response']=={'type':'time'}


def test_unexpected_model_written_chinese_requests_korean_repair():
    with pytest.raises(ValueError,match='한국어'):
        validate_question_language([{'text':'结果要怎样整理？'}],{'messages':[{'role':'user','content':'결과를 정리해줘.'}]})


def test_user_supplied_column_names_and_choice_values_are_preserved():
    questions=[{'text':'「年月」 열을 기준으로 정리할까요?','options':['年月','날짜']}]
    validate_question_language(questions,{'messages':[{'role':'user','content':'年月 열을 기준으로 정리해줘.'}]})
    assert questions[0]['options']==['年月','날짜']


def test_actual_source_row_values_remain_available_as_choices():
    questions=[{'text':'어느 지역을 선택할까요?','options':['上海','北京']}]
    validate_question_language(questions,{'resources':[{'columns':['지역'],'preview':[{'지역':'上海'},{'지역':'北京'}]}]})
    assert questions[0]['options']==['上海','北京']


@pytest.mark.parametrize('question',[
    {'text':'결과 文件 형식은 무엇인가요?'},
    {'text':'어떤 형식으로 만들까요?','options':['表格','목록']},
    {'text':'어떤 조건을 적용할까요?','reason':'条件을 정해야 합니다.'},
    {'text':'얼마나 처리할까요?','response':{'type':'number','unit':'条'}},
])
def test_non_time_questions_choices_reasons_and_units_share_the_language_guard(question):
    with pytest.raises(ValueError,match='한국어'):
        validate_question_language([question],{'messages':[{'role':'user','content':'결과를 정리해줘.'}]})


def test_both_planners_and_runtime_question_settings_use_the_same_guard():
    with pytest.raises(ValueError):validate_draft_question_language({'questions':['保存 형식은 무엇인가요?']},{})
    with pytest.raises(ValueError):validate_draft_question_language({'questions':[], 'workflow':{'nodes':[
        {'tool':'ask_user','settings':{'question':'이 内容을 승인할까요?'}}]}},{})


def test_old_non_time_questions_are_blocked_without_showing_untranslated_text():
    question={'id':'same-id','key':'format','text':'어떤 文件로 만들까요?','options':[]}
    state={'draft':{'questions':[question['text']],'question_options':[[]]},'pending_questions':[question],
           'messages':[{'role':'user','content':'CSV로 정리해줘.'},{'role':'assistant','content':question['text']}]}
    shown=normalize_question_language(state)
    assert shown['draft']['questions']==[] and shown['pending_questions']==[]
    assert '文件' not in shown['messages'][-1]['content']
    assert shown['requirement_failure']=={'kind':'question_language_error','retryable':True}


def test_direct_planner_rewrites_a_non_time_question_within_its_existing_retry_budget(monkeypatch):
    from tests.design.test_direct_conversation import Fake,draft,planner,state
    bad={**draft(),'questions':['어떤 文件 형식으로 만들까요?']}
    good={**draft(),'questions':['어떤 파일 형식으로 만들까요?']}
    fake=Fake(bad,good)
    # The request must leave the format open: a format the user already wrote is never asked again.
    result=planner(monkeypatch,fake)({**state(),'messages':[{'role':'user','content':'입력 표를 파일로 저장해줘'}]})
    assert result['draft']['questions']==good['questions']
    assert len(fake.calls)==2


def test_deep_planner_rewrites_non_time_questions_before_persisting_a_response(monkeypatch):
    from agent_builder.design.nodes.conversation import make_conversation
    from tests.design.test_deep_agent_conversation import draft,state
    calls=[]
    class Agent:
        def invoke(self,payload,config):
            calls.append(payload)
            return {'structured_response':draft(questions=['保存 형식은 무엇인가요?' if len(calls)==1 else '저장 형식은 무엇인가요?'])}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kwargs:Agent())
    result=make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'업무 조건을 확인해줘.'}]))
    assert result['draft']['questions']==['저장 형식은 무엇인가요?']
    assert len(calls)==2
