"""Offline reconciliation evidence fixtures; no model calls."""
from copy import deepcopy
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
SPEC=importlib.util.spec_from_file_location('reconcile',ROOT/'scripts/verify/reconcile-qwen-live-accounting.py')
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)


def returned(transport='ollama_native'):
    call={'status':'returned','requested_model':'qwen-test','actual_model':'qwen-test:latest',
          'transport':transport,'http_dispatch_attempts':1,'http_responses_received':1,
          'raw_output':{'role':'assistant','content':'synthetic'}}
    if transport=='existing_langchain_client':
        call.update(http_dispatch_attempts=0,http_responses_received=0,model_response_observed=False,
                    raw_output=[{'content':'synthetic','response_metadata':{'model_name':'qwen-test','finish_reason':'stop','id':'synthetic-id'}}])
    return call


def run(calls):
    return {'status':'completed','effective_process_settings':{'A4B_MAIN_PROVIDER':'ollama','A4B_MAIN_MODEL':'qwen-test'},
            'cases':[{'id':'R02','status':'completed','live_model_calls':0,'model_calls':calls}]}


def test_sdk_http_counter_omission_does_not_erase_verified_response_or_mutate_source():
    source=run([returned('existing_langchain_client')]);before=deepcopy(source)
    result=MODULE.reconcile(source)
    assert result['verified_returned_responses']==1
    assert result['http_dispatch_attempts_lower_bound']==0
    assert result['live_filter_omitted_case_ids']==['R02']
    assert result['total_server_inferences'] is None and result['total_cost'] is None
    assert source==before


def test_missing_metadata_wrong_model_and_incomplete_records_are_not_invented_responses():
    wrong=returned();wrong['actual_model']='different-model'
    incomplete={'status':'started','transport':'ollama_native','http_dispatch_attempts':0}
    sdk=returned('existing_langchain_client');sdk['raw_output'][0]['response_metadata'].pop('finish_reason')
    result=MODULE.reconcile(run([wrong,incomplete,sdk,{'status':'failed','error_type':'TimeoutError','http_dispatch_attempts':1}]))
    assert result['verified_returned_responses']==0
    assert len(result['returned_but_unverified'])==2
    assert len(result['incomplete_records'])==1 and len(result['failed_records'])==1
    assert result['http_dispatch_attempts_lower_bound']==2


def test_probe_and_active_evidence_are_separate_and_interruption_alias_is_not_double_counted():
    source=run([returned()]);source['probe_calls']=[returned()]
    source.update(active_case_id='M02',active_case_calls=[returned('existing_langchain_client')],
                  interrupted_case_inference_calls=[returned('existing_langchain_client')])
    result=MODULE.reconcile(source)
    assert result['verified_returned_responses']==3
    assert result['verified_returned_by_scope']=={'case':1,'probe':1,'active':1}
    assert result['verified_returned_by_transport']=={'ollama_native':2,'existing_langchain_client':1}


def test_active_trace_collision_is_reported_without_double_counting():
    source=run([returned()]);source.update(active_case_id='R02',active_case_calls=[returned()])
    result=MODULE.reconcile(source)
    assert result['verified_returned_responses']==1
    assert result['overlap_exclusions'][0]['case_id']=='R02'
