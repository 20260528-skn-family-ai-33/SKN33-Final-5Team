"""Model step choices stay explicit; compiler owns only structural wiring."""
from copy import deepcopy
import json
import pytest
from agent_builder.linear_plan import compile_plan,eligible,schema_for
from agent_builder.models import NodeSettings
from agent_builder.nodes.direct_conversation import decoder_schema,make_direct_conversation
from agent_builder.nodes.validation import validate_node
from agent_builder.nodes.condition_check import audit_conditions
from agent_builder.test_condition_readiness import state,proposal,checked,workflow_draft
from agent_builder.test_direct_conversation import Fake


def context():
    s=state();s.update(condition_enforced=True,condition_ledger=checked(s,proposal(s)))
    return s


def ir(s,tools=('read_table','filter_sort','write_result')):
    d=workflow_draft(s);steps=[]
    for tool in tools:
        settings=NodeSettings(file_ids=[],query=None,fields=[],instructions=None,format='json' if tool=='write_result' else None,
            question=None,condition_field=None,condition_operator=None).model_dump()
        steps.append({'tool':tool,'label':tool,'settings':settings})
    d['workflow']={'steps':steps};return d


def test_compiler_preserves_model_choices_and_does_not_insert_tools():
    s=context();raw=ir(s);original=deepcopy(raw);d=compile_plan(raw,s)
    assert raw==original
    assert [n['tool'] for n in d['workflow']['nodes']]==[t['tool'] for t in raw['workflow']['steps']]
    assert d['workflow']['entry']=='step_1'
    assert d['workflow']['edges']==[{'source':'step_1','target':'step_2','branch':'next'},{'source':'step_2','target':'step_3','branch':'next'}]
    assert d['workflow']['nodes'][-1]['bindings']==[{'input':'table','node_id':'step_2','output':'table'}]


@pytest.mark.parametrize('tools',[
    ('filter_sort','write_result'),('read_table','write_result','filter_sort'),
    ('read_table','filter_sort','finish'),('read_table','read_table','write_result'),
    ('read_table','filter_sort','write_result','finish','finish'),
])
def test_incompatible_choices_rejected_without_guessing_a_replacement(tools):
    s=context();raw=ir(s,tools);before=deepcopy(raw)
    with pytest.raises(ValueError):compile_plan(raw,s)
    assert raw==before


def test_missing_transform_is_not_inserted_and_audit_blocks_it():
    s=context();raw=ir(s,('read_table','write_result'));d=compile_plan(raw,s)
    assert [n['tool'] for n in d['workflow']['nodes']]==['read_table','write_result']
    assert audit_conditions({**s,'draft':d})['semantic_issues']


def test_scope_and_existing_references_are_preserved():
    s=context();assert eligible(s)
    for changes in [{'condition_enforced':False},{'required_node_refs':['existing@1']},
                    {'condition_ledger':{**s['condition_ledger'],'operations':['filter','deduplicate']}},
                    {'condition_ledger':{**s['condition_ledger'],'decision':'clarify'}}]:
        assert not eligible({**s,**changes})


def test_ir_schema_requires_steps_and_preserves_public_workflow_schema():
    s=context();base=decoder_schema('v8');before=deepcopy(base);schema=schema_for(base,s)
    assert schema['properties']['workflow']['required']==['steps']
    assert 'id' not in schema['$defs']['LinearStep']['properties']
    assert schema['$defs']['LinearStep']['properties']['tool']['enum']==['read_table','filter_sort','write_result','finish']
    assert schema['$defs']['LinearSettings']['properties']['execution_plan']=={'type':'null'}
    assert base==before
    assert not any(key.startswith('NativeNode_') for key in schema['$defs'])


def test_opt_in_ir_still_runs_original_validation_and_needs_confirmation(monkeypatch):
    monkeypatch.setenv('A4B_PLANNER_PROMPT','v8');s=context();fake=Fake(ir(s))
    result=make_direct_conversation('','fake',model_factory=lambda:fake)(s)
    checked_state=validate_node({**s,**result,**audit_conditions({**s,**result})})
    assert checked_state['status']=='ready' and not checked_state['issues']
    assert result['confirmed'] is False and result['saved_agent_id'] is None
    assert result['trace'][-1]['workflow_compiler']=='ordered_steps_v1'
    ctx=json.loads(fake.calls[0][1]['content'].split('\n',1)[1])
    assert len(ctx['supported_tools'])==4 and ctx['output_contract']['workflow_encoding']=='ordered_steps_v1'
