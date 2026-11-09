"""Deferred files and required settings survive generation, repair and execution."""
from copy import deepcopy
import json
import pytest
from jsonschema import Draft202012Validator, ValidationError
from agent_builder.design.attachment_precheck import QUESTION
from agent_builder.design.runtime_inputs import runtime_file_context, constrain_runtime_readers
from agent_builder.design.native_settings import constrain_draft_settings
from agent_builder.design.planner_constraints import native_decoder_schema
from agent_builder.design.nodes.conversation import make_conversation
from agent_builder.design.nodes.validation import validate_node
from contracts.models import AgentDraft
from tests.design.test_deep_agent_conversation import draft, state
from tests.workflow_engine.test_workflow import node, bind, edge, saved, run_workflow, FakeLLM, flow

PROMPT='첨부한 계약서 PDF 내용을 검토하고 사람이 확인하도록 구성해줘.'

def deferred():
    return {**state([{'role':'user','content':PROMPT},
                     {'role':'assistant','content':QUESTION},
                     {'role':'user','content':'실행할 때 넣을게요'}]),
            'session_id':'deferred-file-test','draft':draft(questions=[QUESTION])}


@pytest.mark.parametrize('answer',['실행할 때 넣을게요','파일은 나중에 업로드할게요','실행 시 제공할 자료입니다.'])
def test_user_defers_file_with_evidence(answer):
    s=deferred();s['messages'][-1]['content']=answer
    context=runtime_file_context(s)
    assert context['quote']==answer and context['file_delivery']=='at_execution'
    assert 'file_ids=[]' in context['instruction']


@pytest.mark.parametrize('messages',[
    [{'role':'user','content':PROMPT},{'role':'assistant','content':'실행할 때 넣을게요'}],
    [{'role':'user','content':'회의 날짜를 정해줘'},{'role':'user','content':'실행할 때 넣을게요'}],
    [{'role':'user','content':PROMPT},{'role':'user','content':'나중에 사람이 승인할게요'}],
    [{'role':'user','content':PROMPT},{'role':'user','content':'실행할 때 파일을 안 넣을게요'}],
])
def test_deferral_is_not_inferred_from_assistant_or_unrelated_answers(messages):
    assert runtime_file_context({'messages':messages}) is None


def test_new_file_choice_or_upload_supersedes_deferral():
    s=deferred();s['messages'].append({'role':'user','content':'파일은 지금 올릴게요'})
    assert runtime_file_context(s) is None
    s=deferred();s['resources']=[{'id':'actual','kind':'document'}]
    assert runtime_file_context(s) is None


def test_attachment_precheck_remembers_deferral_across_followup_turns():
    from agent_builder.design.attachment_precheck import missing_attachment_reply
    s=deferred()
    s['messages'].append({'role':'user','content':'첨부한 계약서 PDF의 해지 조건도 검토해줘'})
    assert missing_attachment_reply(s) is None
    s['messages'].append({'role':'user','content':'파일을 지금 제공할게요. 첨부한 계약서 PDF를 확인해줘'})
    assert missing_attachment_reply(s)['draft']['questions']==[QUESTION]


def test_decoder_rejects_empty_instructions_and_sum_without_column():
    schema=constrain_draft_settings(native_decoder_schema(AgentDraft.model_json_schema(),[]))
    instructions=Draft202012Validator(schema['properties']['instructions'])
    with pytest.raises(ValidationError):instructions.validate('')
    instructions.validate('금액을 합계합니다.')
    instructions.validate('금액을 합계합니다.\n결과를 저장합니다.')
    # Keep JSON string escaping in Ollama's native grammar. Whitespace-only
    # values are rejected by application validation even if a provider accepts them.
    from agent_builder.design.native_settings import draft_setting_issues
    assert 'pattern' not in schema['properties']['instructions']
    assert draft_setting_issues({'instructions':'   '})
    validator=Draft202012Validator({'$ref':'#/$defs/NativeNode_aggregate_table','$defs':schema['$defs']})
    candidate={'id':'total','tool':'aggregate_table','label':'금액 합계',
               'settings':{'fields':[],'aggregation':'sum','sum_field':None,'node_ref':None},
               'bindings':[bind('table','read','table')]}
    with pytest.raises(ValidationError):validator.validate(candidate)
    candidate['settings']['sum_field']='금액';validator.validate(candidate)
    candidate['settings'].update(aggregation='count',sum_field=None);validator.validate(candidate)


def test_only_deferred_readers_exclude_existing_asset_operations():
    schema=native_decoder_schema(AgentDraft.model_json_schema(),[])
    def operations(value):
        return [tool for v in value['$defs']['WorkflowNode']['anyOf'] if 'properties' in v
                for tool in v['properties']['tool']['enum'] if tool in ('read_table','read_document')]
    assert operations(schema)
    constrained=constrain_runtime_readers(schema,deferred())
    assert not operations(constrained)
    assert 'NativeNode_read_document' in constrained['$defs']
    assert operations(constrain_runtime_readers(schema,{'messages':[]}))==operations(schema)


def contract_workflow():
    return {'entry':'read','nodes':[node('read','read_document'),
        node('review','compose_answer',[bind('documents','read','documents')],instructions='계약 내용을 근거와 함께 검토하세요.'),
        node('human','ask_user',[bind('text','review','text')],question='계약 검토 결과를 확인하셨나요?')],
        'edges':[edge('read','review'),edge('review','human')]}


def test_deepagent_repairs_repeated_upload_question(monkeypatch):
    calls=[]
    def factory(**options):
        assert '실행 입력' in options['system_prompt']
        class Agent:
            def invoke(self,payload,config):
                calls.append(payload)
                context=json.loads(payload['messages'][0]['content'].split('\n',1)[1])
                assert context['runtime_file_input']['file_delivery']=='at_execution'
                assert context['current_draft'] is None
                if len(calls)==1:return {'structured_response':draft(questions=[QUESTION])}
                assert '파일은 실행할 때' in payload['messages'][-1]['content']
                return {'structured_response':draft(workflow=contract_workflow())}
        return Agent()
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    s=deferred();result=make_conversation('','test',model_factory=lambda:object())(s)
    assert len(calls)==2
    checked=validate_node({**s,**result})
    assert checked['issues']==[] and checked['status']=='ready'
    assert result['draft']['workflow']['nodes'][0]['settings']['file_ids']==[]
    assert result['draft']['workflow']['nodes'][-1]['tool']=='ask_user'


@pytest.mark.parametrize('fault',['instructions','sum_field'])
def test_deepagent_repairs_required_values_if_provider_ignores_schema(monkeypatch,fault):
    calls=[]
    workflow={'entry':'read','nodes':[node('read','read_table'),
        node('sum','aggregate_table',[bind('table','read','table')],aggregation='sum',sum_field='금액')],
        'edges':[edge('read','sum')]}
    valid=draft(workflow=workflow);broken=deepcopy(valid)
    if fault=='instructions':broken['instructions']=''
    else:broken['workflow']['nodes'][1]['settings']['sum_field']=None
    def factory(**options):
        class Agent:
            def invoke(self,payload,config):
                calls.append(payload)
                if len(calls)==2:
                    previous=json.loads(payload['messages'][-2]['content'])
                    assert payload['messages'][-2]['role']=='assistant'
                    assert previous==broken
                return {'structured_response':deepcopy(broken if len(calls)==1 else valid)}
        return Agent()
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    s=state([{'role':'user','content':'표의 금액 열을 합계해줘'}])
    result=make_conversation('','test',model_factory=lambda:object())(s)
    assert len(calls)==2 and not validate_node({**s,**result})['issues']
    assert result['draft']['workflow']['nodes'][1]['settings']['sum_field']=='금액'
    assert result['draft']['instructions']


def test_deferral_keeps_real_missing_business_questions(monkeypatch):
    class Agent:
        def invoke(self,*args,**kwargs):return {'structured_response':draft(questions=['어떤 조항을 위험으로 판단할까요?'])}
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kw:Agent())
    result=make_conversation('','test',model_factory=lambda:object())(deferred())
    assert result['draft']['questions']==['어떤 조항을 위험으로 판단할까요?']
    assert result['draft']['workflow'] is None


def test_repeated_invalid_setting_stops_without_inventing_instructions(monkeypatch):
    calls=[]
    class Agent:
        def invoke(self,*args,**kwargs):
            calls.append(1)
            return {'structured_response':{**draft(questions=['어떤 열인가요?']),'instructions':''}}
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kw:Agent())
    with pytest.raises(ValueError,match='필수 설정'):
        make_conversation('','test',model_factory=lambda:object())(deferred())
    assert len(calls)==2


def test_human_review_control_remains_available_after_capability_selection(monkeypatch):
    from tests.design.test_deep_agent_conversation import run_capturing
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    s=state([{'role':'user','content':'문서를 검토하고 사람이 확인하게 해줘'}])
    s['capability_assessment']={'decision':'existing_tools','tool_ids':['read_document','review_document']}
    captured=run_capturing(monkeypatch,s,draft(questions=['어떤 기준으로 검토할까요?']))
    variants=captured['schema']['$defs']['WorkflowNode']['anyOf']
    assert any(v['properties']['tool']['enum']==['ask_user'] for v in variants)
    assert any(t['id']=='ask_user' for t in captured['context']['execution_control_contracts'])


def test_repair_context_exposes_actual_prior_node_outputs(monkeypatch):
    from tests.design.test_deep_agent_conversation import run_capturing
    s=deferred()
    workflow=contract_workflow()
    workflow['nodes'][1]=node('review','extract_information',[bind('documents','read','documents')],fields=['계약기간'])
    s['draft']=draft(workflow=workflow)
    s['issues']=['extract_information에는 text 출력이 없습니다.']
    captured=run_capturing(monkeypatch,s,draft(questions=['어떤 기준으로 검토할까요?']))
    prior=captured['context']['current_draft_outputs']
    assert prior[0]['outputs']=={'documents':'documents'}
    assert prior[1]['node_id']=='review' and prior[1]['step']==2
    assert 'records' in prior[1]['outputs']
    assert 'text' not in prior[1]['outputs'] and 'documents' not in prior[1]['outputs']
    assert s['draft']['workflow']==workflow


def test_direct_repairs_repeated_file_request(monkeypatch):
    from tests.design.test_direct_conversation import Fake
    valid=draft(workflow=contract_workflow())
    valid['requirements']=[{'description':'계약 검토','source_id':'user','quote':PROMPT}]
    wrong=deepcopy(valid);wrong['questions']=['계약서 PDF 파일은 무엇인가요?'];wrong['workflow']=None
    fake=Fake(wrong,valid)
    monkeypatch.setenv('A4B_PLANNER_MODE','direct');monkeypatch.setenv('A4B_PLANNER_PROMPT','v8')
    s=deferred();result=make_conversation('','test',model_factory=lambda:fake)(s)
    assert len(fake.calls)==2 and not validate_node({**s,**result})['issues']
    assert '실행할 때' in fake.calls[-1][-1]['content']


def test_saved_document_workflow_accepts_file_only_at_execution(tmp_path):
    store,agent_id,asset=saved(tmp_path,flow())
    assert store.agent(agent_id)['config']['document_ids']==[]
    assert store.agent(agent_id)['config']['workflow']['nodes'][0]['settings']['file_ids']==[]
    result=run_workflow(store,agent_id,'실행 때 받은 문서를 처리','unused',file_ids=[asset['id']],llm=FakeLLM())
    assert result['status']=='succeeded' and result['artifact']
    assert json.loads(result['artifact']['content'])[0]['value']=='테스트'
    assert store.agent(agent_id)['config']['document_ids']==[]
    store.close()


def malformed_human_review():
    return {'entry':'read','nodes':[node('read','read_document'),
        node('extract','extract_information',[bind('documents','read','documents')],fields=['계약기간']),
        node('human','ask_user',[bind('records','extract','records'),bind('documents','extract','documents'),
                               bind('text','extract','text')],question='추출 내용을 확인했나요?')],
        'edges':[edge('read','extract'),edge('extract','human')]}


def test_connection_correction_offers_only_actual_typed_ports_and_keeps_valid_data():
    from agent_builder.design.port_correction import correction_contract, correct_connections
    workflow=malformed_human_review(); before=deepcopy(workflow)
    contract=correction_contract(workflow)
    pending=contract['nodes'][0]
    assert list(pending['choices'].values())==[bind('records','extract','records')]
    class Model:
        def with_structured_output(self,schema,**kw):
            assert schema['properties']['human']['items']['enum']==['b1']
            return self
        def invoke(self,messages):return {'human':['b1']}
    result,changes=correct_connections(workflow,[],Model())
    assert workflow==before
    assert result['nodes'][-1]['bindings']==[bind('records','extract','records')]
    assert result['edges']==before['edges']
    assert all(a['settings']==b['settings'] for a,b in zip(result['nodes'],before['nodes']))
    assert changes[0]['rule']=='model_selected_existing_typed_ports'


@pytest.mark.parametrize('response',[{'human':[]},{'human':['invented']},{'human':['b1','b1']},{'wrong':['b1']}])
def test_connection_correction_rejects_untrusted_model_changes(response):
    from agent_builder.design.port_correction import correct_connections
    class Model:
        def with_structured_output(self,*a,**kw):return self
        def invoke(self,*a):return response
    workflow=malformed_human_review();before=deepcopy(workflow)
    with pytest.raises(ValueError):correct_connections(workflow,[],Model())
    assert workflow==before


def test_connection_correction_does_not_guess_missing_nodes_or_branches():
    from agent_builder.design.port_correction import correction_contract
    w=malformed_human_review();w['nodes'][-1]['bindings'][-1]['node_id']='missing'
    assert correction_contract(w) is None
    w=malformed_human_review();w['edges'][-1]['branch']='yes'
    assert correction_contract(w) is None


def test_connection_correction_retains_full_workflow_validation():
    from agent_builder.design.port_correction import correct_connections
    w=malformed_human_review();w['nodes'][-1]['settings']['question']=None
    class Model:
        def with_structured_output(self,*a,**kw):return self
        def invoke(self,*a):return {'human':['b1']}
    with pytest.raises(ValueError):correct_connections(w,[],Model())


def test_deepagent_corrects_bad_ports_without_regenerating_business_settings(monkeypatch):
    model_calls=[]
    class Agent:
        def invoke(self,*a,**kw):return {'structured_response':draft(workflow=malformed_human_review())}
    class Model:
        def with_structured_output(self,*a,**kw):return self
        def invoke(self,messages):model_calls.append(messages);return {'human':['b1']}
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama')
    monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',lambda **kw:Agent())
    s=deferred();result=make_conversation('','test',model_factory=Model)(s)
    assert len(model_calls)==1
    assert result['draft']['workflow']['nodes'][-1]['bindings']==[bind('records','extract','records')]
    assert result['trace'][-1]['binding_repairs'][0]['rule']=='model_selected_existing_typed_ports'
    assert validate_node({**s,**result})['issues']==[]


def test_ollama_fills_empty_instructions_in_one_small_call(monkeypatch):
    captured=[];workflow=contract_workflow()
    raw=draft(workflow=workflow);raw['instructions']=''
    class Agent:
        def invoke(self,*a,**kw):return {'structured_response':deepcopy(raw)}
    class Model:
        def with_structured_output(self,schema,**kw):
            assert set(schema['properties'])=={'instructions'}
            return self
        def invoke(self,messages):
            captured.append(messages)
            return {'instructions':'실행 시 제공한 계약서를 검토하고 사람의 확인을 받습니다.'}
    def factory(**options):
        assert 'minLength' not in options['response_format'].schema_spec.json_schema['properties']['instructions']
        return Agent()
    monkeypatch.setenv('A4B_MAIN_PROVIDER','ollama');monkeypatch.setenv('A4B_PLANNER_MODE','deepagent')
    monkeypatch.setattr('agent_builder.design.nodes.conversation.create_deep_agent',factory)
    s=deferred();result=make_conversation('','test',model_factory=Model)(s)
    assert len(captured)==1 and raw['instructions']==''
    assert result['draft']['instructions'].startswith('실행 시 제공한 계약서')
    assert result['draft']['workflow']['edges']==workflow['edges']
    assert result['trace'][-1]['instruction_corrections']==1
    assert validate_node({**s,**result})['issues']==[]


@pytest.mark.parametrize('response',[{'instructions':''},{'instructions':' '},{'instructions':'ok','workflow':None}, {'instructions':None}])
def test_instruction_correction_cannot_accept_empty_or_replace_the_graph(response):
    from agent_builder.design.instruction_correction import correct_instructions
    class Model:
        def with_structured_output(self,*a,**kw):return self
        def invoke(self,*a):return response
    raw=draft(workflow=contract_workflow());raw['instructions']='';before=deepcopy(raw)
    with pytest.raises(ValueError):correct_instructions(raw,deferred(),Model())
    assert raw==before


def aggregate_source_case():
    w={'entry':'read','nodes':[node('read','read_table',file_ids=['orders']),
        node('total','aggregate_table',[bind('table','read','table')],fields=['고객코드'],aggregation='sum',sum_field='금액')],
        'edges':[edge('read','total')]}
    s={'messages':[{'role':'user','content':'주문금액을 고객코드별로 합계해줘'}],
       'resources':[{'id':'orders','kind':'table','columns':['고객코드','주문금액']},
                    {'id':'other','kind':'table','columns':['금액']}]}
    return w,s


def test_sum_correction_uses_only_the_explicit_source_columns():
    from agent_builder.design.column_correction import column_contract, correct_sum_columns
    from workflow_engine.workflow import workflow_errors
    w,s=aggregate_source_case();before=deepcopy(w)
    assert column_contract(w,s['resources'])[0]['source_columns']==['고객코드','주문금액']
    class Model:
        def with_structured_output(self,schema,**kw):
            assert schema['properties']['total']['enum']==[None,'고객코드','주문금액'];return self
        def invoke(self,*a):return {'total':'주문금액'}
    result,changes=correct_sum_columns(w,s,Model())
    assert result['nodes'][1]['settings']['sum_field']=='주문금액'
    assert not workflow_errors(result,['read_table','aggregate_table'],s['resources'])
    assert w==before and changes[0]['before']=='금액'


@pytest.mark.parametrize('value',[None,'금액','invented','',123])
def test_sum_correction_rejects_unknown_or_other_table_columns(value):
    from agent_builder.design.column_correction import correct_sum_columns
    class Model:
        def with_structured_output(self,*a,**kw):return self
        def invoke(self,*a):return {'total':value}
    w,s=aggregate_source_case();before=deepcopy(w)
    with pytest.raises(ValueError):correct_sum_columns(w,s,Model())
    assert w==before


def test_sum_correction_does_not_guess_an_ambiguous_source_or_computed_columns():
    from agent_builder.design.column_correction import column_contract
    w,s=aggregate_source_case();w['nodes'][0]['settings']['file_ids']=[]
    assert column_contract(w,s['resources'])==[]
    w,s=aggregate_source_case();w['nodes'][0]['tool']='calculate_columns'
    assert column_contract(w,s['resources'])==[]


def test_analysis_correction_uses_the_extractors_original_document_only():
    from agent_builder.design.port_correction import correction_contract, correct_connections
    w={'entry':'unrelated','nodes':[node('unrelated','read_document'),node('read','read_document'),
       node('extract','extract_information',[bind('documents','read','documents')],fields=['기간']),
       node('analysis','compose_answer',[bind('documents','extract','documents')],instructions='모순을 분석하고 수정 제안을 작성하세요.')],
       'edges':[edge('unrelated','read'),edge('read','extract'),edge('extract','analysis')]}
    contract=correction_contract(w)
    choices=contract['nodes'][0]['choices']
    assert list(choices.values())==[bind('documents','read','documents')]
    class Model:
        def with_structured_output(self,*a,**kw):return self
        def invoke(self,*a):return {'analysis':['b1']}
    result,_=correct_connections(w,[],Model())
    assert result['nodes'][-1]['bindings']==[bind('documents','read','documents')]
    assert result['nodes'][-1]['settings']==w['nodes'][-1]['settings']
    assert w['nodes'][-1]['bindings'][0]['node_id']=='extract'
