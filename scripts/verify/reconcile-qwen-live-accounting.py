"""Read-only response-evidence accounting; never reconstruct unknown HTTP usage/cost."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def canonical(model,provider):
    if not isinstance(model,str) or not model:return None
    return model+':latest' if provider=='ollama' and ':' not in model.rsplit('/',1)[-1] else model


def verified_return(call,selected,provider):
    if call.get('status')!='returned':return False,'not_returned'
    requested=canonical(call.get('requested_model'),provider)
    actual=canonical(call.get('actual_model'),provider)
    if not actual or actual!=requested or actual not in selected:return False,'model_identity_unverified'
    raw=call.get('raw_output');transport=call.get('transport')
    if transport=='ollama_native':
        if isinstance(raw,dict) and (isinstance(raw.get('content'),str) or isinstance(raw.get('tool_calls'),list)):
            return True,None
        return False,'native_response_body_missing'
    if transport=='existing_langchain_client':
        if not isinstance(raw,list) or not raw:return False,'sdk_response_body_missing'
        for message in raw:
            if not isinstance(message,dict):return False,'sdk_response_body_missing'
            metadata=message.get('response_metadata') or {}
            response_model=canonical(metadata.get('model_name') or metadata.get('model'),provider)
            if response_model!=actual or not metadata.get('finish_reason'):
                return False,'sdk_model_or_completion_metadata_missing'
            if not isinstance(message.get('content'),(str,list)) and not isinstance(message.get('tool_calls'),list):
                return False,'sdk_response_body_missing'
        return True,None
    return False,'unrecognized_transport'


def reconcile(run):
    settings=run.get('effective_process_settings') or {};provider=settings.get('A4B_MAIN_PROVIDER')
    selected={canonical(settings.get(name),provider) for name in ('A4B_MAIN_MODEL','A4B_CAPABILITY_MODEL')}
    selected.discard(None)
    records=[];case_rows=[];omissions=[];overlap=[]
    for case in run.get('cases',[]):
        calls=case.get('model_calls') or [];confirmed=0
        for index,call in enumerate(calls):
            valid,reason=verified_return(call,selected,provider);confirmed+=int(valid)
            records.append((f"case:{case['id']}:{index}",call,valid,reason))
        attempted=case.get('status') in ('completed','failed')
        missing=bool(attempted and confirmed and not case.get('live_model_calls',0))
        if missing:omissions.append(case['id'])
        case_rows.append({'id':case['id'],'status':case.get('status'),
                          'original_live_model_calls':case.get('live_model_calls',0),
                          'verified_returned_response_count':confirmed,
                          'has_verified_model_response':bool(confirmed),
                          'omitted_by_original_live_filter':missing})
    for index,call in enumerate(run.get('probe_calls') or []):
        valid,reason=verified_return(call,selected,provider)
        records.append((f'probe:{index}',call,valid,reason))
    active=run.get('active_case_calls')
    if active is None:active=run.get('interrupted_case_inference_calls') or []
    active_id=run.get('active_case_id') or run.get('interrupted_case_id')
    if active and any(c['id']==active_id and c.get('model_calls') for c in run.get('cases',[])):
        overlap.append({'case_id':active_id,'reason':'active trace overlaps a retained case; excluded from totals rather than double counted'})
    else:
        for index,call in enumerate(active):
            valid,reason=verified_return(call,selected,provider)
            records.append((f'active:{active_id}:{index}',call,valid,reason))
    verified=[(location,call) for location,call,valid,_ in records if valid]
    count=lambda field:sum(call.get(field,0) for _,call,_,_ in records if isinstance(call.get(field),int) and not isinstance(call.get(field),bool) and call[field]>=0)
    return {
        'schema_version':1,'source_status':run.get('status'),
        'source_harness_sha256':(run.get('evaluation_harness_manifest') or {}).get('source_sha256'),
        'gold_sha256':run.get('gold_sha256'),'selected_models':sorted(selected),
        'trace_records_observed':len(records),
        'verified_returned_responses':len(verified),
        'verified_returned_by_transport':dict(Counter(call.get('transport') for _,call in verified)),
        'verified_returned_by_scope':dict(Counter(location.split(':',1)[0] for location,_ in verified)),
        'verified_case_ids':[c['id'] for c in case_rows if c['has_verified_model_response']],
        'live_filter_omitted_case_ids':omissions,
        'failed_records':[{'location':location,'error_type':call.get('error_type')} for location,call,_,_ in records if call.get('status')=='failed'],
        'incomplete_records':[{'location':location,'status':call.get('status'),'dispatch_state':call.get('dispatch_state')} for location,call,_,_ in records if call.get('status') in ('started','dispatch_pending','dispatched','instrumentation_failed')],
        'returned_but_unverified':[{'location':location,'reason':reason} for location,call,valid,reason in records if call.get('status')=='returned' and not valid],
        'http_dispatch_attempts_lower_bound':count('http_dispatch_attempts'),
        'http_responses_received_lower_bound':count('http_responses_received'),
        'source_reported_inference_calls':run.get('inference_calls'),
        'total_server_inferences':None,'total_cost':None,'overlap_exclusions':overlap,
        'limitations':[
            'Verified response count is observed completed response evidence, not total server inference attempts, total token usage, or billing cost.',
            'HTTP dispatch attempts are a recorded lower bound. Missing SDK attempts, retries, in-flight requests and interrupted unsaved calls cannot be reconstructed.',
            'The frozen v2 harness patches httpx but this installed OpenAI client uses httpx2: SDK HTTP dispatch/response counters and same-origin API-path/redirect hooks are not covered.',
            'The selected host/port DNS guard and synthetic execution allowlist remain separate protections; absence of a recorded redirect does not independently prove no redirect occurred.',
            'A verified response proves recorded model output, not correctness or actual external connector execution. SDK output is assembled content, not original HTTP stream frames.',
        ],
        'case_accounting':case_rows,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise SystemExit('Preserve existing accounting artifacts; choose a new output path')
    raw=args.run.read_bytes();run=json.loads(raw)
    result=reconcile(run)
    result.update(source_run=str(args.run),source_run_sha256=hashlib.sha256(raw).hexdigest(),
                  accounting_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with args.output.open('x',encoding='utf-8') as output:json.dump(result,output,ensure_ascii=False,indent=2);output.write('\n')
    print(json.dumps({k:result[k] for k in ('verified_returned_responses','http_dispatch_attempts_lower_bound','live_filter_omitted_case_ids')}))


if __name__=='__main__':main()
