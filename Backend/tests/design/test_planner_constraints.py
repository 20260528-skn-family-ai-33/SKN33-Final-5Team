"""Native decoder/repair counterexamples; no model calls or automatic repairs."""
from copy import deepcopy
import json
import pytest

from contracts.models import AgentDraft
from agent_builder.design.nodes.direct_conversation import decoder_schema, make_direct_conversation
from agent_builder.design.nodes.validation import validate_node
from agent_builder.design.planner_constraints import native_decoder_schema, binding_repair_options


def test_native_settings_do_not_offer_operation_executor_fields():
    schema = native_decoder_schema(decoder_schema('v8'), [{'id':'csv','kind':'table'}])
    settings = schema['$defs']['NativeNode_read_table']['properties']['settings']
    assert set(settings['properties']) == {'file_ids', 'node_ref'}
    assert settings['additionalProperties'] is False
    for variant in schema['$defs']['WorkflowNode']['anyOf']:
        if '$ref' not in variant:
            assert variant['properties']['tool']['enum'] != ['read_table']


def test_raw_file_operations_remain_available_without_parsed_tables(monkeypatch):
    monkeypatch.setenv('A4B_MAIN_PROVIDER', 'ollama')
    schema = native_decoder_schema(decoder_schema('v8'), [])
    assert any(v.get('properties', {}).get('tool', {}).get('enum') == ['read_table']
               for v in schema['$defs']['WorkflowNode']['anyOf'])


@pytest.mark.parametrize('value,compare_field,value_type,operator,valid', [
    (20, None, 'number', 'gte', True),
    (20, 'amount', 'number', 'gte', False),
    (20, None, 'text', 'gte', False),
    (None, 'reorder_level', 'number', 'lt', True),
    (None, None, 'text', 'is_empty', True),
    ('서울', None, 'text', 'eq', True),
])
def test_filter_grammar_distinguishes_constants_columns_and_types(value,compare_field,value_type,operator,valid):
    from jsonschema import Draft202012Validator
    schema = native_decoder_schema(decoder_schema('v8'), [])
    validator = Draft202012Validator({'$ref':'#/$defs/TypedFilterCondition', '$defs':schema['$defs']})
    condition = {'field':'amount','operator':operator,'value':value,
                 'compare_field':compare_field,'value_type':value_type}
    assert validator.is_valid(condition) is valid


def test_table_input_cannot_decode_an_artifact_output():
    from jsonschema import Draft202012Validator
    schema = native_decoder_schema(decoder_schema('v8'), [])
    validator = Draft202012Validator({'$ref':'#/$defs/NativeBinding_write_result', '$defs':schema['$defs']})
    assert validator.is_valid({'input':'table','node_id':'filter','output':'table'})
    assert not validator.is_valid({'input':'table','node_id':'filter','output':'artifact'})
from workflow_engine.catalog.registry import PORTS
from tests.design.test_direct_conversation import draft, state, Fake
from tests.workflow_engine.test_workflow import node, bind, edge


def test_unknown_file_ids_are_excluded_and_public_schema_is_unchanged():
    base=decoder_schema('v6');before=deepcopy(base)
    empty=native_decoder_schema(base,[])
    assert empty['properties']['document_ids']['maxItems']==0
    assert empty['$defs']['NodeSettings']['properties']['file_ids']['maxItems']==0
    actual=native_decoder_schema(base,[{'id':'native-id','kind':'table'}])
    assert actual['properties']['document_ids']['items']['enum']==['native-id']
    assert actual['$defs']['NodeSettings']['properties']['file_ids']['items']['enum']==['native-id']
    assert actual['$defs']['Requirement']['properties']['source_id']['enum']==['user','native-id']
    assert base==before and AgentDraft.model_json_schema()['$defs']['Binding']['properties']['input']=={'title':'Input','type':'string'}


def test_all_native_tools_remain_available_with_type_compatible_bindings():
    schema=native_decoder_schema(decoder_schema('v6'),[]);defs=schema['$defs']
    assert {tool for v in defs['WorkflowNode']['anyOf'] for tool in ((defs[v['$ref'].split('/')[-1]]) if '$ref' in v else v)['properties']['tool']['enum']}==set(PORTS)
    finish=defs['NativeBinding_finish']['anyOf']
    assert {b['properties']['input']['enum'][0] for b in finish}=={'text','artifact'}
    artifact=next(b for b in finish if b['properties']['input']['enum']==['artifact'])
    assert artifact['properties']['output']['enum']==['artifact']
    table=defs['NativeBinding_write_result']['anyOf'][0]
    assert table['properties']['input']['enum']==table['properties']['output']['enum']==['table']
    assert defs['NativeNode_filter_sort']['properties']['bindings']['minItems']==1
    assert defs['NativeNode_read_table']['properties']['bindings']['maxItems']==0


@pytest.mark.parametrize('operator,value',[('gt',300000),('gte',0),('contains','정상')])
def test_confirmed_filter_is_bound_to_current_value_not_previous_draft(operator,value):
    ledger={'decision':'ready','operations':['filter'],'slots':{k:{'status':'specified','value':v} for k,v in
        {'filter.field':'비용','filter.operator':operator,'filter.value':value,'missing.policy':'error','output.format':'json'}.items()}}
    schema=native_decoder_schema(decoder_schema('v6'),[],ledger)
    props=schema['$defs']['FilterCondition']['properties'];plan=schema['$defs']['ExecutionPlan']['properties']
    assert props['operator']['enum']==[operator] and props['value']['enum']==[value]
    assert props['field']['enum']==['비용'] and props['compare_field']['enum']==[None]
    assert plan['filters']['minItems']==plan['filters']['maxItems']==1 and plan['sort']['maxItems']==0
    assert schema['$defs']['WorkflowDefinition']==decoder_schema('v6')['$defs']['WorkflowDefinition']


def test_pending_or_incomplete_combined_conditions_do_not_bind_filter_values():
    for ledger in [{'decision':'clarify','operations':['filter']},{'decision':'ready','operations':['filter','deduplicate']}]:
        schema=native_decoder_schema(decoder_schema('v6'),[],ledger)
        assert 'enum' not in schema['$defs']['FilterCondition']['properties']['value']


def test_complete_combined_contract_constrains_both_graph_and_step_encodings():
    from agent_builder.design.linear_plan import schema_for
    values={'filter.field':'가격','filter.operator':'gte','filter.value':27000,
            'duplicate.field':'상품번호','duplicate.policy':'first',
            'missing.policy':'exclude','output.format':'csv','sort.policy':'none'}
    ledger={'decision':'ready','operations':['filter','deduplicate'],
            'slots':{k:{'status':'specified','value':v} for k,v in values.items()}}
    original=deepcopy(ledger)
    graph=native_decoder_schema(decoder_schema('v7'),[],ledger,require_ready_draft=True)
    steps=schema_for(decoder_schema('v8'),{'condition_enforced':True,'condition_ledger':ledger,'resources':[]})
    for schema in (graph,steps):
        for key in ('questions','question_options','unsupported'):
            assert schema['properties'][key]['maxItems']==0
        plan=schema['$defs']['ExecutionPlan'];props=plan['properties']
        assert props['filters']['minItems']==props['filters']['maxItems']==1
        assert props['sort']['maxItems']==0
        assert props['duplicate_field']['enum']==['상품번호']
        assert props['duplicate_policy']['enum']==['first']
        assert props['missing_policy']['enum']==['exclude']
        assert props['output_format']['enum']==['csv']
        assert set(('filters','sort','duplicate_field','duplicate_policy','missing_policy','output_format'))<=set(plan['required'])
        assert schema['$defs']['FilterCondition']['properties']['operator']['enum']==['gte']
        assert schema['$defs']['FilterCondition']['properties']['value']['enum']==[27000]
        condition=schema['$defs']['FilterCondition']['properties']
        assert condition['compare_field']=={'type':'null','enum':[None]}
        assert condition['value']=={'type':'number','enum':[27000]}
        assert props['duplicate_field']=={'type':'string','enum':['상품번호']}
    assert graph['$defs']['ExecutionPlan']==steps['$defs']['ExecutionPlan']
    assert ledger==original


def test_repair_options_use_real_ancestor_ports_and_do_not_modify_graph():
    d=draft();d['workflow']['nodes'] += [node('finish','finish',[bind('table','read','table')])]
    d['workflow']['edges'].append(edge('save','finish'));before=deepcopy(d)
    options=binding_repair_options(d)
    finish=next(o for o in options if o['node_id']=='finish')
    assert finish['compatible_existing_ancestor_outputs']=={'text':[],'artifact':[{'node_id':'save','output':'artifact'}]}
    assert d==before and finish['required_inputs']==[]


def test_repair_options_with_cycles_are_bounded_and_remain_advisory():
    d=draft();d['workflow']['edges'].append(edge('save','read'))
    assert len(binding_repair_options(d))==2
    assert validate_node({**state(),'draft':d})['issues']


def test_v6_discards_obsolete_draft_from_new_turn_but_keeps_original_user_history(monkeypatch):
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v6')
    s=state();s['draft']=draft();s['messages'] += [{'role':'assistant','content':'이전 설명'},{'role':'user','content':'기준을 변경해줘'}]
    fake=Fake(draft());result=make_direct_conversation('','fake',model_factory=lambda:fake)(s)
    ctx=json.loads(fake.calls[0][1]['content'].split('\n',1)[1])
    assert ctx['current_draft'] is None and 'schema' not in ctx
    assert fake.calls[0][-3:]==s['messages'] and ctx['output_contract']['binding_fields']==['input','node_id','output']
    assert fake.schema['$defs']['WorkflowNode']['anyOf']
    assert result['trace'][-1]['prompt_version']=='v6'


def test_v6_invalid_mock_port_is_still_rejected_not_rewired(monkeypatch):
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v6')
    bad=draft();bad['workflow']['nodes'][1]['bindings'][0]['output']='artifact'
    fake=Fake(bad);s=state();result=make_direct_conversation('','fake',model_factory=lambda:fake)(s)
    assert result['draft']['workflow']['nodes'][1]['bindings'][0]['output']=='artifact'
    assert validate_node({**s,**result})['issues']
    second=Fake(draft());checked=validate_node({**s,**result})
    make_direct_conversation('','fake',model_factory=lambda:second)({**s,**result,**checked})
    ctx=json.loads(second.calls[0][1]['content'].split('\n',1)[1])
    assert ctx['current_draft'] and ctx['repair_feedback']['binding_options']


def test_ready_generation_gate_does_not_reask_or_expose_null_workflow():
    ledger={'decision':'ready','operations':['filter'],'slots':{k:{'status':'specified','value':v} for k,v in
        {'filter.field':'비용','filter.operator':'gt','filter.value':300000}.items()}}
    schema=native_decoder_schema(decoder_schema('v7'),[],ledger,require_ready_draft=True)
    assert all(schema['properties'][k]['maxItems']==0 for k in ('questions','question_options','unsupported'))
    assert schema['properties']['workflow']=={'$ref':'#/$defs/WorkflowDefinition'}
    assert 'workflow' in schema['required']
    for other in [None,{**ledger,'decision':'clarify'},{**ledger,'slots':{}},{**ledger,'operations':['filter','deduplicate']}]:
        pending=native_decoder_schema(decoder_schema('v7'),[],other,require_ready_draft=True)
        assert pending['properties']['questions']['maxItems']==3
        assert 'anyOf' in pending['properties']['workflow']


def test_ignored_ready_grammar_is_still_blocked_by_code_audit(monkeypatch):
    from tests.conditions.test_condition_readiness import state as condition_state, proposal, checked, workflow_draft
    from agent_builder.conditions.condition_check import audit_conditions
    from agent_builder.design.nodes.supervisor import supervisor_node
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v7')
    s=condition_state();s.update(condition_enforced=True,condition_ledger=checked(s,proposal(s)))
    bad=workflow_draft(s);bad['questions']=['이미 확정된 금액을 다시 알려주세요.']
    fake=Fake(bad)
    result={**s,**make_direct_conversation('','fake',model_factory=lambda:fake)(s)}
    result.update(audit_conditions(result));result.update(validate_node(result))
    assert result['issues'] and result['confirmed'] is False
    assert supervisor_node(result)['route']=='repair'
    assert result.get('saved_agent_id') is None


def test_ready_table_tool_scope_blocks_unrequested_aggregation_in_decoder_audit_and_save(monkeypatch):
    from agent_builder.design.planner_constraints import TABLE_PLAN_TOOLS,table_tool_issues
    from tests.design.test_linear_plan import context
    from tests.conditions.test_condition_readiness import workflow_draft
    from agent_builder.conditions.condition_check import audit_conditions
    from agent_builder.capabilities.capability_gate import require_save_ready
    s=context();s['node_library']=[]
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v7')
    fake=Fake(workflow_draft(s))
    make_direct_conversation('','fake',model_factory=lambda:fake)(s)
    variants=fake.schema['$defs']['WorkflowNode']['anyOf']
    tools={fake.schema['$defs'][v['$ref'].split('/')[-1]]['properties']['tool']['enum'][0] for v in variants}
    assert tools==set(TABLE_PLAN_TOOLS)
    ctx=json.loads(fake.calls[0][1]['content'].split('\n',1)[1])
    assert {t['id'] for t in ctx['supported_tools']}==tools
    d=workflow_draft(s)
    d['workflow']['nodes'].insert(2,node('extra','aggregate_table',[bind('table','filter','table')],aggregation='count',fields=[]))
    d['workflow']['nodes'][-1]['bindings']=[bind('table','extra','table')]
    d['workflow']['edges']=[edge('read','filter'),edge('filter','extra'),edge('extra','out')]
    from workflow_engine.workflow import workflow_errors
    assert not workflow_errors(d['workflow'],d['tool_ids']+['aggregate_table'],[],d['execution_plan'])
    s.update(draft=d,confirmed=True)
    assert table_tool_issues(s)
    assert any('범위에 없는 도구' in i for i in audit_conditions(s)['semantic_issues'])
    with pytest.raises(ValueError,match='확정 업무 조건'):require_save_ready(s)
    # An explicit request outside this scoped contract must retain its full catalog.
    unrestricted=native_decoder_schema(decoder_schema('v7'),[],None,require_ready_draft=True)
    defs=unrestricted['$defs']
    assert {tool for v in defs['WorkflowNode']['anyOf'] for tool in ((defs[v['$ref'].split('/')[-1]]) if '$ref' in v else v)['properties']['tool']['enum']}==set(PORTS)


def test_node_ids_follow_validator_rule_and_scoped_graph_bounds_repetition():
    import re
    from agent_builder.design.planner_constraints import NODE_ID_PATTERN,TABLE_PLAN_TOOLS
    full=native_decoder_schema(decoder_schema('v7'),[])
    assert full['$defs']['WorkflowDefinition']==decoder_schema('v7')['$defs']['WorkflowDefinition']
    for variant in full['$defs']['WorkflowNode']['anyOf']:
        assert (full['$defs'][variant['$ref'].split('/')[-1]] if '$ref' in variant else variant)['properties']['id']['pattern']==NODE_ID_PATTERN
    for good in ('read','node_read_1','finish-2'):
        assert re.fullmatch(NODE_ID_PATTERN.strip('^$'),good)
    for bad in ('1','_a','a'*41):  # e.g. real failed draft used IDs "1".."4"
        assert not re.fullmatch(NODE_ID_PATTERN.strip('^$'),bad)
    scoped=native_decoder_schema(decoder_schema('v7'),[],None,require_ready_draft=True,allowed_tools=TABLE_PLAN_TOOLS)
    graph=scoped['$defs']['WorkflowDefinition']['properties']
    assert graph['nodes']['maxItems']==4 and graph['edges']['maxItems']==3


def test_scoped_finish_offers_only_ports_an_in_scope_producer_supplies():
    from agent_builder.design.planner_constraints import TABLE_PLAN_TOOLS,TERMINAL_TOOLS
    assert set(TERMINAL_TOOLS)<=set(PORTS)
    scoped=native_decoder_schema(decoder_schema('v7'),[],None,require_ready_draft=True,allowed_tools=TABLE_PLAN_TOOLS)
    finish=scoped['$defs']['NativeBinding_finish']['anyOf']
    # Real failed draft bound finish.text <- write_result.text; no in-scope tool emits text.
    assert [(b['properties']['input']['enum'],b['properties']['output']['enum']) for b in finish]==[(['artifact'],['artifact'])]
    assert scoped['$defs']['NativeBinding_write_result']['anyOf'][0]['properties']['output']['enum']==['table']
    # The full catalog keeps every producer; an added text producer reopens text automatically.
    full=native_decoder_schema(decoder_schema('v7'),[])
    assert {b['properties']['input']['enum'][0] for b in full['$defs']['NativeBinding_finish']['anyOf']}=={'text','artifact'}
    wider=native_decoder_schema(decoder_schema('v7'),[],None,allowed_tools=(*TABLE_PLAN_TOOLS,'compose_answer','read_document'))
    assert {b['properties']['input']['enum'][0] for b in wider['$defs']['NativeBinding_finish']['anyOf']}=={'text','artifact'}


@pytest.mark.parametrize('tools,message',[(('read_table','unknown_ml_tool'),'포트가 등록되지 않은'),
                                          (('read_document','write_result'),'필수 입력을 공급할')])
def test_inconsistent_scope_fails_loudly_instead_of_hiding_tools(tools,message):
    with pytest.raises(ValueError,match=message):
        native_decoder_schema(decoder_schema('v7'),[],None,allowed_tools=tools)
