"""The conversation owner is Deep Agents, including clarification and repairs."""
import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from contracts.models import ExecutionPlan, QuestionResponse
from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.nodes.supervisor import supervisor_node
from tests.workflow_engine.test_workflow import node, bind, edge


def state(messages):
    return {'messages':messages,'resources':[],'issues':[],'repair_count':0,'node_library':[]}


def test_overlong_requirement_is_retried_before_state_update(monkeypatch):
    calls = []
    class Agent:
        def invoke(self, payload, config):
            calls.append(payload)
            value = draft(questions=['실제 미정 조건은 무엇인가요?'])
            if len(calls) == 1:
                value['requirements'][0]['description'] = 'x' * 1001
            return {'structured_response': value}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', lambda **kwargs: Agent())
    original = state([{'role':'user','content':'업무 조건을 확인해줘'}])
    result = make_conversation(None,'test',model_factory=lambda:object())(original)
    assert len(calls) == 2
    assert result['trace'][-1]['response_relation_repairs'] == 1
    assert result['draft']['requirements'][0]['description'] == '실제 사용자 요청'
    assert '1000' in calls[-1]['messages'][-1]['content']


def draft(questions=None,unsupported=None,workflow=None,question_responses=None):
    responses = question_responses if question_responses is not None else [{'type':'text'} for _ in questions or []]
    return {'name':'업무 설계','purpose':'요청 처리','instructions':'사용자 조건 적용','tool_ids':[],
        'document_ids':[],'requirements':[{'description':'실제 사용자 요청','evidence_id':'e1'}],
        'questions':questions or [],'question_options':[[] for _ in questions or []],
        'question_responses':[QuestionResponse.model_validate(response).model_dump() for response in responses],
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
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
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
                assert 'validate_workflow' not in tools
                return {'structured_response':draft(workflow=workflow)}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    original=state([{'role':'user','content':'집계 결과를 저장해줘'},{'role':'assistant','content':'기준은 얼마인가요?'},{'role':'user','content':'기준을 3건 이상으로 바꿔줘'}]);original['issues']=['앞 단계 출력 포트를 고쳐야 합니다.']
    result=make_conversation(None,'test',model_factory=lambda:object())(original)
    assert result['draft']['workflow']['nodes'][1]['settings']['execution_plan']['filters'][0]['value']==3
    assert result['trace'][-1]['final_code_validation'] is True
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


def test_clarification_preserves_graph_preview_but_never_approves_it(monkeypatch):
    workflow={'entry':'read','nodes':[node('read','read_table')],'edges':[]}
    class Agent:
        def invoke(self,payload,config):return {'structured_response':draft(questions=['기준은 얼마인가요?'],workflow=workflow)}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kwargs:Agent())
    original=state([{'role':'user','content':'큰 값만 남겨줘'}])
    result=make_conversation(None,'test',model_factory=lambda:object())(original)
    assert result['draft']['questions']==['기준은 얼마인가요?']
    assert result['draft']['workflow']['entry']=='read'
    assert result['draft']['tool_ids']==['read_table']
    assert validate_node({**original,**result})['status']=='needs_clarification'


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
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kwargs:Agent())
    invoke=lambda:make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'조건을 정리해줘'}]))
    if failures==3:
        with pytest.raises(StructuredOutputValidationError):invoke()
        assert len(calls)==3
    else:
        assert invoke()['draft']['questions']==['기준은 얼마인가요?']
        assert len(calls)==2


def test_unvalidated_ready_draft_is_code_checked_without_extra_model_call(monkeypatch):
    workflow={'entry':'read','nodes':[node('read','read_table')],'edges':[]};calls=[]
    def factory(**kwargs):
        tools={t.name:t for t in kwargs['tools']}
        class Agent:
            def invoke(self,payload,config):
                calls.append(payload)
                assert 'validate_workflow' not in tools
                return {'structured_response':draft(workflow=workflow)}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    result=make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'자료를 읽어줘'}]))
    assert len(calls)==1
    assert result['trace'][-1]['final_code_validation'] is True
    assert result['draft']['workflow']['entry']=='read'
    assert result['draft']['workflow']['nodes'][0]['tool']=='read_table'


def test_unsupported_request_does_not_ask_for_credentials_or_setup(monkeypatch):
    class Agent:
        def invoke(self,payload,config):return {'structured_response':draft(questions=['외부 시스템 계정을 알려주세요'],unsupported=['실제 전송 도구가 없습니다.'])}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kwargs:Agent())
    result=make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'외부 시스템에 전송해줘'}]))
    assert result['draft']['unsupported']==['실제 전송 도구가 없습니다.']
    assert result['draft']['questions']==[]
    assert result['draft']['workflow'] is None


def test_empty_output_is_regenerated_once_with_fresh_nested_checkpoint(monkeypatch):
    """The real failure shape cannot become a ready draft; a new agent repairs it."""
    workflow={'entry':'read','nodes':[node('read','read_table')],'edges':[]}
    calls=[];factories=[]
    def factory(**kwargs):
        factories.append(kwargs)
        tools={t.name:t for t in kwargs['tools']}
        class Agent:
            def invoke(self,payload,config):
                tools['list_tools'].invoke({})
                calls.append(payload)
                if len(calls)==1:return {'structured_response':draft(workflow=None)}
                assert 'workflow가 없고 questions와 unsupported도 비어' in payload['messages'][-1]['content']
                return {'structured_response':draft(workflow=workflow)}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    s=state([{'role':'user','content':'자료를 읽어줘'}])
    result=make_conversation(None,'test',model_factory=lambda:object())(s)
    assert len(calls)==2 and factories[1]['checkpointer'] is False
    assert result['trace'][-1]['response_relation_repairs']==1
    assert result['trace'][-1]['final_code_validation'] is True
    assert result['confirmed'] is False and result['saved_agent_id'] is None
    assert validate_node({**s,**result})['status']=='ready'


def test_repeated_empty_output_stops_after_one_repair_without_partial_draft(monkeypatch):
    calls=[];factories=[]
    def factory(**kwargs):
        factories.append(kwargs)
        class Agent:
            def invoke(self,payload,config):calls.append(payload);return {'structured_response':draft(workflow=None)}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    s=state([{'role':'user','content':'자료를 읽어줘'}]);before=deepcopy(s)
    with pytest.raises(ValueError,match='한 번 다시 생성했지만'):
        make_conversation(None,'test',model_factory=lambda:object())(s)
    assert s==before and len(calls)==2 and factories[1]['checkpointer'] is False


@pytest.mark.parametrize('questions,unsupported',[(['기준을 알려주세요'],[]),([],['외부 발송 도구 없음'])])
def test_real_question_or_unsupported_with_null_workflow_is_not_regenerated(monkeypatch,questions,unsupported):
    calls=[]
    class Agent:
        def invoke(self,payload,config):calls.append(1);return {'structured_response':draft(questions,unsupported)}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kw:Agent())
    result=make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'업무 요청'}]))
    assert len(calls)==1 and result['trace'][-1]['response_relation_repairs']==0
    assert result['draft']['questions']==questions and result['draft']['unsupported']==unsupported


def test_unreadable_json_gets_one_fresh_repair(monkeypatch):
    from langchain_core.messages import AIMessage
    calls=[];factories=[]
    def factory(**kwargs):
        factories.append(kwargs)
        tools={t.name:t for t in kwargs['tools']}
        class Agent:
            def invoke(self,payload,config):
                tools['list_tools'].invoke({})
                calls.append(payload)
                if len(calls)==1:return {'messages':[AIMessage(content='not json')]}
                assert '유효한 JSON 객체로 읽을 수 없거나' in payload['messages'][-1]['content']
                return {'structured_response':draft(questions=['기준을 알려주세요'])}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    result=make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'기준을 정해줘'}]))
    assert len(calls)==2 and factories[-1]['checkpointer'] is False
    assert result['draft']['questions']==['기준을 알려주세요'] and not result['confirmed']


def test_unreadable_json_twice_stops(monkeypatch):
    from langchain_core.messages import AIMessage
    calls=[]
    def factory(**kwargs):
        tools={t.name:t for t in kwargs['tools']}
        class Agent:
            def invoke(self,payload,config):
                tools['list_tools'].invoke({});calls.append(1);return {'messages':[AIMessage(content='not json')]}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    with pytest.raises(ValueError,match='한 번 다시 생성'):
        make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'업무 요청'}]))
    assert len(calls)==2

def test_skipped_tool_selection_continues_once_with_context_catalog(monkeypatch):
    """Real Ollama shape: forced tool_choice ignored, model answers in prose."""
    from langchain_core.messages import AIMessage
    from workflow_engine.catalog.registry import TOOLS
    calls=[];factories=[]
    def factory(**kwargs):
        factories.append(kwargs)
        class Agent:
            def invoke(self,payload,config):
                calls.append(payload)
                if len(calls)==1:return {'messages':[AIMessage(content='기준이 모호하여 질문합니다.')]}
                context=json.loads(payload['messages'][0]['content'].split('\n',1)[1])
                from workflow_engine.catalog.canonical import planner_catalog
                assert context['supported_tools']==planner_catalog(TOOLS)
                return {'structured_response':draft(questions=['기준을 알려주세요'])}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    s=state([{'role':'user','content':'업무 요청'}]);before=deepcopy(s)
    result=make_conversation(None,'test',model_factory=lambda:object())(s)
    assert len(calls)==2 and factories[1]['checkpointer'] is False and calls[0]==calls[1]
    assert result['draft']['questions']==['기준을 알려주세요'] and not result['confirmed']
    assert result['trace'][-1]['tool_selection_fallback'] is True and s==before


def test_skipped_tool_selection_fallback_is_bounded(monkeypatch):
    from langchain_core.messages import AIMessage
    calls=[]
    class Agent:
        def invoke(self,payload,config):calls.append(1);return {'messages':[AIMessage(content='도구를 확인하겠습니다')]}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kw:Agent())
    with pytest.raises(ValueError,match='한 번 다시 생성'):
        make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'업무 요청'}]))
    # tool selection + one context-catalog draft stage + one existing JSON repair.
    assert len(calls)==3

def test_direct_catalog_supplies_registry_without_model_tool_call(monkeypatch):
    from workflow_engine.catalog.registry import TOOLS
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE','direct')
    class Agent:
        def invoke(self,payload,config):
            context=json.loads(payload['messages'][0]['content'].split('\n',1)[1])
            from workflow_engine.catalog.canonical import planner_catalog
            assert context['supported_tools']==planner_catalog(TOOLS)
            return {'structured_response':draft(questions=['기준을 알려주세요'])}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kw:Agent())
    result=make_conversation(None,'test',model_factory=lambda:object())(state([{'role':'user','content':'업무 요청'}]))
    assert result['draft']['questions']==['기준을 알려주세요']

@pytest.mark.parametrize('mode,expected_output,expected_count', [('off','typo',0),('safe','table',1)])
def test_optional_binding_repair_in_generation_is_audited(monkeypatch,mode,expected_output,expected_count):
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE','direct')
    monkeypatch.setenv('A4B_BINDING_REPAIR_MODE',mode)
    w={'entry':'read','nodes':[node('read','read_table',file_ids=['asset']),
       node('save','write_result',[bind('table','read','typo')],format='json')],
       'edges':[edge('read','save')]}
    class Agent:
        def invoke(self,payload,config):return {'structured_response':draft(workflow=w)}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kw:Agent())
    s=state([{'role':'user','content':'표를 저장해줘'}])
    s['resources']=[{'id':'asset','kind':'table','name':'a.csv','columns':['value'],'profile':{}}]
    result=make_conversation(None,'test',model_factory=lambda:object())(s)
    assert result['draft']['workflow']['nodes'][1]['bindings'][0]['output']==expected_output
    assert len(result['trace'][-1]['binding_repairs'])==expected_count
    assert not result['confirmed'] and result['saved_agent_id'] is None

def test_length_error_is_not_parsed_as_json_and_retries_once_with_the_next_seed(monkeypatch):
    """A failed attempt leaves no state, so the next seed is tried here once, then reported."""
    from openai import LengthFinishReasonError
    from openai.types.chat import ChatCompletion
    calls=[];seeds=[];factories=[]
    def factory(**kwargs):
        factories.append(kwargs)
        tools={t.name:t for t in kwargs['tools']}
        class Agent:
            def invoke(self,payload,config):
                tools['list_tools'].invoke({}); calls.append(1)
                completion=ChatCompletion.model_construct(choices=[],usage=None)
                raise LengthFinishReasonError(completion=completion)
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_chat_model',lambda **kw:seeds.append(kw['seed']) or object())
    with pytest.raises(ValueError,match='길이 제한'):
        make_conversation(None,'test')(state([{'role':'user','content':'업무 요청'}]))
    assert len(calls)==2 and seeds==[42,43] and factories[1]['checkpointer'] is False


def test_local_output_overflow_expands_once_without_changing_request_or_schema(monkeypatch):
    from openai import LengthFinishReasonError
    from openai.types.chat import ChatCompletion
    models=[];payloads=[];factories=[]
    class Agent:
        def invoke(self,payload,config):
            payloads.append(deepcopy(payload))
            raise LengthFinishReasonError(completion=ChatCompletion.model_construct(choices=[],usage=None))
    def factory(**kwargs):factories.append(kwargs);return Agent()
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE','model')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_chat_model',lambda **kwargs:models.append(kwargs) or object())
    original=state([{'role':'user','content':'표를 계산하고 집계해서 저장해줘.'}])
    with pytest.raises(ValueError,match='길이 제한'):
        make_conversation(None,'test')(original)
    assert [m['max_tokens'] for m in models]==[4096,8192]
    assert [m['seed'] for m in models]==[42,43]
    assert len(payloads)==2 and payloads[0]==payloads[1]
    assert factories[0]['response_format'].schema==factories[1]['response_format'].schema
    assert not original.get('saved_agent_id')


@pytest.mark.parametrize('ceiling,expected',[
    (None,[4096,8192]),('',[4096,8192]),('2048',[2048]),('4096',[4096]),
    ('6000',[4096,6000]),('8192',[4096,8192]),('12000',[4096,8192]),
])
def test_length_recovery_respects_the_actual_ollama_wire_budget(monkeypatch,ceiling,expected):
    """Use the real model factory/serializer: a mock factory hid the ceiling regression."""
    from openai import LengthFinishReasonError
    from openai.types.chat import ChatCompletion
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','https://example.test/v1')
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE','direct')
    monkeypatch.setenv('A4B_REQUIREMENT_GENERATION_MODE','baseline')
    if ceiling is None:monkeypatch.delenv('A4B_OLLAMA_NUM_PREDICT',raising=False)
    else:monkeypatch.setenv('A4B_OLLAMA_NUM_PREDICT',ceiling)
    wires=[];requests=[]
    def factory(**kwargs):
        class Agent:
            def invoke(self,payload,config):
                requests.append(deepcopy(payload))
                wires.append(kwargs['model']._get_request_payload(payload['messages']))
                if len(wires)==1:
                    raise LengthFinishReasonError(completion=ChatCompletion.model_construct(choices=[],usage=None))
                return {'structured_response':draft(questions=['기준은 얼마인가요?'])}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    original=state([{'role':'user','content':'표를 계산하고 집계해서 저장해줘.'}])
    if len(expected)==1:
        with pytest.raises(ValueError,match='출력 상한'):
            make_conversation(None,'final664')(original)
    else:
        assert make_conversation(None,'final664')(original)['draft']['questions']==['기준은 얼마인가요?']
        assert requests[0]==requests[1]
    assert [wire['max_tokens'] for wire in wires]==expected
    assert all('max_completion_tokens' not in wire for wire in wires)
    assert not original.get('saved_agent_id')


def test_ollama_ceiling_does_not_change_selected_openai_account_recovery(monkeypatch):
    from openai import LengthFinishReasonError
    from openai.types.chat import ChatCompletion
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_MAIN_BASE_URL','https://example.test/v1')
    monkeypatch.setenv('A4B_OLLAMA_NUM_PREDICT','4096')
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE','direct')
    wires=[]
    def factory(**kwargs):
        class Agent:
            def invoke(self,payload,config):
                wires.append(kwargs['model']._get_request_payload(payload['messages']))
                raise LengthFinishReasonError(completion=ChatCompletion.model_construct(choices=[],usage=None))
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    with pytest.raises(ValueError,match='길이 제한'):
        make_conversation('test-real-openai-account','gpt-4.1-mini')(state([{'role':'user','content':'업무 요청'}]))
    assert [wire['max_completion_tokens'] for wire in wires]==[4096,8192]


def test_seed_counts_attempts_within_the_same_user_turn(monkeypatch):
    seeds=[]
    class Agent:
        def invoke(self,payload,config):return {'structured_response':draft(questions=['기준을 알려주세요'])}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kw:Agent())
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_chat_model',lambda **kw:seeds.append(kw['seed']) or object())
    node=make_conversation(None,'test')
    first=node(state([{'role':'user','content':'업무 요청'}]))
    assert seeds==[42] and first['trace'][-1]['seed']==42 and first['trace'][-1]['user_turn']==0
    retry=node({**state([{'role':'user','content':'업무 요청'}]),'trace':first['trace']})  # same turn again
    assert seeds[-1]==43 and retry['trace'][-1]['seed']==43
    later=state([{'role':'user','content':'업무 요청'},{'role':'assistant','content':'?'},{'role':'user','content':'100만원 초과'}])
    node({**later,'trace':retry['trace']})  # a new user turn starts again from the base
    assert seeds[-1]==42


@pytest.mark.parametrize('sequence', [
    ['schema', 'schema', 'length', 'ok'],
    ['length', 'schema', 'schema', 'ok'],
    ['schema', 'length', 'schema', 'ok'],
])
def test_mixed_schema_and_length_failures_return_a_draft(monkeypatch, sequence):
    from langchain.agents.structured_output import StructuredOutputValidationError
    from langchain_core.messages import AIMessage
    from openai import LengthFinishReasonError
    from openai.types.chat import ChatCompletion
    calls = []
    class Agent:
        def invoke(self, payload, config):
            outcome = sequence[len(calls)]; calls.append(outcome)
            if outcome == 'schema':
                raise StructuredOutputValidationError('response_format', ValueError('bad JSON'), AIMessage(content='{} {}'))
            if outcome == 'length':
                raise LengthFinishReasonError(completion=ChatCompletion.model_construct(choices=[], usage=None))
            return {'structured_response': draft(questions=['기준은 얼마인가요?'])}
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE', 'direct')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', lambda **kw: Agent())
    result = make_conversation(None, 'test', model_factory=lambda: object())(state([{'role': 'user', 'content': '업무 요청'}]))
    assert calls == sequence and result['draft']['questions'] == ['기준은 얼마인가요?']


def test_mixed_retry_exhaustion_reports_length_error_instead_of_none(monkeypatch):
    from langchain.agents.structured_output import StructuredOutputValidationError
    from langchain_core.messages import AIMessage
    from openai import LengthFinishReasonError
    from openai.types.chat import ChatCompletion
    calls = []
    class Agent:
        def invoke(self, payload, config):
            calls.append(1)
            if len(calls) <= 2:
                raise StructuredOutputValidationError('response_format', ValueError('bad JSON'), AIMessage(content='{} {}'))
            raise LengthFinishReasonError(completion=ChatCompletion.model_construct(choices=[], usage=None))
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE', 'direct')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', lambda **kw: Agent())
    with pytest.raises(ValueError, match='길이 제한'):
        make_conversation(None, 'test', model_factory=lambda: object())(state([{'role': 'user', 'content': '업무 요청'}]))
    assert len(calls) == 4


def test_seed_is_not_reused_after_successful_length_retry(monkeypatch):
    from openai import LengthFinishReasonError
    from openai.types.chat import ChatCompletion
    seeds, calls = [], []
    class Agent:
        def invoke(self, payload, config):
            calls.append(1)
            if len(calls) == 1:
                raise LengthFinishReasonError(completion=ChatCompletion.model_construct(choices=[], usage=None))
            return {'structured_response': draft(questions=['기준은 얼마인가요?'])}
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE', 'direct')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', lambda **kw: Agent())
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_chat_model', lambda **kw: seeds.append(kw['seed']) or object())
    node = make_conversation(None, 'test')
    original = state([{'role': 'user', 'content': '업무 요청'}])
    first = node(original)
    node({**original, 'trace': first['trace']})
    assert seeds == [42, 43, 44]


@pytest.mark.parametrize('value', ['', '   '])
def test_empty_design_seed_uses_default(monkeypatch, value):
    seeds = []
    class Agent:
        def invoke(self, payload, config):return {'structured_response': draft(questions=['기준은 얼마인가요?'])}
    monkeypatch.setenv('A4B_DESIGN_SEED', value)
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', lambda **kw: Agent())
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_chat_model', lambda **kw: seeds.append(kw['seed']) or object())
    make_conversation(None, 'test')(state([{'role': 'user', 'content': '업무 요청'}]))
    assert seeds == [42]


def test_invalid_design_seed_reports_configuration_error(monkeypatch):
    monkeypatch.setenv('A4B_DESIGN_SEED', 'not-a-number')
    with pytest.raises(ValueError, match='A4B_DESIGN_SEED.*정수'):
        make_conversation(None, 'test', model_factory=lambda: object())


def run_capturing(monkeypatch, s, response):
    captured = {}
    def factory(**kwargs):
        captured['schema'] = kwargs['response_format'].schema
        tools = {t.name: t for t in kwargs['tools']}
        class Agent:
            def invoke(self, payload, config):
                captured['context'] = json.loads(payload['messages'][0]['content'].split('\n', 1)[1])
                captured['list_tools'] = tools['list_tools'].invoke({})
                return {'structured_response': response}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent', factory)
    captured['result'] = make_conversation(None, 'test', model_factory=lambda: object())(s)
    return captured


def test_ready_table_contract_bounds_deep_agent_output_and_catalog(monkeypatch):
    """Real failures: one-node truncation, extra finish nodes, re-asking confirmed conditions,
    and a ~9.5k-token prompt from the 22-tool catalogue in a 16k context."""
    from tests.design.test_linear_plan import context
    from agent_builder.design.planner_constraints import TABLE_PLAN_TOOLS
    s = context()
    captured = run_capturing(monkeypatch, s, draft(questions=['다시 묻기']))
    schema = captured['schema']; defs = schema['$defs']
    tools = {defs['WorkflowNode']['anyOf'][i]['properties']['tool']['enum'][0] for i in range(len(defs['WorkflowNode']['anyOf']))}
    assert tools == set(TABLE_PLAN_TOOLS)
    for key in ('questions', 'question_options', 'unsupported'):
        assert schema['properties'][key]['maxItems'] == 0
    assert schema['properties']['workflow'] == {'$ref': '#/$defs/WorkflowDefinition'}
    nodes, edges = defs['WorkflowDefinition']['properties']['nodes'], defs['WorkflowDefinition']['properties']['edges']
    assert (nodes['minItems'], nodes['maxItems'], edges['minItems'], edges['maxItems']) == (3, 4, 2, 3)  # format confirmed
    assert [t['id'] for t in captured['context']['supported_tools']] == list(TABLE_PLAN_TOOLS)
    assert [t['id'] for t in captured['list_tools']] == list(TABLE_PLAN_TOOLS)


def test_ready_table_contract_fixes_node_ids_and_single_typed_bindings(monkeypatch):
    """Real failures (live runs, 2026-11-02): library UUIDs copied as node IDs and corrupted
    ('c2f3e8c4-5d2 B, '), invented nodes (start_node_1, filter_sort_flow_1), and finish
    bindings in the valid shape in only 7 of 24 drafts (text port, two sources, duplicates)."""
    from tests.design.test_linear_plan import context
    from agent_builder.design.nodes.conversation import SCOPED_NODE_IDS
    captured = run_capturing(monkeypatch, context(), draft(questions=['다시 묻기']))
    defs = captured['schema']['$defs']
    variants = {v['properties']['tool']['enum'][0]: v['properties'] for v in defs['WorkflowNode']['anyOf']}
    assert {tool: props['id'] for tool, props in variants.items()} == {
        tool: {'type': 'string', 'enum': [tool + '_1']} for tool in variants}
    assert defs['WorkflowDefinition']['properties']['entry']['enum'] == ['read_table_1']
    for key in ('source', 'target'):
        assert defs['WorkflowEdge']['properties'][key]['enum'] == list(SCOPED_NODE_IDS.values())
    assert variants['read_table']['bindings']['maxItems'] == 0
    expected = {'filter_sort': ['table'], 'write_result': ['table'], 'finish': ['artifact']}
    for tool, ports in expected.items():
        bindings = variants[tool]['bindings']
        assert (bindings['minItems'], bindings['maxItems']) == (1, 1)
        item = bindings['items']['properties']
        assert item['input']['enum'] == ports and item['output']['enum'] == ports
        assert tool + '_1' not in item['node_id']['enum']  # no self-binding


def test_ready_table_contract_decodes_only_runtime_settings_and_fills_the_rest(monkeypatch):
    """62% of a live draft was null settings keys the four table tools never read."""
    from tests.design.test_linear_plan import context
    from tests.conditions.test_condition_readiness import workflow_draft
    from agent_builder.design.nodes.conversation import SCOPED_SETTINGS, SETTINGS_DEFAULTS
    from contracts.models import NodeSettings
    s = context(); full = workflow_draft(s)
    ids = {'read': 'read_table_1', 'filter': 'filter_sort_1', 'out': 'write_result_1'}
    nodes = [{'id': ids[n['id']], 'tool': n['tool'], 'label': n['label'],
              'bindings': [{**b, 'node_id': ids[b['node_id']]} for b in n['bindings']],
              'settings': {k: n['settings'][k] for k in SCOPED_SETTINGS[n['tool']]}} for n in full['workflow']['nodes']]
    compact = {**draft(), 'execution_plan': full['execution_plan'], 'workflow': {'entry': 'read_table_1', 'nodes': nodes,
               'edges': [{'source': 'read_table_1', 'target': 'filter_sort_1', 'branch': 'next'},
                         {'source': 'filter_sort_1', 'target': 'write_result_1', 'branch': 'next'}]}}
    captured = run_capturing(monkeypatch, s, compact)
    for variant in captured['schema']['$defs']['WorkflowNode']['anyOf']:
        tool = variant['properties']['tool']['enum'][0]
        settings = variant['properties']['settings']
        assert list(settings['properties']) == settings['required'] == list(SCOPED_SETTINGS[tool])
    produced = captured['result']['draft']['workflow']['nodes']
    assert [n['id'] for n in produced] == ['read_table_1', 'filter_sort_1', 'write_result_1']
    for n in produced:
        NodeSettings.model_validate(n['settings'])
        # canonical_table_draft writes the plan into filter_sort.instructions for display.
        unchanged = set(SETTINGS_DEFAULTS) - set(SCOPED_SETTINGS[n['tool']]) - ({'instructions'} if n['tool'] == 'filter_sort' else set())
        assert {k: n['settings'][k] for k in unchanged} == {k: SETTINGS_DEFAULTS[k] for k in unchanged}
    assert produced[1]['settings']['execution_plan'] == full['execution_plan']
    assert produced[2]['settings']['format'] == 'json'
    assert captured['result']['semantic_issues'] == []


def test_requests_outside_the_ready_table_contract_keep_the_full_catalog(monkeypatch):
    from workflow_engine.catalog.registry import TOOLS
    captured = run_capturing(monkeypatch, state([{'role': 'user', 'content': '문서에서 근거를 찾아 답해줘'}]),
                             draft(questions=['어떤 문서인가요?']))
    schema = captured['schema']
    assert {tool for v in schema['$defs']['WorkflowNode']['anyOf'] for tool in v['properties']['tool']['enum']} == {t['id'] for t in TOOLS}
    assert schema['properties']['questions']['maxItems'] == 3
    assert 'minItems' not in schema['$defs']['WorkflowDefinition']['properties']['nodes']
    from workflow_engine.catalog.canonical import planner_catalog
    assert captured['context']['supported_tools'] == planner_catalog(TOOLS)
    assert 'enum' not in schema['$defs']['WorkflowDefinition']['properties']['entry']
    assert schema['$defs']['WorkflowNode']['anyOf'][0]['properties']['id']['pattern'] == r'^[a-zA-Z][\w-]{0,39}$'
    variants={v['properties']['tool']['enum'][0]:v for v in schema['$defs']['WorkflowNode']['anyOf'] if len(v['properties']['tool']['enum'])==1}
    assert set(variants['read_table']['properties']['settings']['properties'])=={'file_ids','node_ref'}
    assert set(variants['write_result']['properties']['settings']['properties'])=={'format','node_ref'}
    assert schema['properties']['requirements']['minItems']==1


def test_unscoped_compact_native_settings_expand_before_validation(monkeypatch):
    workflow={'entry':'read','nodes':[
        {'id':'read','tool':'read_table','label':'표 읽기','bindings':[], 'settings':{'file_ids':[],'node_ref':None}},
        {'id':'out','tool':'write_result','label':'JSON 저장','bindings':[bind('table','read','table')],
         'settings':{'format':'json','node_ref':None}}], 'edges':[edge('read','out')]}
    original=state([{'role':'user','content':'실행할 때 받을 표를 JSON으로 저장해줘'}])
    captured=run_capturing(monkeypatch,original,draft(workflow=workflow))
    checked=validate_node({**original,**captured['result']})
    assert checked['issues']==[]
    assert checked['draft']['workflow']['nodes'][0]['settings']['query'] is None
    assert checked['draft']['workflow']['nodes'][1]['settings']['format']=='json'


@pytest.mark.parametrize('still_missing',[False,True])
def test_support_disagreement_is_rechecked_without_automatic_approval(monkeypatch,still_missing):
    calls=[]
    def factory(**kwargs):
        class Agent:
            def invoke(self,payload,*args,**kw):
                calls.append(payload)
                if len(calls)==1:return {'structured_response':draft(unsupported=['등록된 도구로 구현 가능합니다.'])}
                assert '지원 판정' in payload['messages'][-1]['content']
                if still_missing:return {'structured_response':draft(unsupported=['필수 Slack 전송 도구가 등록되지 않았습니다.'])}
                return {'structured_response':draft(workflow={'entry':'end','nodes':[node('end','finish')],'edges':[]})}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    original={**state([{'role':'user','content':'현재 요청을 확인해줘'}]),
              'capability_assessment':{'decision':'existing_tools','tool_ids':['finish']}}
    result=make_conversation('','test',model_factory=lambda:object())(original)
    assert len(calls)==2
    assert bool(result['draft']['unsupported']) is still_missing
    assert bool(result['draft']['workflow']) is not still_missing


def test_user_variable_names_are_available_in_ollama_decoder_without_setting_values(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    original={**state([{'role':'user','content':'x 값은 7, y 값은 2로 x+y를 계산해줘'}]),
              'capability_assessment':{'decision':'existing_tools','tool_ids':['scalar_calculate']}}
    captured=run_capturing(monkeypatch,original,draft(questions=['어떤 결과 형식인가요?']))
    variant=next(v for v in captured['schema']['$defs']['WorkflowNode']['anyOf'] if v['properties']['tool']['enum']==['scalar_calculate'])
    variables=variant['properties']['settings']['properties']['arguments_json']['properties']['variables']
    assert variables['properties']['x']==variables['properties']['y']=={'type':'number'}
    assert variables['additionalProperties']=={'type':'number'}


def test_ollama_default_catalog_mode_supplies_selected_contract_without_forced_lookup(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_TOOL_CATALOG_MODE','model')
    s={**state([{'role':'user','content':'x=3으로 x*2를 계산해줘'}]),
       'capability_assessment':{'decision':'existing_tools','tool_ids':['scalar_calculate']}}
    raw=draft(workflow={'first_step':{'tool':'scalar_calculate','label':'계산','settings':{
        'operation':'evaluate','arguments_json':{'formula':'x*2','variables':{'x':3}},'output_port':None}},'next_steps':[]})
    captured=run_capturing(monkeypatch,s,raw)
    assert captured['context']['selected_tool_contracts'][0]['id']=='scalar_calculate'
    assert set(captured['schema']['$defs']['WorkflowDefinition']['properties'])=={'first_step','next_steps'}
    assert captured['result']['trace'][-1]['tool_selection_fallback'] is False
    assert captured['result']['draft']['workflow']['nodes'][0]['id']=='step_1'


def test_cancelled_send_is_removed_from_previous_draft_tool_candidates(monkeypatch):
    from tests.workflow_engine.test_workflow import node

    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    s = state([{'role':'user', 'content':'파일을 만들고 이메일로 보내줘'},
               {'role':'user', 'content':'메일은 취소'}])
    s['capability_assessment'] = {'decision':'existing_tools', 'tool_ids':['write_result']}
    s['draft'] = draft(workflow={'entry':'mail', 'nodes':[node('mail', 'email.send')], 'edges':[]})
    captured = run_capturing(monkeypatch, s, draft(questions=['저장할 형식을 알려주세요.']))
    assert [tool['id'] for tool in captured['context']['selected_tool_contracts']] == ['write_result']


def test_local_discovery_summary_keeps_all_tools_and_inspection_contracts(monkeypatch):
    from workflow_engine.catalog.registry import TOOLS
    from workflow_engine.catalog.canonical import planner_catalog
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    original=state([{'role':'user','content':'x=3으로 x*2를 계산해줘'}])
    original['capability_assessment']={'decision':'existing_tools','tool_ids':['scalar_calculate']}
    def factory(**kwargs):
        tools={t.name:t for t in kwargs['tools']}
        class Agent:
            def invoke(self,payload,config):
                context=json.loads(payload['messages'][0]['content'].split('\n',1)[1])
                discovery=context['supported_tools']
                assert [t['id'] for t in discovery]==[t['id'] for t in TOOLS]
                assert tools['list_tools'].invoke({})==discovery
                assert len(json.dumps(discovery)) < len(json.dumps(planner_catalog(TOOLS)))*0.65
                assert all('native_contract' not in t and 'operation_contracts' not in t for t in discovery)
                detailed=tools['inspect_tool'].invoke({'tool_id':'text_hash'})
                assert detailed['id']=='text_hash' and detailed['native_contract']['input_ports']=={'text':'text'}
                assert context['selected_tool_contracts'][0]['operation_contracts']
                return {'structured_response':draft(questions=['출력 형식을 알려주세요'])}
        return Agent()
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    result=make_conversation(None,'test',model_factory=lambda:object())(original)
    assert result['draft']['questions']==['출력 형식을 알려주세요']


def test_first_step_decoder_cannot_drop_required_input_or_reference_future_table(monkeypatch):
    from jsonschema import Draft202012Validator
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    original=state([{'role':'user','content':'첨부한 두 표를 id 기준으로 결합해줘'}])
    original['capability_assessment']={'decision':'existing_tools','tool_ids':['join_tables','calculate_columns','filter_sort']}
    original['resources']=[{'id':id,'kind':'table','columns':['id'],'evidence_text':'id','name':id,'status':'ready'} for id in ('a','b')]
    captured=run_capturing(monkeypatch,original,draft(questions=['결합 방식을 알려주세요']))
    definitions=captured['schema']['$defs']
    first=definitions['FirstBusinessStep']
    assert not any(v['properties']['tool']['enum']==['calculate_columns'] for v in first['anyOf'])
    assert not any(v['properties']['tool']['enum']==['filter_sort'] for v in first['anyOf'])
    validator=Draft202012Validator({'$ref':'#/$defs/FirstBusinessStep','$defs':definitions})
    constants={'keys':['id'],'how':'left','cardinality':'many_to_one','null_keys':'error','max_rows':100}
    join=dict(tool='join_tables',label='결합',settings={'operation':'join','output_port':None,
              'arguments_json':{**constants,'left':{'$binding':'left'},'right':{'$binding':'right'}}})
    assert not validator.is_valid(join)
    missing=deepcopy(join);missing['settings']['arguments_json']=constants
    assert not validator.is_valid(missing)
    literal=deepcopy(join)
    literal['settings']['arguments_json']={**constants,'left':{'rows':[{'id':'A'}]},'right':{'rows':[{'id':'A'}]}}
    validator.validate(literal)
    reader=dict(tool='read_table',label='읽기',settings={'file_ids':['a'],'node_ref':None})
    validator.validate(reader)
    # Subsequent steps still accept references to actual earlier readers.
    later=next(v for v in definitions['WorkflowNode']['anyOf']
               if v['properties']['tool']['enum']==['join_tables'] and v['properties']['settings']['properties']['operation']['enum']==['join'])
    assert any('$binding' in p.get('properties',{})
               for p in later['properties']['settings']['properties']['arguments_json']['properties']['left']['anyOf'])
