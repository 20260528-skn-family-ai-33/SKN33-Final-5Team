"""Team boundary tests with fixed core fixtures; no real model/DB/service."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_builder.clarification_contract import (
    CONTRACT,ClarificationInput,ClarificationOutput,MLAdvice,catalog_fingerprint,native_ml_advice,
    to_builder_state,to_handoff,tool_catalog,
)
from agent_builder.condition_readiness import source_catalog
from agent_builder.nodes.condition_check import make_condition_check

EXAMPLES=Path(__file__).resolve().parents[2]/'docs/clarification-handoff/examples'


def load(name,suffix='input'):
    return json.loads((EXAMPLES/f'{name}.{suffix}.json').read_text())


def evaluate(payload):
    state=to_builder_state(payload,expected_session_id=payload['session_id'],expected_revision=payload['revision'])
    evidence=next(e['id'] for e in source_catalog(state)[0] if e['source_id']=='user')
    class Fake:
        calls=0
        def invoke(self,messages):
            self.calls+=1
            return {'content':json.dumps({'operation':'filter','conditions':[
                {'key':key,'status':'specified','value':value,'evidence_ids':[evidence]} for key,value in
                {'filter.field':'비용','filter.operator':'gte','filter.value':'100000'}.items()],
                'unsupported_actions':[]},ensure_ascii=False)}
    fake=Fake();result={**state,**make_condition_check(transport=fake,mode='enforce')(state)}
    return state,result,fake


@pytest.mark.parametrize('name,decision',[
    ('01-ready','READY'),('02-clarify','CLARIFY'),('03-parser-error','SYSTEM_ERROR'),('04-db-error','SYSTEM_ERROR'),
    ('05-unsupported','UNSUPPORTED'),('06-deferred','DEFERRED'),('07-manual-review','MANUAL_REVIEW'),('08-answer','READY'),('09-conflict','CLARIFY'),
])
def test_published_examples_validate_and_never_grant_save_or_execution(name,decision):
    request=ClarificationInput.model_validate(load(name));result=ClarificationOutput.model_validate(load(name,'output'))
    assert request.contract_version==CONTRACT and result.decision==decision
    assert not result.allows_save and not result.allows_execution and result.requires_user_approval
    assert (result.generator_context is not None)==(decision=='READY')


@pytest.mark.parametrize('change',[
    {'revision':99},{'session_id':'another-session'},{'tool_catalog_fingerprint':'0'*64},
])
def test_stale_session_revision_and_registry_are_integration_errors(change):
    payload=load('01-ready');payload.update(change)
    with pytest.raises(ValueError):
        to_builder_state(payload,expected_session_id='sample-session',expected_revision=0)


@pytest.mark.parametrize('change',[{'answer_question_id':'0'*24},{'answer_question_id':'not-an-id'}])
def test_unknown_answer_ids_rejected_before_any_model_call(change):
    payload=load('08-answer');payload.update(change)
    with pytest.raises(ValueError):to_builder_state(payload,expected_session_id='sample-session',expected_revision=1)


def test_answer_is_bound_to_question_and_original_turn_with_no_payload_mutation():
    payload=load('08-answer');original=deepcopy(payload)
    result=to_builder_state(payload,expected_session_id='sample-session',expected_revision=1)
    answer=result['condition_answers'][-1]
    assert answer['question_ids']==[payload['answer_question_id']]
    assert answer['candidate_keys']==['filter.value']
    assert answer['answer']=='20만원 초과로 해줘.' and answer['turn']==len(payload['messages'])-1
    assert result['condition_ledger']==payload['previous_state']['ledger'] and payload==original
    result['condition_ledger']['slots'].clear()
    assert payload==original


@pytest.mark.parametrize('name',['03-parser-error','04-db-error'])
def test_source_faults_skip_model_and_cannot_become_questions(name):
    payload=load(name);state,result,fake=evaluate(payload)
    exported=to_handoff(payload,result)
    assert fake.calls==0 and exported.decision=='SYSTEM_ERROR'
    assert not exported.questions and exported.generator_context is None


def test_selected_page_metadata_is_not_full_document_or_fake_page_one():
    payload=load('03-parser-error')
    payload['resources'][0].update(parser_status='succeeded',parser_metadata={'whole_document_complete':False,'requested_pages':[67]})
    state,result,fake=evaluate(payload)
    assert fake.calls==0 and result['condition_ledger']['decision']=='system_error'
    asset=state['resources'][0]
    assert asset['processing_status']=='partial'
    assert asset['parser_metadata']['requested_pages']==[67]
    assert payload['resources'][0]['parser_status']=='succeeded'  # Original input unchanged.


def test_db_canonical_text_contract_is_not_silently_accepted_as_runtime_asset():
    root=Path(__file__).resolve().parents[1]
    parser=root/'integrations/parser_handoff/db-contract-v0.1/samsung-p67.parser-result.json'
    payload=load('01-ready');payload['resources']=[json.loads(parser.read_text())]
    with pytest.raises(ValidationError):ClarificationInput.model_validate(payload)


def test_legacy_parser_keys_cannot_hide_inside_an_otherwise_valid_asset():
    payload=load('03-parser-error');payload['resources'][0]['canonical_text']='DB 본문'
    with pytest.raises(ValidationError,match='DB 파서 공유안'):ClarificationInput.model_validate(payload)


def test_conflicting_source_values_retain_both_citations_for_the_ui():
    result=ClarificationOutput.model_validate(load('09-conflict','output'))
    item=result.unresolved_conditions['filter.value']
    assert item.status=='conflict' and item.value is None
    assert {e['source_id'] for e in item.evidence}=={'policy-1','policy-2'}
    assert 'filter.value' not in result.confirmed_conditions
    assert result.decision=='CLARIFY' and result.generator_context is None


@pytest.mark.parametrize('status',['not_requested','fallback','failed'])
def test_optional_svm_unavailability_does_not_block_ready_or_remove_tools(status):
    payload=load('01-ready');payload['ml_advice']={'status':status,'ml_candidates':[]}
    _,result,_=evaluate(payload);exported=to_handoff(payload,result)
    assert exported.decision=='READY'
    assert len(exported.generator_context['tool_catalog'])==len(tool_catalog())==16
    assert exported.generator_context['ml_advice']['status']==status


def test_native_scored_svm_advice_is_preserved_as_hint_not_tool_selection():
    payload=load('01-ready')
    advice=native_ml_advice({'ml_candidates':[{'tool_id':'filter_sort','estimated_probability':0.7}],'ml_usage':'참고 정보'})
    payload['ml_advice']=advice.model_dump()
    _,result,_=evaluate(payload);exported=to_handoff(payload,result)
    assert exported.generator_context['ml_advice']['ml_candidates']==advice.model_dump()['ml_candidates']
    assert len(exported.generator_context['tool_catalog'])==16
    assert native_ml_advice(None).status=='not_requested'
    assert native_ml_advice({'common_rules':['전체 도구 유지']}).status=='fallback'


@pytest.mark.parametrize('candidate',[
    {'tool_id':'unknown_tool','estimated_probability':0.5},
    {'tool_id':'finish','estimated_probability':0.5},
    {'tool_id':'filter_sort','estimated_probability':float('nan')},
    {'tool_id':'filter_sort','estimated_probability':True},
    {'tool_id':'filter_sort','estimated_probability':1.1},
])
def test_invalid_ml_ids_or_scores_are_not_business_clarification(candidate):
    with pytest.raises(ValidationError):MLAdvice(status='available',ml_candidates=[candidate])


def test_raw_svm_and_oracle_predictions_need_an_explicit_adapter():
    for packet in [{'candidates':['filter_sort'],'probs':{},'full_list':False},{'required_tool_ids':['filter_sort']}]:
        with pytest.raises(ValueError):native_ml_advice(packet)


@pytest.mark.parametrize('change',['session','messages','resources','source_health','ledger_version','off','shadow'])
def test_mismatched_or_inactive_results_cannot_be_exported(change):
    payload=load('01-ready');_,result,_=evaluate(payload)
    if change=='session':result['session_id']='foreign'
    elif change=='messages':result['messages'][0]['content']='다른 요청'
    elif change=='resources':result['resources']=[{'id':'other'}]
    elif change=='source_health':result['source_health']['db_status']='timeout'
    elif change=='ledger_version':result['condition_ledger']['protocol']='old'
    else:result['condition_enforced']=False
    with pytest.raises(ValueError):to_handoff(payload,result)


def test_ready_output_cannot_hide_issues_or_grant_execution():
    ready=load('01-ready','output')
    for change in [{'allows_execution':True},{'allows_save':True},{'issues':['DB 실패']},{'next_action':'ASK_USER'}]:
        with pytest.raises(ValidationError):ClarificationOutput.model_validate({**ready,**change})


def test_catalog_fingerprint_and_schema_artifacts_match_current_proposal():
    root=EXAMPLES.parent
    assert json.loads((root/'input.schema.json').read_text())==ClarificationInput.model_json_schema()
    assert json.loads((root/'output.schema.json').read_text())==ClarificationOutput.model_json_schema()
    catalog=json.loads((root/'tool-catalog.json').read_text())
    assert catalog=={'fingerprint':catalog_fingerprint(),'tools':tool_catalog()}


def test_ready_output_requires_real_confirmed_condition_evidence():
    ready=load('01-ready','output')
    with pytest.raises(ValidationError):ClarificationOutput.model_validate({**ready,'confirmed_conditions':{}})
    ready['confirmed_conditions']['filter.field']['evidence']=[]
    with pytest.raises(ValidationError):ClarificationOutput.model_validate(ready)


def test_forged_ready_cannot_hide_a_missing_required_slot():
    payload=load('01-ready');_,result,_=evaluate(payload)
    result['condition_ledger']['slots'].pop('filter.value')
    with pytest.raises(ValueError,match='필수 조건'):to_handoff(payload,result)
