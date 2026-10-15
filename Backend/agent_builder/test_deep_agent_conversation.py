"""The conversation owner is Deep Agents, including clarification and repairs."""
import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from agent_builder.models import ExecutionPlan
from agent_builder.nodes.conversation import make_conversation
from agent_builder.nodes.validation import validate_node
from agent_builder.nodes.supervisor import supervisor_node
from agent_builder.test_workflow import node,bind,edge


def state(messages):
    return {'messages':messages,'resources':[],'issues':[],'repair_count':0,'node_library':[]}


def draft(questions=None,unsupported=None,workflow=None):
    return {'name':'업무 설계','purpose':'요청 처리','instructions':'사용자 조건 적용','tool_ids':[],
        'document_ids':[],'requirements':[{'description':'실제 사용자 요청','evidence_id':'e1'}],
        'questions':questions or [],'question_options':[[] for _ in questions or []],
        'unsupported':unsupported or [],'explanation':'요청을 확인했습니다.',
        'execution_plan':ExecutionPlan(filters=[],sort=[],match='all',missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='csv').model_dump(),
        'workflow':workflow,'schedule':None,'report_destination':None}


@pytest.mark.parametrize('questions,unsupported,route',[
    (['얼마 이상을 대상으로 할까요?'],[],'await_input'),
    ([],['연결되지 않은 외부 서비스 전송은 지원하지 않습니다.'],'unsupported')])
def test_deep_agent_owns_question_and_support_decision(monkeypatch,questions,unsupported,route):
    seen=[]
    def factory(**kwargs):
        tools={t.name:t for t in kwargs['tools']}
        class Agent:
            def invoke(self,payload,config):
                seen.append(payload['messages']);assert tools['list_tools'].invoke({})
                return {'structured_response':draft(questions,unsupported)}
        return Agent()
    monkeypatch.setattr('agent_builder.nodes.conversation.create_deep_agent',factory)
    original=state([{'role':'user','content':'요청 기준을 확인해줘'}])
    # No with_structured_output method: separate question/reviewer models cannot run.
    result=make_conversation(None,'test',model_factory=lambda:object())(original)
    assert result['draft']['questions']==questions
    assert result['draft']['unsupported']==unsupported
    checked=validate_node({**original,**result})
    assert supervisor_node({**original,**result,**checked})['route']==route
    assert not checked['issues']
    assert len(seen)==1


def test_followup_and_validation_feedback_reach_same_deep_agent(monkeypatch):
    plan=ExecutionPlan(filters=[{'field':'count','operator':'gte','value':3,'compare_field':None,'value_type':'number'}],sort=[],match='all',missing_policy='keep',duplicate_policy='keep',duplicate_field=None,output_format='csv').model_dump()
    workflow={'entry':'read','nodes':[node('read','read_table'),node('filter','filter_sort',[bind('table','read','table')],execution_plan=plan),node('save','write_result',[bind('table','filter','table')],format='csv')],'edges':[edge('read','filter'),edge('filter','save')]}
    def factory(**kwargs):
        tools={t.name:t for t in kwargs['tools']}
        class Agent:
            def invoke(self,payload,config):
                context=json.loads(payload['messages'][0]['content'].split('\n',1)[1])
                assert context['validation_issues']==['앞 단계 출력 포트를 고쳐야 합니다.']
                assert payload['messages'][-1]['content']=='기준을 3건 이상으로 바꿔줘'
                bad=deepcopy(workflow);bad['nodes'][-1]['bindings'][0]['output']='nonexistent'
                assert not tools['validate_workflow'].invoke({'workflow_json':json.dumps(bad),'tool_ids':['read_table','filter_sort','write_result']})['valid']
                assert tools['validate_workflow'].invoke({'workflow_json':json.dumps(workflow),'tool_ids':['read_table','filter_sort','write_result']})['valid']
                return {'structured_response':draft(workflow=workflow)}
        return Agent()
    monkeypatch.setattr('agent_builder.nodes.conversation.create_deep_agent',factory)
    original=state([{'role':'user','content':'집계 결과를 저장해줘'},{'role':'assistant','content':'기준은 얼마인가요?'},{'role':'user','content':'기준을 3건 이상으로 바꿔줘'}]);original['issues']=['앞 단계 출력 포트를 고쳐야 합니다.']
    result=make_conversation(None,'test',model_factory=lambda:object())(original)
    assert result['draft']['workflow']['nodes'][1]['settings']['execution_plan']['filters'][0]['value']==3
    assert validate_node({**original,**result})['status']=='ready'


def test_code_validation_returns_unresolved_policy_to_agent_without_canned_question():
    plan=ExecutionPlan(filters=[],sort=[],match='all',missing_policy='keep',duplicate_policy='ask',duplicate_field='email',output_format='csv').model_dump()
    workflow={'entry':'read','nodes':[node('read','read_table'),node('filter','filter_sort',[bind('table','read','table')],execution_plan=plan)],'edges':[edge('read','filter')]}
    raw=draft(workflow=workflow);raw['requirements']=[{'description':'근거','source_id':'user','quote':'중복 문의를 정리해줘'}];raw['tool_ids']=['read_table','filter_sort']
    original=state([{'role':'user','content':'중복 문의를 정리해줘'}]);original['draft']=raw
    checked=validate_node(original)
    assert checked['draft']['questions']==[]
    assert any('중복 처리 정책' in issue for issue in checked['issues'])
    assert supervisor_node({**original,**checked})['route']=='repair'


def test_clarification_does_not_expose_executable_partial_workflow(monkeypatch):
    workflow={'entry':'read','nodes':[node('read','read_table')],'edges':[]}
    class Agent:
        def invoke(self,payload,config):return {'structured_response':draft(questions=['기준은 얼마인가요?'],workflow=workflow)}
    monkeypatch.setattr('agent_builder.nodes.conversation.create_deep_agent',lambda **kwargs:Agent())
    result=make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'큰 값만 남겨줘'}]))
    assert result['draft']['questions']==['기준은 얼마인가요?']
    assert result['draft']['workflow'] is None
    assert result['draft']['tool_ids']==[]


@pytest.mark.parametrize('failures',[1,3])
def test_invalid_structured_response_is_regenerated_with_bounded_agent_retry(monkeypatch,failures):
    from langchain.agents.structured_output import StructuredOutputValidationError
    from langchain_core.messages import AIMessage
    calls=[]
    class Agent:
        def invoke(self,payload,config):
            calls.append(payload)
            if len(calls)<=failures:
                raise StructuredOutputValidationError('response_format',ValueError('invalid JSON'),AIMessage(content='{} {}'))
            return {'structured_response':draft(questions=['기준은 얼마인가요?'])}
    monkeypatch.setattr('agent_builder.nodes.conversation.create_deep_agent',lambda **kwargs:Agent())
    invoke=lambda:make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'조건을 정리해줘'}]))
    if failures==3:
        with pytest.raises(StructuredOutputValidationError):invoke()
        assert len(calls)==3
    else:
        assert invoke()['draft']['questions']==['기준은 얼마인가요?']
        assert len(calls)==2


def test_unvalidated_ready_draft_is_returned_to_agent_for_tool_validation(monkeypatch):
    workflow={'entry':'read','nodes':[node('read','read_table')],'edges':[]};calls=[]
    def factory(**kwargs):
        tools={t.name:t for t in kwargs['tools']}
        class Agent:
            def invoke(self,payload,config):
                calls.append(payload)
                if len(calls)==2:
                    assert '검증이 아직 실행되지 않았습니다' in payload['messages'][-1]['content']
                    assert tools['validate_workflow'].invoke({'workflow_json':json.dumps(workflow),'tool_ids':['read_table']})['valid']
                return {'structured_response':draft(workflow=workflow)}
        return Agent()
    monkeypatch.setattr('agent_builder.nodes.conversation.create_deep_agent',factory)
    result=make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'자료를 읽어줘'}]))
    assert len(calls)==2
    assert result['draft']['workflow']['entry']=='read'
    assert result['draft']['workflow']['nodes'][0]['tool']=='read_table'


def test_unsupported_request_does_not_ask_for_credentials_or_setup(monkeypatch):
    class Agent:
        def invoke(self,payload,config):return {'structured_response':draft(questions=['외부 시스템 계정을 알려주세요'],unsupported=['실제 전송 도구가 없습니다.'])}
    monkeypatch.setattr('agent_builder.nodes.conversation.create_deep_agent',lambda **kwargs:Agent())
    result=make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'외부 시스템에 전송해줘'}]))
    assert result['draft']['unsupported']==['실제 전송 도구가 없습니다.']
    assert result['draft']['questions']==[]
    assert result['draft']['workflow'] is None
