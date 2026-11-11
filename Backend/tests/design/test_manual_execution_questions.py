from copy import deepcopy

import pytest

from agent_builder.design.execution_mode import (
    execution_schedule_question,project_execution_questions,reconcile_manual_capability,
)
from agent_builder.design.question_controls import complete_draft_question_controls,complete_questions


@pytest.mark.parametrize('question',[
    {'key':'schedule.time','text':'시간은 무엇인가요?'},
    {'text':'실행 시각 (시간)에 대해 알려주세요.'},
    {'text':'매일 오전의 구체적인 시간을 알려주세요. (예: 09:00)'},
    {'text':'언제 반복 실행할까요? 주기와 실행 시각을 알려주세요.'},
])
def test_manual_omits_native_trigger_questions_and_scheduled_keeps_them(question):
    assert execution_schedule_question(question)
    assert complete_questions([question],{'execution_mode':'manual'})==[]
    assert len(complete_questions([question],{'execution_mode':'scheduled'}))==1


@pytest.mark.parametrize('text',[
    '조회할 메일의 기간은 언제부터 언제까지인가요?',
    '캘린더에 등록할 회의 시작 시각은 몇 시인가요?',
    '업무 제출 마감 시간은 몇 시인가요?',
    '날짜 열의 시간 변환 형식은 무엇인가요?',
])
def test_manual_retains_business_times(text):
    assert not execution_schedule_question({'text':text})
    assert complete_questions([{'text':text}],{'execution_mode':'manual'})[0]['text']==text


def test_manual_filters_all_aligned_draft_metadata_without_changing_the_task():
    original={'name':'원래 업무','workflow':{'entry':'same'},'questions':[
        '실행 시각은 몇 시인가요?','파일 형식을 선택해주세요. (예: CSV, JSON)'],
        'question_options':[[],['CSV','JSON']],'question_responses':[{'type':'time'},{'type':'single_choice'}]}
    before=deepcopy(original)
    result=complete_draft_question_controls(original,{'execution_mode':'manual'})
    assert result['questions']==[original['questions'][1]]
    assert result['question_options']==[['CSV','JSON']]
    assert result['question_responses']==[{'type':'single_choice'}]
    assert result['workflow']==original['workflow'] and original==before


def test_old_interrupted_schedule_only_questions_offer_retry_without_claiming_readiness():
    text='실행 시각은 몇 시인가요?'
    state={'execution_mode':'manual','status':'needs_clarification',
        'pending_questions':[{'id':'old-id','key':'schedule.time','text':text}],
        'draft':{'questions':[text],'question_options':[[]],'question_responses':[{'type':'time'}]}}
    result=project_execution_questions(state)
    assert not result['pending_questions'] and not result['draft']['questions']
    assert result['status']=='needs_clarification'
    assert result['requirement_failure']['retryable'] is True


def test_capability_removes_only_trigger_gaps_and_never_invents_tools():
    packet={'decision':'clarify','tool_ids':['read_table','write_result'],
        'missing_conditions':['실행 시각은 몇 시인가요?'],'missing_actions':[],
        'allows_generation':False,'allows_execution':False}
    result=reconcile_manual_capability(packet,{'execution_mode':'manual'})
    assert result['decision']=='existing_tools' and result['tool_ids']==packet['tool_ids']
    assert result['allows_generation'] is False and result['allows_execution'] is False
    assert reconcile_manual_capability({**packet,'tool_ids':[]},{'execution_mode':'manual'})['decision']=='manual_review'
    mixed=reconcile_manual_capability({**packet,'missing_conditions':packet['missing_conditions']+['조회 기간은 무엇인가요?']},{'execution_mode':'manual'})
    assert mixed['decision']=='clarify' and mixed['missing_conditions']==['조회 기간은 무엇인가요?']


def test_direct_planner_does_not_reask_schedule_in_manual_mode(monkeypatch):
    from tests.design.test_direct_conversation import Fake,draft,planner,state
    fake=Fake({**draft(),'questions':['실행 시각은 몇 시인가요?','파일 형식은 CSV 또는 JSON 중 무엇으로 만들까요?']})
    result=planner(monkeypatch,fake)({**state(),'execution_mode':'manual'})
    assert len(fake.calls)==1
    assert result['draft']['questions']==['파일 형식은 CSV 또는 JSON 중 무엇으로 만들까요?']
    assert result['draft']['question_options']==[['CSV','JSON']]


def test_deep_planner_does_not_reask_schedule_in_manual_mode(monkeypatch):
    from agent_builder.design.nodes.conversation import make_conversation
    from tests.design.test_deep_agent_conversation import draft,state
    class Agent:
        def invoke(self,payload,config):
            return {'structured_response':draft(questions=['실행 시각은 몇 시인가요?','파일 형식은 CSV 또는 JSON 중 무엇으로 만들까요?'])}
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **_:Agent())
    result=make_conversation(None,'test',model_factory=lambda:object())({**state([{'role':'user','content':'표를 정리해줘.'}]),'execution_mode':'manual'})
    assert result['draft']['questions']==['파일 형식은 CSV 또는 JSON 중 무엇으로 만들까요?']
    assert result['draft']['question_options']==[['CSV','JSON']]
