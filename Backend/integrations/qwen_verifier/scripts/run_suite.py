"""fixture 회귀 또는 명시적 live 연결 점검. 두 결과를 모델 성능으로 혼합하지 않는다."""
import argparse,json,sys,time,hashlib,platform
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from a4b_verifier.contracts import VerificationInput
from a4b_verifier.client import OllamaConfig,health
from a4b_verifier.client import SYSTEM
from a4b_verifier.contracts import VerificationOutput
from a4b_verifier.service import verify

def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['fixture','live'],required=True)
    p.add_argument('--cases',default='',help='쉼표로 구분한 사례 ID. live는 semantic 사례만 사용')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    allcases=json.loads((ROOT/'examples/cases.json').read_text())['cases']
    cases=[c for c in allcases if a.mode=='fixture' or c['kind']=='semantic']
    if a.cases:
        ids=a.cases.split(',');cases=[c for c in cases if c['id'] in ids]
        if {c['id'] for c in cases}!=set(ids):raise ValueError('선택 사례 ID를 확인하세요.')
    config=OllamaConfig.from_env();before=health(config) if a.mode=='live' else None
    if before and not before['model_present']:raise RuntimeError('로컬 모델이 없습니다. 자동 다운로드하지 않습니다.')
    records=[]
    for c in cases:
        x=VerificationInput.model_validate_json((ROOT/'examples'/c['input']).read_text())
        fixture=json.loads((ROOT/'examples'/c['fixture']).read_text()) if a.mode=='fixture' else None
        result=verify(x,mode=a.mode,fixture=fixture,config=config)
        prediction=result.model_output
        checks={'route':result.route==c['expected']['route'],'required_code_findings':set(c['expected']['required_codes'])<={f.code for f in result.findings},
            'never_executes':result.execution_allowed is False,'response_valid':result.error_code is None}
        if a.mode=='live':
            checks['request_status']=bool(prediction and prediction.request_status==c['expected']['request_status'])
            checks['draft_verdict']=bool(prediction and prediction.draft_verdict==c['expected']['draft_verdict'])
            checks['reflection_verdict']=bool(prediction and prediction.reflection_verdict==c['expected']['reflection_verdict'])
            checks['missing_conditions']=bool(prediction and {m.code for m in prediction.missing_conditions}==set(c['expected']['missing_codes']))
        records.append({'id':c['id'],'expected':c['expected'],'input_sha256':hashlib.sha256((ROOT/'examples'/c['input']).read_bytes()).hexdigest(),
            'result':result.model_dump(mode='json'),'checks':checks,'all_passed':all(checks.values())})
        print(json.dumps({'id':c['id'],'checks':checks,'latency_ms':round(result.latency_ms,1)},ensure_ascii=False),flush=True)
    after=health(config) if a.mode=='live' else None
    report={'mode':a.mode,'purpose':'연동 점검 예제이며 독립 모델 성능 벤치마크가 아닙니다.',
        'config':asdict(config) if a.mode=='live' else None,'platform':platform.platform(),'python':platform.python_version(),
        'system_prompt_sha256':hashlib.sha256(SYSTEM.encode()).hexdigest(),
        'output_schema_sha256':hashlib.sha256(json.dumps(VerificationOutput.model_json_schema(),sort_keys=True).encode()).hexdigest(),
        'source_hashes':{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [Path(__file__),ROOT/'a4b_verifier/client.py',ROOT/'a4b_verifier/service.py',ROOT/'a4b_verifier/validation.py',ROOT/'a4b_verifier/contracts.py']},
        'before':before,'after':after,'model_digest_unchanged':bool(before and after and before['model_digest']==after['model_digest']) if before else None,
        'cases':len(records),'passed':sum(r['all_passed'] for r in records),'records':records,'training_runs':0}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if report['passed']!=len(records):sys.exit(1)
if __name__=='__main__':main()
