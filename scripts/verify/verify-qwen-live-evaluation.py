"""Explicit live backend session E2E; no external connector execution or production DB.

--environment records an unavailable preflight without inference.
--live uses an explicit RunPod endpoint or an exact --allow-local-endpoint opt-in; no fallback.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import urllib.request
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Backend'))
from qwen_live_support import validate_corpus, digest, sanitize, route, choose_answer, metrics, can_execute_locally, task_scores
from qwen_live_policy import endpoint_policy, environment_identity, assert_same_environment, require_complete_baseline, canonical_model
from qwen_live_storage import InstrumentationFatal, write_report
from workflow_engine.catalog.registry import TOOLS


def code_manifest():
    paths=[]
    for root in ('Backend/agent_builder','Backend/contracts','Backend/infrastructure/llm','Backend/service','Backend/workflow_engine','Backend/ml','Frontend/app'):
        paths.extend(p for p in (ROOT/root).rglob('*') if p.suffix in ('.py','.ts','.tsx','.json') and '__pycache__' not in p.parts)
    paths += [ROOT/'Backend/api/src/agent-builder.ts',ROOT/'scripts/run-configured.mjs']
    hashes={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    return {'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'source_sha256':digest(hashes),'files':hashes}


def harness_manifest():
    directory=ROOT/'scripts/verify'
    names=('verify-qwen-live-evaluation.py','run-qwen-live-evaluation.mjs','qwen_live_trace.py',
           'qwen_live_support.py','qwen_live_policy.py','qwen_live_storage.py',
           'qwen-live-profiles.mjs','qwen-live-policy.mjs')
    files={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in names}
    return {'source_sha256':digest(files),'files':files}


def live_probe(tracer, identity, expected_identity=None):
    from agent_builder.design.requirement_ledger import requirement_transport
    base=os.environ['A4B_MAIN_BASE_URL'].rstrip('/');key=os.getenv('A4B_MAIN_API_KEY','')
    model=os.environ['A4B_MAIN_MODEL']
    headers={'User-Agent':'A4B/0.1',**({'Authorization':'Bearer '+key} if key else {})}
    tracer.probe_step='model_list'
    with urllib.request.urlopen(urllib.request.Request(base+'/models',headers=headers),timeout=15) as response: body=json.load(response)
    ids=[item['id'] for item in body.get('data',[])]
    provider=os.getenv('A4B_MAIN_PROVIDER')
    if identity['kind']=='local' and provider!='ollama':raise ValueError('Local evaluation currently requires Ollama weight digests; vLLM needs explicit weight provenance')
    selected_models=list(dict.fromkeys([model,os.getenv('A4B_CAPABILITY_MODEL') or model]))
    for selected in selected_models:
        if not any(canonical_model(item,provider)==canonical_model(selected,provider) for item in ids):raise ValueError('Selected model absent from server list')
        if 'qwen' not in selected.lower():raise ValueError('Model identity is not an explicitly configured Qwen model')
    server_metadata=[]
    identity['models']={selected:{'listed_model_id':next(item for item in ids if canonical_model(item,provider)==canonical_model(selected,provider))} for selected in selected_models}
    if provider=='ollama':
        tracer.probe_step='model_digests'
        with urllib.request.urlopen(urllib.request.Request(base.removesuffix('/v1')+'/api/tags',headers=headers),timeout=15) as response: tags=json.load(response)
        for selected in selected_models:
            tag=next((t for t in tags.get('models',[]) if canonical_model(t.get('name'),provider)==canonical_model(selected,provider)),{})
            identity['models'][selected].update(digest=tag.get('digest'),size_bytes=tag.get('size'))
            if identity['kind']=='local' and not tag.get('digest'):raise ValueError('Local Ollama model digest is required')
        tracer.probe_step='server_version'
        try:
            with urllib.request.urlopen(urllib.request.Request(base.removesuffix('/v1')+'/api/version',headers=headers),timeout=15) as response: identity['server_version']=json.load(response).get('version')
            if identity['kind']=='local' and not identity['server_version']:raise ValueError('Local Ollama server version is required')
        except OSError as error:
            if identity['kind']=='local':raise ValueError('Local Ollama server version is required') from error
        for selected in selected_models:
            tracer.probe_step='model_metadata'
            request=urllib.request.Request(base.removesuffix('/v1')+'/api/show',headers={**headers,'Content-Type':'application/json'},
                                           data=json.dumps({'model':selected}).encode())
            try:
                with urllib.request.urlopen(request,timeout=15) as response: info=json.load(response)
            except OSError as error:
                if identity['kind']=='local':raise ValueError('Local Ollama model metadata is required') from error
                server_metadata.append({'model':selected,'metadata_unavailable':type(error).__name__,'http_status':getattr(error,'code',None)})
                continue
            server_metadata.append({'model':selected,'details':info.get('details'),
                                    'context_lengths':{k:v for k,v in info.get('model_info',{}).items() if k.endswith('.context_length')},
                                    'num_ctx_parameter':[line for line in info.get('parameters','').splitlines() if line.strip().startswith('num_ctx')]})
            identity['models'][selected].update(details=info.get('details'),parameters=info.get('parameters'),
                                                template_sha256=digest(info.get('template')),
                                                context_lengths=server_metadata[-1]['context_lengths'])
    # Compare discovered weights/runtime before spending any inference call.
    if expected_identity is not None:assert_same_environment(expected_identity,identity)
    transport=requirement_transport(model,'local-not-a-secret')
    transport.schema={'type':'object','properties':{'ok':{'type':'boolean'},'count':{'type':'integer'}},'required':['ok','count'],'additionalProperties':False}
    tracer.probe_step='structured_inference'
    reply=transport.invoke([{'role':'user','content':'연결 검증용 합성 요청입니다. JSON으로 ok=true, count=5를 반환하세요.'}])
    value=json.loads(reply['content'])
    if value!={'ok':True,'count':5}:raise ValueError('Structured probe did not match its schema and values')
    if not tracer.calls[-1].get('actual_model'):raise ValueError('Actual response model metadata missing')
    if tracer.calls[-1]['actual_model'] not in (model,model+':latest'):raise ValueError('Actual model differs from configured model')
    return {'model_ids':ids,'actual_model':tracer.calls[-1]['actual_model'],'structured_probe':'passed',
            'server_model_metadata':server_metadata,
            'weights_family_attestation':'model ID and response metadata only; server weights not independently inspected'}


def run_case(case,model,tracer,directory):
    from service.worker import handle
    from infrastructure.persistence.storage import Storage
    from agent_builder.design.graph import build_graph
    start=time.monotonic();call_start=len(tracer.calls);stage_start=len(tracer.stages)
    record={'id':case['id'],'status':'started','kind':'live_backend_session_e2e','expected_route':case['gold']['routing'],
            'request':case['request'],'turns':[],'external_execution':'NOT_RUN','frontend_browser_observed':False,
            'http_api_exercised':False,'execution_success':None,'schema_valid':None}
    params={'directory':str(directory),'api_key':'local-not-a-secret'}
    session=handle({**params,'action':'new_session','model':model,'credential_id':'local-model'})
    sid=session['id'];record['session_id']=sid;record['thread_id']=sid
    store=Storage(directory)
    try:
        for resource in case['resource_state']['resources']:
            asset={k:v for k,v in resource.items() if k!='rows'}
            store.add_asset(asset,resource.get('rows',[]))
    finally:store.close()
    for resource in case['resource_state']['resources']:
        session=handle({**params,'action':'attach_asset','id':sid,'revision':session['revision'],'asset_id':resource['id']})
    if case['prior_conversation']:
        # Explicit frozen context fixture, not claimed to be generated in this evaluation.
        store=Storage(directory);graph,conn=build_graph(store,conversation=lambda _: {})
        try:graph.update_state({'configurable':{'thread_id':sid}}, {'session_id':sid,'messages':case['prior_conversation'],
                              'resources':case['resource_state']['resources'],'confirmed':False},as_node='save')
        finally:conn.close();store.close()
    answered=set();content=case['request'];question_id=None;reference=case['reference_time'];revision_sent=False
    try:
        for turn in range(case['max_user_answers']+2):
            request={**params,'action':'message','id':sid,'revision':session['revision'],'content':content,
                     'user_timezone':case['user_timezone'],'gmail_connected':case['resource_state']['gmail_connected_metadata']}
            if question_id:request['question_id']=question_id
            # Each call reopens storage/checkpoint via the real worker handler.
            with patch('agent_builder.design.temporal_context._utc_now',lambda:datetime.fromisoformat(reference.replace('Z','+00:00'))):
                session=handle(request)
            record['turns'].append(sanitize({'user':content,'question_id':question_id,'reference_time':reference,
                                           'response':session},tracer.secrets))
            if turn==0:record['actual_route']=route(session)
            if route(session)=='READY':
                if case['revision_after_ready'] and not revision_sent:
                    content=case['revision_after_ready']['text'];reference=case['revision_after_ready']['reference_time']
                    question_id=None;revision_sent=True;continue
                break
            if route(session)!='CLARIFY':break
            questions=session.get('pending_questions') or [{'text':q} for q in (session.get('draft') or {}).get('questions',[])]
            matches=[(q,choose_answer(case,q,answered)) for q in questions]
            match=next(((q,a) for q,a in matches if a['status']=='matched'),None)
            if match is None:
                record['answer_stop_reason']=[a['status'] for _,a in matches] or ['no_visible_question'];break
            q,a=match;content=a['text'];question_id=q.get('id');answered.update(a['slots'])
        record['final_route']=route(session);draft=session.get('draft')
        record['workflow']=deepcopy((draft or {}).get('workflow'))
        record['validation']={'status':session.get('status'),'issues':session.get('issues',[])}
        if route(session)=='READY' or (draft or {}).get('workflow'):
            record['schema_valid']=bool(draft and draft.get('workflow') and route(session)=='READY' and not session.get('issues'))
        record['task_partial_scores']=task_scores(draft,case['gold'])
        record['semantic_metrics_status']='requires_independent_adjudication_against_frozen_gold'
        if case['gold']['execution']['mode']=='local_synthetic' and record['schema_valid'] and can_execute_locally(draft):
            saved=handle({**params,'action':'confirm','id':sid,'revision':session['revision']})
            result=handle({**params,'action':'test','id':saved['saved_agent_id'],'content':'',
                           'file_ids':[r['id'] for r in case['resource_state']['resources']]})
            record['local_execution']=sanitize(result,tracer.secrets)
            record['execution_success']=result['status']=='succeeded' and result.get('rows')==case['gold']['execution']['expected_rows']
        record['status']='completed'
    except Exception as error:
        record.update(status='failed',error_type=type(error).__name__,actual_route=record.get('actual_route','SYSTEM_ERROR'))
    record.update(elapsed_seconds=time.monotonic()-start,
                  live_model_calls=sum(c['http_dispatch_attempts'] for c in tracer.calls[call_start:]),
                  model_call_records=len(tracer.calls)-call_start,
                  model_responses_received=sum(c['model_response_observed'] for c in tracer.calls[call_start:]),
                  model_calls=tracer.calls[call_start:],stages=tracer.stages[stage_start:])
    if record['live_model_calls']==0 and record['status']=='completed':
        record['kind']='code_only_recipe_not_live_model_evidence' if not record['model_call_records'] else 'no_model_dispatch_not_live_model_evidence'
    return sanitize(record,tracer.secrets)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus',type=Path,default=ROOT/'docs/qwen-live-evaluation-cases.json')
    parser.add_argument('--phase',choices=['baseline','after'],required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--environment',type=Path)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--max-calls',type=int,default=120)
    parser.add_argument('--case',action='append')
    parser.add_argument('--allow-local-endpoint',help='Exact loopback /v1 URL explicitly selected for this evaluation')
    args=parser.parse_args()
    if args.output.exists():raise SystemExit('Output exists; preserve prior runs and use a new path')
    corpus=json.loads(args.corpus.read_text(encoding='utf-8'))
    validate_corpus(corpus,{t['id']:t for t in TOOLS})
    cases=[c for c in corpus['cases'] if not args.case or c['id'] in args.case]
    if not cases:raise SystemExit('No matching cases')
    manifest=code_manifest()
    report={'version':1,'phase':args.phase,'created_at':datetime.now(timezone.utc).isoformat(),
            'gold_sha256':corpus['gold_sha256'],'catalog_fingerprint':corpus['catalog_fingerprint'],
            'code_manifest':manifest,'evaluation_harness_manifest':harness_manifest(),
            'requested_scenarios':len(cases),'evaluated_live_scenarios':0,
            'inference_calls':0,'status':'not_run','cases':[],'metrics':metrics([]),
            'connector_policy':'No Gmail/Notion/customer DB access or email sending',
            'scope':'backend service/checkpoint/model E2E, API and browser covered separately'}
    baseline=json.loads(args.baseline.read_text(encoding='utf-8')) if args.baseline else None
    if args.phase=='after':require_complete_baseline(baseline,corpus)
    if baseline and baseline.get('evaluation_harness_manifest')!=report['evaluation_harness_manifest']:
        raise SystemExit('Evaluation harness differs from baseline; use a separate baseline')
    exit_code=0
    env=json.loads(args.environment.read_text(encoding='utf-8')) if args.environment else None
    report['environment']=env
    if baseline and baseline.get('evaluated_live_scenarios',0)==0:
        report.update(status='not_run',reason='No completed live baseline exists; no model-driven production change to compare')
    elif not args.live:
        report.update(status='blocked' if env else 'not_run',reason=(env or {}).get('status','Live inference was not requested'))
    else:
        provider=os.getenv('A4B_MAIN_PROVIDER');endpoint=os.getenv('A4B_MAIN_BASE_URL','');model=os.getenv('A4B_MAIN_MODEL')
        if provider not in ('ollama','vllm') or not model:raise SystemExit('Explicit Qwen configuration required; no fallback is permitted')
        policy=endpoint_policy(endpoint,args.allow_local_endpoint)
        identity=environment_identity(policy,provider)
        report['evaluation_environment']=identity
        if baseline and baseline.get('evaluation_environment') is None:
            raise SystemExit('Baseline lacks environment identity; do not mix legacy and newly measured environments')
        from qwen_live_trace import Trace
        # Disable all third-party telemetry and model output caches for the isolated evaluation.
        os.environ['LANGCHAIN_TRACING_V2']='false';os.environ['LANGSMITH_TRACING']='false'
        os.environ['A4B_STORAGE']='sqlite';os.environ['EASYFLOW_PUBLIC_URL']='http://127.0.0.1'
        os.environ.pop('A4B_DATABASE_URL',None);os.environ.pop('A4B_BUSINESS_RECORDS',None)
        os.environ.pop('A4B_TIMING_LOG',None)
        os.environ['A4B_REQUIRE_LOCAL_MODEL']='1'
        # Ambient proxies must not redirect local or RunPod model requests elsewhere.
        for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
            os.environ.pop(name,None)
        tracer=Trace(endpoint,args.max_calls,allow_local_endpoint=args.allow_local_endpoint,
                     models=[model,os.getenv('A4B_CAPABILITY_MODEL') or model],provider=provider)
        keys=['A4B_MAIN_PROVIDER','A4B_MAIN_MODEL','A4B_CAPABILITY_MODEL','A4B_PLANNER_MODE','A4B_PLANNER_PROMPT',
              'A4B_CAPABILITY_MODE','A4B_CONDITION_MODE','A4B_REQUIREMENT_MODE','A4B_VERIFIER_MODE',
              'A4B_OLLAMA_NUM_CTX','A4B_OLLAMA_NUM_PREDICT','A4B_OLLAMA_TEMPERATURE','A4B_MAIN_TIMEOUT_S',
              'A4B_REQUIREMENT_GENERATION_MODE','A4B_TOOL_CATALOG_MODE','A4B_TOOL_ADVICE_MODE',
              'A4B_TOOL_ADVICE_BACKEND','A4B_OLLAMA_MODEL_ALIASES']
        settings={k:os.getenv(k) for k in keys};report['effective_process_settings']=settings
        if baseline and baseline.get('effective_process_settings')!=settings:raise SystemExit('Model/settings differ from baseline')
        directory=ROOT/'data/qwen-live-evaluation';directory.mkdir(parents=True,exist_ok=True)
        def refresh_progress():
            report.update(tracer.counts())
            report['inference_calls']=report['model_responses_received']
            report['inference_calls_accounting']='Confirmed model responses only; HTTP dispatch attempts do not prove server inference. dispatch_pending in a saved snapshot means dispatch may or may not have begun.'
            report['probe_calls']=[c for c in tracer.calls if c['stage']=='probe']
            report['evaluated_live_scenarios']=sum(r.get('live_model_calls',0)>0 for r in report['cases'])
            report['not_run_case_ids']=[c['id'] for c in cases if c['id'] not in {r['id'] for r in report['cases']}]
            if report.get('active_case_id'):
                report['active_case_calls']=tracer.calls[report['active_case_call_offset']:]
            report['metrics']=metrics(report['cases'])
        def save_progress():
            refresh_progress()
            write_report(args.output,sanitize(report,tracer.secrets))
        tracer.on_change=save_progress
        report['status']='running'
        try:
            save_progress()
            with tracer.installed():
                report['probe']=live_probe(tracer,identity,(baseline or {}).get('evaluation_environment'))
                save_progress()
                for case in cases:
                    if len(tracer.calls)>=args.max_calls:
                        report.update(status='incomplete',reason='model_call_budget');break
                    report.update(active_case_id=case['id'],active_case_call_offset=len(tracer.calls),active_case_calls=[])
                    save_progress()
                    with tempfile.TemporaryDirectory(prefix=case['id']+'-',dir=directory) as temporary:
                        result=run_case(case,model,tracer,Path(temporary))
                    report['cases'].append(result)
                    for key in ('active_case_id','active_case_call_offset','active_case_calls'):report.pop(key,None)
                    save_progress()
                    print(json.dumps({'case':case['id'],'status':result['status'],'calls':result['live_model_calls']}),flush=True)
                    if any(c.get('status')=='failed' for c in result.get('model_calls',[])):
                        report.update(status='incomplete',reason='transport_or_model_failure_circuit_breaker');break
                if report['status']=='running':report['status']='completed'
        except KeyboardInterrupt:
            report.update(status='paused',reason='interrupted_by_user',
                          interrupted_case_id=report.get('active_case_id'),
                          interrupted_case_inference_calls=tracer.calls[report.get('active_case_call_offset',len(tracer.calls)):])
        except InstrumentationFatal as error:
            exit_code=2
            report.update(status='blocked_instrumentation',reason='evaluation_snapshot_failure',
                          instrumentation_error=error.diagnostic,interrupted_case_id=report.get('active_case_id'))
        except Exception as error:report.update(status='blocked',reason=type(error).__name__,
            http_status=getattr(error,'code',None),probe_step=getattr(tracer,'probe_step',None))
        refresh_progress()
        report=sanitize(report,tracer.secrets)
    done={r['id'] for r in report['cases']}
    report['cases'] += [{'id':c['id'],'status':'not_run','reason':report.get('reason','not reached'),'live_model_calls':0} for c in cases if c['id'] not in done]
    report['metrics']=metrics(report['cases'])
    try:write_report(args.output,report)
    except InstrumentationFatal:
        # The last valid atomic snapshot remains available; stderr has the safe
        # persistence diagnostic. Never continue inference after this failure.
        return 2
    print(json.dumps({'status':report['status'],'evaluated_live_scenarios':report['evaluated_live_scenarios'],'inference_calls':report['inference_calls']}))
    return exit_code


if __name__=='__main__':raise SystemExit(main())
