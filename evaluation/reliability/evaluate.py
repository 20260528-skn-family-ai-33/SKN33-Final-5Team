"""Frozen, synthetic diagnostic questions through the real two-agent worker.
The key arrives on stdin, stays in memory, and is never written to reports.
"""
import copy
import sys,json,time,hashlib,os,uuid,traceback,csv,io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(os.environ.get('A4B_EVAL_OUTPUT',str(Path(__file__).resolve().parent/'output'))).resolve()
ROOT.mkdir(parents=True,exist_ok=True)
case_source=Path(__file__).resolve().parent/'questions.json'
if not (ROOT/'questions.json').exists():(ROOT/'questions.json').write_bytes(case_source.read_bytes())
PROJECT=Path(os.environ.get('A4B_EVAL_PROJECT',str(Path(__file__).resolve().parents[2]))).resolve()
sys.path.insert(0,str(PROJECT/'Backend'))
os.environ['LANGCHAIN_TRACING_V2']='false';os.environ['LANGSMITH_TRACING']='false'
from agent_builder.worker import handle
from agent_builder.models import AgentDraft
from agent_builder.runtime import public_error
from agent_builder.workflow import workflow_errors


def asset_payload(item):
    if item['kind']=='document':
        text=item['text'];return {'asset':{'name':item['name'],'kind':'document','format':'txt','text':text,'pages':[text],'columns':[],'profile':{},'preview':[],'row_count':0,'hash':hashlib.sha256(text.encode()).hexdigest()},'rows':[]}
    rows=item['rows'];columns=list(dict.fromkeys(k for r in rows for k in r));profile={}
    for col in columns:
        vals=[r.get(col) for r in rows];filled=[v for v in vals if v not in [None,'']]
        profile[col]={'types':list(dict.fromkeys(type(v).__name__.replace('str','string').replace('int','number').replace('float','number') for v in filled)),'missing':len(vals)-len(filled),'duplicateValues':len(filled)-len({json.dumps(v) for v in filled}),'numericStrings':sum(isinstance(v,str) and v.replace('.','',1).isdigit() for v in filled)}
    return {'asset':{'name':item['name'],'kind':'table','format':'json','text':'','pages':[],'columns':columns,'profile':profile,'preview':rows[:3],'row_count':len(rows),'hash':hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest()},'rows':rows}


def expected_match(case,run):
    checks={'run_succeeded':run['status']=='succeeded'}
    if 'expected_rows' in case:
        actual=run.get('rows',[]);expected=case['expected_rows']
        if case.get('unordered'):
            canonical=lambda rows:sorted(json.dumps(r,ensure_ascii=False,sort_keys=True) for r in rows)
            checks['rows_exact']=canonical(actual)==canonical(expected)
        else:checks['rows_exact']=actual==expected
    if 'expected_facts' in case:
        facts={r.get('field'):r.get('value') for r in run.get('rows',[])}
        checks['facts_exact']=facts==case['expected_facts']
        checks['fact_evidence']=all(r.get('value') is None and not r.get('evidence') or bool(r.get('evidence')) and r['value'] in r['evidence']['quote'] for r in run.get('rows',[]))
    answer=run.get('answer','')
    if case.get('required_answer'):checks['answer_required_facts']=all(t in answer for t in case['required_answer'])
    if case.get('require_evidence'):checks['has_evidence']=bool(run.get('evidence'))
    if case.get('abstain'):
        checks['abstention_heuristic']=any(t in answer for t in ['확인할 수 없','알 수 없','확인되지 않','기록되지 않','제공되지 않','포함되어 있지 않','기재되어 있지 않','찾을 수 없'])
    if case.get('review_required'):
        reviews=run.get('reviews',[])
        checks['review_revision']=bool(reviews) and all(r['report']['verdict']!='pass' for r in reviews)
        checks['unverified_action_found']=bool(reviews) and any(f['category']=='unverified_action' for r in reviews for f in r['report']['findings'])
        checks['approval_pending']=run.get('approval_status')=='pending'
    fmt=case.get('format');artifact=run.get('artifact')
    if fmt:
        suffix='md' if fmt=='markdown' else fmt
        checks['artifact_format']=bool(artifact) and artifact['name'].endswith('.'+suffix)
    if case.get('formula_safe'):
        content=(run.get('artifact') or {}).get('content','')
        checks['formula_neutralized']="'=SUM(2,3)" in content
    if case.get('forbidden_answer'):
        checks['no_invented_personal_facts']=not any(t in answer for t in case['forbidden_answer'])
    if 'review_expected_verdict' in case:
        reviews=run.get('reviews',[])
        checks['review_present']=bool(reviews)
        verdict=case['review_expected_verdict']
        checks['review_verdict']=bool(reviews) and all(r['report']['verdict']==verdict if verdict else r['report']['verdict']!='pass' for r in reviews)
        if case.get('review_expected_category'):
            checks['review_category']=any(f['category']==case['review_expected_category'] for r in reviews for f in r['report']['findings'])
        checks['approval_pending']=run.get('approval_status')=='pending'
    return checks


def evaluate(case,credentials):
    start=time.perf_counter();directory=ROOT/'private-runs'/case['id'];directory.mkdir(parents=True,exist_ok=True)
    result={'id':case['id'],'category':case['category'],'question':case['question'],'expected_kind':case['expected_kind'],'model':credentials['model'],'real_model_requested':True,'checks':{},'stages':{},'variants':[]}
    def call(action,**data):
        before=time.perf_counter()
        try:return handle({'directory':str(directory),'api_key':credentials['api_key'],'action':action,**data})
        finally:result['stages'].setdefault(action,[]).append(round(time.perf_counter()-before,3))
    try:
        s=call('new_session',model=credentials['model'],credential_id=credentials['credential_id'])
        for item in case['assets']:s=call('upload',id=s['id'],revision=s['revision'],**asset_payload(item))
        result['session_id']=s['id']
        s=call('message',id=s['id'],revision=s['revision'],content=case['question'])
        result.update(first_status=s['status'],first_questions=copy.deepcopy((s.get('draft') or {}).get('questions',[])),first_unsupported=(s.get('draft') or {}).get('unsupported',[]),first_issues=s.get('issues',[]),first_draft=copy.deepcopy(s.get('draft')),messages=s.get('messages',[]))
        kind=case['expected_kind']
        if kind=='ready':result['checks']['first_turn_expected']=s['status']=='ready'
        elif kind=='clarify':
            qs=result['first_questions'];result['checks']['first_turn_expected']=s['status']=='needs_clarification' and 1<=len(qs)<=3 and any(t in '\n'.join(qs) for t in case['question_terms'])
            result['checks']['no_premature_ready']=s['status']!='ready'
        else:
            result['checks']['first_turn_expected']=s['status']=='unsupported' and bool(result['first_unsupported'])
            result['checks']['no_premature_ready']=s['status']!='ready'
        if s.get('draft'):
            AgentDraft.model_validate(s['draft']);result['checks']['schema_valid']=True
            result['checks']['workflow_valid']=not workflow_errors(s['draft'].get('workflow'),s['draft']['tool_ids'],s['resources'],s['draft']['execution_plan']) if s['draft'].get('workflow') else False
        if case.get('followup'):
            s=call('message',id=s['id'],revision=s['revision'],content=case['followup'])
            result.update(followup_status=s['status'],followup_questions=(s.get('draft') or {}).get('questions',[]),messages=s.get('messages',[]))
            result['checks']['followup_ready']=s['status']=='ready'
        if kind=='unsupported':return result
        if s['status']!='ready':return result
        saved=call('confirm',id=s['id'],revision=s['revision'])
        result.update(saved_status=saved['status'],execution_status=saved.get('execution_status'),execution_program=saved.get('execution_program'),confirm_issues=saved.get('issues',[]),confirm_trace=saved.get('trace',[]),messages=saved.get('messages',[]))
        result['checks']['saved']=saved['status']=='saved' and bool(saved.get('saved_agent_id'))
        if not result['checks']['saved']:return result
        aid=saved['saved_agent_id'];agent=call('agent',id=aid)['agent'];result['agent_id']=aid;result['final_draft']=agent['config']
        if case.get('tools'):result['checks']['required_tools']=all(t in agent['config']['tool_ids'] for t in case['tools'])
        if case.get('schedule'):result['checks']['schedule_exact']=all(agent['config'].get('schedule',{}).get(k)==v for k,v in case['schedule'].items())
        run=call('test',id=aid,content=case.get('run_question','저장한 조건으로 연결 자료를 처리해 결과를 저장해주세요.'),reply_draft=case.get('reply_draft'))
        result['run']=run;result['checks'].update(expected_match(case,run))
        restored=call('run',id=run['id'])['run'];result['checks']['persisted']=restored==run
        if run.get('report_path'):result['checks']['report_bytes']= (directory/run['report_path']).read_bytes()==run['artifact']['content'].encode('utf-8')
        # Source quotes must be exact substrings of the original synthetic documents.
        texts=[a['text'] for a in case['assets'] if a['kind']=='document']
        if run.get('evidence'):result['checks']['exact_source_quotes']=all(any(e.get('quote','') in t and bool(e.get('quote')) for t in texts) for e in run['evidence'])
        for variant in case.get('variants',[]):
            original=case['assets'][0];payload=asset_payload({**original,'rows':variant['rows']});uploaded=call('run_upload',id=aid,**payload)['asset'];vr=call('test',id=aid,content='저장한 조건을 이번 실행 자료에 그대로 적용해주세요.',file_ids=[uploaded['id']])
            result['variants'].append({'id':variant['id'],'expected_status':variant['expected_status'],'actual_status':vr['status'],'agreement':vr['status']==variant['expected_status'],'run_id':vr['id'],'rows':vr.get('rows'),'error':vr.get('error')})
    except Exception as error:
        result['error']=public_error(error,credentials['api_key']);result['error_type']=type(error).__name__;result['frames']=[{'file':Path(x.filename).name,'function':x.name,'line':x.lineno} for x in traceback.extract_tb(error.__traceback__)[-6:]]
    finally:
        result['seconds']=round(time.perf_counter()-start,3)
        safe=json.dumps(result,ensure_ascii=False,indent=2).replace(credentials['api_key'],'[REDACTED]')
        (ROOT/'results').mkdir(exist_ok=True);(ROOT/'results'/(case['id']+'.json')).write_text(safe)
        print(json.dumps({'id':case['id'],'first_status':result.get('first_status'),'saved':result['checks'].get('saved'),'run_status':result.get('run',{}).get('status'),'expected_first':result['checks'].get('first_turn_expected'),'seconds':result['seconds'],'error':result.get('error')},ensure_ascii=False),flush=True)
    return result


def main():
    key=os.environ.get('A4B_EVAL_API_KEY') or os.environ.get('OPENAI_API_KEY')
    if not key:raise ValueError('A4B_EVAL_API_KEY 또는 OPENAI_API_KEY를 환경변수로 지정하세요.')
    credentials={'api_key':key,'credential_id':'isolated-evaluation','model':os.environ.get('A4B_EVAL_MODEL','gpt-4.1-mini')};cases=json.loads((ROOT/'questions.json').read_text())
    selected=sys.argv[sys.argv.index('--case')+1].split(',') if '--case' in sys.argv else None
    if selected:cases=[c for c in cases if c['id'] in selected]
    cases=[c for c in cases if not (ROOT/'results'/(c['id']+'.json')).exists()]
    fingerprint={str(p.relative_to(PROJECT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (PROJECT/'Backend'/'agent_builder').rglob('*.py')}
    (ROOT/'source_hashes.json').write_text(json.dumps(fingerprint,indent=2))
    print(json.dumps({'starting':len(cases),'model':credentials['model'],'worker_parallelism':2,'key_present':bool(credentials['api_key'])}),flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(evaluate,c,credentials):c['id'] for c in cases}
        for f in as_completed(futures):f.result()
if __name__=='__main__':main()
