"""The conversation owner is Deep Agents, including clarification and repairs."""
import json
from copy import deepcopy
from types import SimpleNamespace
import pytest
from contracts.models import ExecutionPlan
from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.nodes.supervisor import supervisor_node
from tests.workflow_engine.test_workflow import node, bind, edge


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


def test_clarification_does_not_expose_executable_partial_workflow(monkeypatch):
    workflow={'entry':'read','nodes':[node('read','read_table')],'edges':[]}
    class Agent:
        def invoke(self,payload,config):return {'structured_response':draft(questions=['기준은 얼마인가요?'],workflow=workflow)}
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kwargs:Agent())
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
                assert context['supported_tools']==TOOLS
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
            assert context['supported_tools']==TOOLS
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
    assert len(schema['$defs']['WorkflowNode']['anyOf']) == len(TOOLS)
    assert schema['properties']['questions']['maxItems'] == 3
    assert 'minItems' not in schema['$defs']['WorkflowDefinition']['properties']['nodes']
    assert captured['context']['supported_tools'] == TOOLS
    assert 'enum' not in schema['$defs']['WorkflowDefinition']['properties']['entry']
    assert schema['$defs']['WorkflowNode']['anyOf'][0]['properties']['id']['pattern'] == r'^[a-zA-Z][\w-]{0,39}$'
    assert len(schema['$defs']['WorkflowNode']['anyOf'][0]['properties']['settings']['properties']) > 4
