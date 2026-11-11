"""Offline shard metadata regressions; no inference or application mutations."""
from copy import deepcopy
import hashlib
import json
import sys

import pytest

from tests.design.test_qwen_local_harness import ROOT, load_script


def call(status='returned', sdk=False):
    return dict(status=status, transport='existing_langchain_client' if sdk else 'ollama_native',
                http_dispatch_attempts=0 if sdk else 1,
                http_responses_received=int(status=='returned' and not sdk),
                model_response_observed=status=='returned' and not sdk,
                **({'error_type':'TimeoutError'} if status=='failed' else {}))


def shards():
    corpus=json.loads((ROOT/'docs/qwen-live-evaluation-cases.json').read_text(encoding='utf-8'))
    ids=[c['id'] for c in corpus['cases']]
    rows=[dict(id=id,status='completed',live_model_calls=1,model_calls=[call()]) for id in ids]
    rows[-2]['model_calls'].append(call('failed'))
    rows[-2]['live_model_calls']=2
    rows[0]['model_calls'].append(call(sdk=True))
    common=dict(phase='after',gold_sha256=corpus['gold_sha256'],catalog_fingerprint='same',
        code_manifest={'source_sha256':'application'},evaluation_harness_manifest={'source_sha256':'harness'},
        effective_process_settings={'A4B_MAIN_MODEL':'qwen'},evaluation_environment={'kind':'local','model':'qwen'},
        probe_calls=[call()],inference_calls=999,model_call_records=999,
        dispatched_model_requests=999,model_responses_received=999,pending_model_call_records=999,
        evaluated_live_scenarios=999)
    first={**deepcopy(common),'status':'incomplete','reason':'transport_or_model_failure_circuit_breaker',
        'requested_scenarios':52,'not_run_case_ids':['X07'],'cases':deepcopy(rows[:-1])+[
            dict(id='X07',status='not_run',reason='transport_or_model_failure_circuit_breaker',live_model_calls=0)]}
    second={**deepcopy(common),'status':'completed','requested_scenarios':1,'not_run_case_ids':[],
            'cases':[deepcopy(rows[-1])]}
    return corpus,first,second


def test_after_51_plus_1_recomputes_metadata_and_keeps_both_probes_and_timeout():
    corpus,first,second=shards();original=deepcopy([first,second])
    result=load_script('merge-qwen-live-runs.py').merge([first,second],corpus)
    assert [first,second]==original
    assert result['status']=='completed' and result['requested_scenarios']==52
    assert result['not_run_case_ids']==[] and result['evaluated_live_scenarios']==52
    assert len(result['cases'])==52 and len(result['probe_calls'])==2
    assert result['cases'][-2]==first['cases'][-2]
    assert result['cases'][-2]['model_calls'][-1]['error_type']=='TimeoutError'
    assert result['model_call_records']==56
    assert result['dispatched_model_requests']==55
    assert result['http_responses_received']==54
    assert result['model_responses_received']==54
    assert result['returned_model_call_records']==55  # includes the frozen SDK counter omission
    assert result['failed_model_call_records']==1 and result['pending_model_call_records']==0
    assert result['inference_calls'] is None and 'reason' not in result
    assert result['shard_history'][0]['reason']=='transport_or_model_failure_circuit_breaker'


def test_missing_case_list_and_status_are_derived_from_retained_cases():
    corpus,first,_=shards()
    result=load_script('merge-qwen-live-runs.py').merge([first],corpus)
    assert result['status']=='incomplete' and result['not_run_case_ids']==['X07']
    assert result['cases'][-1]['status']=='not_run'


def test_duplicate_attempt_is_rejected_even_if_one_outcome_is_failure():
    corpus,first,second=shards();second['cases'].append(deepcopy(first['cases'][-2]))
    second['cases'][-1]['status']='failed'
    with pytest.raises(ValueError,match='Duplicate attempted case'):
        load_script('merge-qwen-live-runs.py').merge([first,second],corpus)


def test_interrupted_unassigned_trace_is_preserved_without_claiming_inference_total():
    corpus,first,second=shards()
    first.update(status='paused',active_case_id='X07',active_case_calls=[dict(
        status='dispatch_pending',http_dispatch_attempts=0,http_responses_received=0,model_response_observed=False)])
    result=load_script('merge-qwen-live-runs.py').merge([first,second],corpus)
    assert result['status']=='completed' and result['pending_model_call_records']==1
    assert result['orphaned_call_records'][0]['case_id']=='X07'
    assert result['model_call_records']==57 and result['inference_calls'] is None
    assert 'active_case_id' not in result and 'active_case_calls' not in result


def test_legacy_missing_transport_counters_are_unknown_not_zero():
    corpus,first,second=shards();first['cases'][0]['model_calls']=[dict(status='returned')]
    result=load_script('merge-qwen-live-runs.py').merge([first,second],corpus)
    assert result['dispatched_model_requests'] is None
    assert result['model_responses_received'] is None
    assert result['inference_calls'] is None


@pytest.mark.parametrize('change',['running','nonterminal','source','harness','settings'])
def test_incompatible_or_live_shards_remain_rejected(change):
    corpus,first,second=shards()
    if change=='running':second['status']='running'
    elif change=='nonterminal':second['cases'][0]['status']='started'
    elif change=='source':second['code_manifest']['source_sha256']='changed'
    elif change=='harness':second['evaluation_harness_manifest']['source_sha256']='changed'
    else:second['effective_process_settings']['A4B_MAIN_MODEL']='changed'
    with pytest.raises(ValueError):load_script('merge-qwen-live-runs.py').merge([first,second],corpus)


def test_cli_records_exact_input_bytes_and_refuses_overwrite(tmp_path,monkeypatch):
    corpus,first,second=shards();paths=[]
    for name,value in [('first.json',first),('second.json',second),('corpus.json',corpus)]:
        path=tmp_path/name;path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');paths.append(path)
    output=tmp_path/'merged.json'
    monkeypatch.setattr(sys,'argv',['merge',str(paths[0]),str(paths[1]),'--corpus',str(paths[2]),'--output',str(output)])
    module=load_script('merge-qwen-live-runs.py');module.main()
    result=json.loads(output.read_text(encoding='utf-8'))
    for path,history in zip(paths,result['shard_history']):
        assert history['source_document']['path']==path.as_posix()
        assert history['source_document']['file_bytes_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    original=output.read_bytes()
    with pytest.raises(SystemExit,match='Do not overwrite'):module.main()
    assert output.read_bytes()==original
