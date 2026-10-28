"""Real validator/adapter regressions; controlled extraction is not LLM accuracy."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from langchain_core.messages import HumanMessage
from agent_builder.condition_readiness import plan_issues
from agent_builder.input_resolution import resolution_options, answer_kind
from agent_builder.ollama_chat_model import compatible_schema_payload, OllamaChatOpenAI
from agent_builder.models import AgentDraft
from agent_builder.test_condition_readiness import state, proposal, checked, workflow_draft
from agent_builder.tools.filter_sort import filter_sort_node


def test_vague_prompt_does_not_confirm_guessed_operator_or_format():
    s=state('비용 큰 것들만 골라서 파일로 만들어줘.')
    result=checked(s,proposal(s,{'filter.field':'비용','filter.operator':'gte','filter.value':None,'output.format':'csv'}))
    assert result['decision']=='clarify'
    assert set(result['unresolved'])=={'filter.operator','filter.value','output.format'}
    assert all(result['slots'][k]['value'] is None for k in result['unresolved'])


@pytest.mark.parametrize('word,operator,ids', [('초과','gt',['C']),('이상','gte',['B','C']),('미만','lt',['A']),('이하','lte',['A','B'])])
def test_boundary_operator_corrected_from_literal_and_executed(word,operator,ids):
    s=state(f'비용 100만원 {word}만 JSON으로 저장해. 빈 값은 제외, 중복은 유지해.')
    result=checked(s,proposal(s))  # Deliberately wrong gt for three of these cases.
    assert result['decision']=='ready'
    assert result['slots']['filter.operator']['value']==operator
    ctx={'rows':[{'ID':'A','비용':999999},{'ID':'B','비용':1000000},{'ID':'C','비용':1000001}], 'columns':['ID','비용'],'file_id':'synthetic','trace':[]}
    filter_sort_node(ctx,[{'field':'비용','operator':operator,'value':1000000,'value_type':'number'}],[],missing_policy='exclude')
    assert [r['ID'] for r in ctx['rows']]==ids


def test_latest_explicit_answer_replaces_legacy_guessed_operator_and_preserves_policies():
    s=state('비용 큰 것들만 골라서 파일로 만들어줘.')
    old=checked(s,proposal(s,{'filter.field':'비용','filter.value':None}))
    old['slots']['filter.operator'].update(status='specified',value='gte',evidence=[{'source_id':'user','turn':0,'quote':s['messages'][0]['content']}])
    s['condition_ledger']=old
    s['messages'].append({'role':'user','content':'비용 100만원 초과, 빈 비용은 제외해. 중복 거래ID는 마지막 행만 남겨. 정렬은 하지 말고 JSON으로 저장해.'})
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gte','filter.value':'1000000','duplicate.field':'거래ID','duplicate.policy':'first','output.format':'csv'})
    result=checked(s,raw)
    assert result['decision']=='ready'
    slots=result['slots']
    assert {k:slots[k]['value'] for k in ['filter.operator','missing.policy','duplicate.policy','output.format','sort.policy']}=={'filter.operator':'gt','missing.policy':'exclude','duplicate.policy':'last','output.format':'json','sort.policy':'none'}
    assert result['operations']==['filter','deduplicate']


@pytest.mark.parametrize('phrase',['빈 비용은 제외해.','빈 금액은 제외해.','빈 값은 제외해.'])
def test_column_specific_missing_policy_cannot_be_omitted(phrase):
    s=state('비용 100만원 초과만 JSON으로 저장해. '+phrase)
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000'})
    assert checked(s,raw)['slots']['missing.policy']['value']=='exclude'


def test_same_turn_dedup_conflict_remains_unresolved():
    s=state('비용 100만원 초과만 JSON으로 저장해. 중복 거래ID는 첫 행만 남겨. 중복 거래ID는 마지막 행만 남겨.')
    ledger=checked(s,proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000','duplicate.field':'거래ID','duplicate.policy':'last'}))
    assert ledger['decision']=='clarify'
    assert ledger['slots']['duplicate.policy']['status']=='conflict'
    assert ledger['slots']['duplicate.policy']['value'] is None


def test_no_sort_constraint_is_audited_against_actual_draft():
    s=state('비용 100만원 초과만 JSON으로 저장해. 빈 값은 오류, 중복은 유지해. 정렬은 하지 말고.')
    s['condition_ledger']=checked(s,proposal(s));s['condition_enforced']=True
    d=workflow_draft(s)
    assert plan_issues(s['condition_ledger'],d)==[]
    d['workflow']['nodes'][1]['settings']['execution_plan']['sort']=[{'field':'비용','direction':'ascending','value_type':'number'}]
    assert any('정렬하지' in e for e in plan_issues(s['condition_ledger'],d))


def test_attached_file_guidance_does_not_request_same_upload_again():
    options=resolution_options({'resources':[{'id':'already-attached'}]})
    assert options[0]=='연결 자료에서 기준 확인 후 입력'
    assert answer_kind(options[0])=='guidance'
    assert resolution_options({})[0]=='자료 연결 후 기준 입력'


def test_unrequested_status_filter_is_not_accepted_alongside_correct_amount():
    s=state('비용 100만원 초과만 JSON으로 저장해. 상태는 관계없어. 빈 값은 오류, 중복은 유지해.')
    ledger=checked(s,proposal(s));d=workflow_draft(s)
    d['workflow']['nodes'][1]['settings']['execution_plan']['filters'].append(
        {'field':'상태','operator':'eq','value':'완료','compare_field':None,'value_type':'text'})
    assert any('추가 선별' in e for e in plan_issues(ledger,d))


def wire_schema():
    return {'type':'json_schema','json_schema':{'name':'probe','strict':True,'schema':{'type':'object','required':['id','purpose'],'additionalProperties':False,'properties':{'id':{'type':'string','pattern':r'^[a-zA-Z][\w-]{0,39}$'},'purpose':{'type':'string','maxLength':2000},'tool':{'enum':['read_table']}}}}}


def test_ollama_adjustment_only_changes_wire_copy_and_preserves_contract_shape():
    payload={'response_format':wire_schema(),'messages':[{'role':'user','content':'unchanged'}]}
    before=deepcopy(payload);out=compatible_schema_payload(payload)
    assert payload==before
    schema=out['response_format']['json_schema']['schema']
    assert schema['properties']['id']['pattern']==r'^[a-zA-Z][a-zA-Z0-9_-]{0,39}$'
    assert 'maxLength' not in schema['properties']['purpose']
    assert schema['required']==['id','purpose'] and schema['additionalProperties'] is False
    assert schema['properties']['tool']=={'enum':['read_table']}
    assert out['messages']==payload['messages']


def test_actual_ollama_adapter_uses_compatible_copy():
    model=OllamaChatOpenAI(model='qwen3:8b',api_key='local-not-a-secret',base_url='http://127.0.0.1:11434/v1')
    payload=model._get_request_payload([HumanMessage(content='probe')],response_format=wire_schema())
    assert 'maxLength' not in payload['response_format']['json_schema']['schema']['properties']['purpose']


@pytest.mark.parametrize('payload',[{}, {'response_format':None}, {'response_format':{'type':'json_object'}}])
def test_unstructured_or_tool_only_calls_remain_unchanged(payload):
    assert compatible_schema_payload(payload)==payload


@pytest.mark.parametrize('field',['purpose','explanation'])
def test_original_application_length_guard_still_rejects_2001(field):
    d=workflow_draft(state());d[field]='가'*2001
    with pytest.raises(ValidationError,match='string_too_long'):AgentDraft.model_validate(d)


def test_condition_nonanswer_reuses_only_unchanged_capability_and_stays_blocked():
    from agent_builder.nodes.capability_check import make_capability_check
    from agent_builder.capability_gate import input_fingerprint
    from agent_builder.test_input_resolution import waiting, respond
    from agent_builder.test_condition_readiness import Fake
    s=waiting()
    s['capability_assessment']={'decision':'existing_tools','tool_ids':['read_table','filter_sort','write_result'],
        'missing_conditions':[],'missing_actions':[],'reason':'등록 도구로 표 선별 가능','user_quotes':[s['messages'][0]['content']]}
    s['capability_fingerprint']=input_fingerprint(s)
    s=respond(s,'모르겠어');fake=Fake([])
    update=make_capability_check('fixture',fake,'enforce')(s)
    assert fake.calls==0 and update['capability_route']=='conditions'
    from agent_builder.nodes.condition_check import make_condition_check
    update=make_condition_check(transport=fake,mode='enforce')({**s,**update})
    assert update['status']=='needs_clarification'
    assert update['condition_ledger']['decision']=='clarify'
    assert not update['confirmed'] and update['saved_agent_id'] is None


def test_changed_resources_do_not_reuse_capability_after_condition_nonanswer():
    from agent_builder.nodes.capability_check import make_capability_check
    from agent_builder.capability_gate import input_fingerprint
    from agent_builder.test_input_resolution import waiting, respond
    from agent_builder.test_condition_readiness import Fake
    s=waiting();assessment={'decision':'existing_tools','tool_ids':['read_table'],
        'missing_conditions':[],'missing_actions':[],'reason':'등록 도구 사용','user_quotes':[s['messages'][0]['content']]}
    s['capability_assessment']=assessment;s['capability_fingerprint']=input_fingerprint(s)
    s=respond(s,'모르겠어');s['resources']=[{'id':'changed','kind':'table','columns':['비용']}]
    fake=Fake([assessment]);make_capability_check('fixture',fake,'enforce')(s)
    assert fake.calls==1


def test_numeric_condition_cannot_be_executed_as_lexical_text():
    s=state();ledger=checked(s,proposal(s));d=workflow_draft(s)
    d['workflow']['nodes'][1]['settings']['execution_plan']['filters'][0]['value_type']='text'
    assert any('비교' in e for e in plan_issues(ledger,d))


def test_actual_conversation_schema_pins_verified_settings_and_excludes_unused_plans(monkeypatch):
    from agent_builder.nodes import conversation
    s=state('비용 100만원 초과만 JSON으로 저장해. 빈 비용은 제외해. 중복 거래ID는 마지막 행만 남겨. 정렬은 하지 말고.')
    s['resources']=[];s['node_library']=[];s['condition_enforced']=True
    s['condition_ledger']=checked(s,proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000','duplicate.field':'거래ID'}))
    captured={}
    class Stop(Exception):pass
    def capture(**kwargs):
        captured.update(kwargs['response_format'].schema)
        raise Stop()
    monkeypatch.setattr(conversation,'create_deep_agent',capture)
    with pytest.raises(Stop):conversation.make_conversation('','fixture',lambda:object())(s)
    properties=captured['$defs']['FilterCondition']['properties']
    assert properties['operator']=={'enum':['gt']}
    assert properties['compare_field']=={'type':'null'}
    assert properties['value_type']=={'enum':['number']}
    assert properties['value']=={'enum':[1000000.0]}
    plan=captured['$defs']['ExecutionPlan']['properties']
    assert plan['duplicate_policy']=={'enum':['last']}
    assert plan['missing_policy']=={'enum':['exclude']}
    assert plan['sort']['maxItems']==0
    variants=captured['$defs']['WorkflowNode']['anyOf']
    for v in variants:
        tool=v['properties']['tool']['enum'][0]
        setting=v['properties']['settings']['properties']
        assert setting['execution_plan']==({'$ref':'#/$defs/ExecutionPlan'} if tool=='filter_sort' else {'type':'null'})
        if tool=='write_result':assert setting['format']=={'enum':['json']}


def test_json_content_without_provider_parsed_field_is_only_parsed_not_approved():
    import json
    from langchain_core.messages import AIMessage
    from agent_builder.nodes.conversation import draft_response
    raw=workflow_draft(state());raw['purpose']='가'*2001
    parsed=draft_response({'structured_response':None,'messages':[AIMessage(content=json.dumps(raw,ensure_ascii=False))]})
    with pytest.raises(ValidationError,match='string_too_long'):AgentDraft.model_validate(parsed)


@pytest.mark.parametrize('content',['null','[]','{}','not JSON'])
def test_missing_parsed_response_never_becomes_a_default_draft(content):
    from langchain_core.messages import AIMessage
    from agent_builder.nodes.conversation import draft_response
    with pytest.raises(ValueError):draft_response({'structured_response':None,'messages':[AIMessage(content=content)]})


def test_tool_or_earlier_json_is_not_used_as_final_assistant_draft():
    from langchain_core.messages import AIMessage, ToolMessage
    from agent_builder.nodes.conversation import draft_response
    with pytest.raises(ValueError):draft_response({'messages':[AIMessage(content='{}'),ToolMessage(content='{}',tool_call_id='tool')]})
    with pytest.raises(ValueError):draft_response({'messages':[AIMessage(content='{}',tool_calls=[{'name':'validate_workflow','args':{},'id':'t'}])]})


def test_conversation_switches_from_catalog_read_to_json_only_without_unread_resources(monkeypatch):
    from agent_builder.nodes import conversation
    from langchain.agents.middleware.types import ModelRequest
    s=state();s['resources']=[];s['node_library']=[]
    captured={}
    class Stop(Exception):pass
    def capture(**kwargs):captured.update(kwargs);raise Stop()
    monkeypatch.setattr(conversation,'create_deep_agent',capture)
    with pytest.raises(Stop):conversation.make_conversation('','fixture',lambda:object())(s)
    tools={t.name:t for t in captured['tools']}
    middleware=captured['middleware'][0]
    request=ModelRequest(model=object(),messages=[],tools=list(tools.values()),response_format=captured['response_format'])
    seen=[]
    def handler(value):seen.append(value);return value
    middleware.wrap_model_call(request,handler)
    assert seen[-1].response_format is None
    tools['list_tools'].invoke({})
    middleware.wrap_model_call(request,handler)
    assert 'list_tools' not in [t.name for t in seen[-1].tools]
    assert 'validate_workflow' not in tools  # App code validates the actual final graph.
    middleware.wrap_model_call(request,handler)
    assert seen[-1].tools==[]
    assert seen[-1].response_format is captured['response_format']


@pytest.mark.parametrize('invalid',[False,True])
def test_final_generated_graph_is_code_validated_without_model_rewriting_tool_arguments(monkeypatch,invalid):
    import json
    from langchain_core.messages import AIMessage
    from agent_builder.nodes import conversation
    s=state();s['resources']=[];s['node_library']=[]
    raw=workflow_draft(s);raw['requirements']=[{'description':'조건','evidence_id':'e1'}]
    if invalid:raw['workflow']['edges'][0]['target']='unknown-node'
    class Agent:
        def invoke(self,*args,**kwargs):return {'structured_response':raw,'messages':[AIMessage(content=json.dumps(raw,ensure_ascii=False))]}
    monkeypatch.setattr(conversation,'create_deep_agent',lambda **kwargs:Agent())
    result=conversation.make_conversation('','fixture',lambda:object())(s)
    assert result['trace'][-1]['tool_calls']==[]  # No invented model tool call.
    assert result['trace'][-1]['final_code_validation'] is (not invalid)
    assert bool(result['semantic_issues']) is invalid
    assert result['confirmed'] is False and result['saved_agent_id'] is None


def test_manual_review_explains_conflicting_policy_without_creating_or_saving():
    from agent_builder.nodes.condition_check import make_condition_check
    from agent_builder.test_condition_readiness import Fake
    s=state('비용 100만원 초과만 JSON으로 저장해. 중복 거래ID는 첫 행만 남겨. 중복 거래ID는 마지막 행만 남겨.')
    raw=proposal(s,{'filter.field':'비용','filter.operator':'gt','filter.value':'1000000','duplicate.field':'거래ID','duplicate.policy':'last'})
    raw['operation']='mixed';raw.pop('operations')
    update=make_condition_check(transport=Fake([raw]),mode='enforce')(s)
    assert update['status']=='needs_clarification' and update['condition_route']=='supervisor'
    assert update['condition_ledger']['slots']['duplicate.policy']['status']=='conflict'
    assert '첫 행' in update['messages'][-1]['content']
    assert not update['confirmed'] and update['saved_agent_id'] is None
