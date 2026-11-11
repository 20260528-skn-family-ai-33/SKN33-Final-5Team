"""Merge disjoint measured shards without rewriting failures or claiming total inference usage."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from qwen_live_support import metrics
from qwen_live_policy import assert_same_environment


def _trace_counts(calls):
    def count_field(field):
        values=[c.get(field) for c in calls]
        return sum(values) if all(type(v) is int and v>=0 for v in values) else None
    observed=[c.get('model_response_observed') for c in calls]
    return dict(model_call_records=len(calls),
        dispatched_model_requests=count_field('http_dispatch_attempts'),
        http_responses_received=count_field('http_responses_received'),
        model_responses_received=sum(observed) if all(type(v) is bool for v in observed) else None,
        pending_model_call_records=sum(c.get('status') in ('started','dispatch_pending','dispatched') for c in calls),
        returned_model_call_records=sum(c.get('status')=='returned' for c in calls),
        failed_model_call_records=sum(c.get('status')=='failed' for c in calls))


def merge(runs,corpus,source_documents=None):
    if not runs:raise ValueError('At least one shard is required')
    if source_documents is not None and len(source_documents)!=len(runs):
        raise ValueError('Source provenance must describe every shard')
    first=runs[0];indexed={};sources=[];probes=[];orphaned=[]
    for index,run in enumerate(runs):
        if run.get('status')=='running':raise ValueError('Do not merge a running shard')
        assert_same_environment(first.get('evaluation_environment'),run.get('evaluation_environment'))
        if first.get('evaluation_harness_manifest')!=run.get('evaluation_harness_manifest'):
            raise ValueError('Evaluation harness changed between shards')
        for key in ('phase','gold_sha256','catalog_fingerprint','effective_process_settings'):
            if run.get(key)!=first.get(key):raise ValueError('Shard differs: '+key)
        if run['code_manifest']['source_sha256']!=first['code_manifest']['source_sha256']:
            raise ValueError('Application source changed between shards')
        source={k:deepcopy(run[k]) for k in ('created_at','status','requested_scenarios','inference_calls',
            'inference_calls_accounting','reason','model_call_records','dispatched_model_requests',
            'model_responses_received','pending_model_call_records','not_run_case_ids','probe',
            'active_case_id','active_case_calls','interrupted_case_id','interrupted_case_inference_calls',
            'instrumentation_error') if k in run}
        source['shard_index']=index
        if source_documents is not None:source['source_document']=deepcopy(source_documents[index])
        sources.append(source)
        probes.extend(deepcopy(run.get('probe_calls') or []))
        for row in run['cases']:
            if row['status']=='not_run':continue
            if row['status'] not in ('completed','failed'):
                raise ValueError('Non-terminal attempted case: '+row['id'])
            if row['id'] in indexed:raise ValueError('Duplicate attempted case; do not cherry-pick: '+row['id'])
            indexed[row['id']]=deepcopy(row)
        active=run.get('active_case_calls')
        if active is None:active=run.get('interrupted_case_inference_calls') or []
        if active:
            active_id=run.get('active_case_id') or run.get('interrupted_case_id')
            retained=next((r.get('model_calls') for r in run['cases'] if r['id']==active_id and r['status']!='not_run'),None)
            if retained and active!=retained:
                raise ValueError('Ambiguous active/case trace overlap: '+str(active_id))
            if not retained:orphaned.append(dict(shard_index=index,case_id=active_id,model_calls=deepcopy(active)))
    if corpus['gold_sha256']!=first['gold_sha256']:raise ValueError('Wrong frozen corpus')
    ordered=[c['id'] for c in corpus['cases']]
    if set(indexed)-set(ordered):raise ValueError('Unknown case IDs')
    result=deepcopy(first)
    missing=[id for id in ordered if id not in indexed]
    result.update(status='completed' if len(indexed)==len(ordered) else 'incomplete',requested_scenarios=len(ordered),
                  cases=[indexed.get(id,{'id':id,'status':'not_run','reason':'not reached','live_model_calls':0}) for id in ordered],
                  shard_history=sources,not_run_case_ids=missing,probe_calls=probes,
                  orphaned_call_records=orphaned,merged_at=datetime.now(timezone.utc).isoformat(),inference_calls=None,
                  inference_calls_accounting='Total server inference attempts and cost remain unknown. Aggregate counters describe retained case/probe/orphan trace records only. HTTP dispatch and model_response_observed counters retain the frozen harness semantics/coverage limits; returned records are separate and are not independently verified model identities.')
    for key in ('reason','probe','active_case_id','active_case_call_offset','active_case_calls',
                'interrupted_case_id','interrupted_case_inference_calls','instrumentation_error','http_status','probe_step'):
        result.pop(key,None)
    calls=[c for row in indexed.values() for c in row.get('model_calls',[])]+probes
    calls += [c for group in orphaned for c in group['model_calls']]
    result.update(_trace_counts(calls))
    result['trace_inventory']=dict(retained_case_records=sum(len(r.get('model_calls',[])) for r in indexed.values()),
        probe_records=len(probes),orphaned_records=sum(len(g['model_calls']) for g in orphaned),
        definition='Stored traces only; absent/unpersisted traces are not reconstructed. Missing legacy transport fields yield null aggregate counters, not zero.')
    result['retained_case_inference_calls']=sum(r.get('live_model_calls',0) for r in indexed.values())
    result['evaluated_live_scenarios']=sum(r.get('live_model_calls',0)>0 for r in indexed.values())
    result['metrics']=metrics(result['cases'])
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('runs',type=Path,nargs='+');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--corpus',type=Path,default=Path(__file__).resolve().parents[2]/'docs/qwen-live-evaluation-cases.json')
    a=p.parse_args()
    if a.output.exists():raise SystemExit('Do not overwrite prior evidence; choose a new output path')
    documents=[f.read_bytes() for f in a.runs]
    provenance=[dict(path=f.as_posix(),file_bytes_sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
                for f,raw in zip(a.runs,documents)]
    result=merge([json.loads(raw) for raw in documents],json.loads(a.corpus.read_text(encoding='utf-8')),provenance)
    result['merge_helper_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with a.output.open('x',encoding='utf-8') as output:
        output.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'live_scenarios':result['evaluated_live_scenarios'],'retained_case_calls':result['retained_case_inference_calls']}))


if __name__=='__main__':main()
