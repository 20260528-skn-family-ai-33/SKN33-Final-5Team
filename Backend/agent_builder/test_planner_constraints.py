"""Native decoder/repair counterexamples; no model calls or automatic repairs."""
from copy import deepcopy
import json
import pytest

from agent_builder.models import AgentDraft
from agent_builder.nodes.direct_conversation import decoder_schema,make_direct_conversation
from agent_builder.nodes.validation import validate_node
from agent_builder.planner_constraints import native_decoder_schema,binding_repair_options
from agent_builder.registry import PORTS
from agent_builder.test_direct_conversation import draft,state,Fake
from agent_builder.test_workflow import node,bind,edge


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
    assert len(defs['WorkflowNode']['anyOf'])==len(PORTS)==16
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


def test_pending_or_combined_conditions_do_not_bind_filter_values():
    for ledger in [{'decision':'clarify','operations':['filter']},{'decision':'ready','operations':['filter','deduplicate']}]:
        schema=native_decoder_schema(decoder_schema('v6'),[],ledger)
        assert 'enum' not in schema['$defs']['FilterCondition']['properties']['value']


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
    from agent_builder.test_condition_readiness import state as condition_state,proposal,checked,workflow_draft
    from agent_builder.nodes.condition_check import audit_conditions
    from agent_builder.nodes.supervisor import supervisor_node
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v7')
    s=condition_state();s.update(condition_enforced=True,condition_ledger=checked(s,proposal(s)))
    bad=workflow_draft(s);bad['questions']=['이미 확정된 금액을 다시 알려주세요.']
    fake=Fake(bad)
    result={**s,**make_direct_conversation('','fake',model_factory=lambda:fake)(s)}
    result.update(audit_conditions(result));result.update(validate_node(result))
    assert result['issues'] and result['confirmed'] is False
    assert supervisor_node(result)['route']=='repair'
    assert result.get('saved_agent_id') is None
