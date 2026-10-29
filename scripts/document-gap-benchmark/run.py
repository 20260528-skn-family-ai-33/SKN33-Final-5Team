"""Run fixed comparison arms; only input files are passed to inference workers."""
import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
ARMS=['service-default','service-no-supplement','docling-base','docling-formula','docling-table-fast','docling-chart']
p=argparse.ArgumentParser()
p.add_argument('--manifest',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--arms',nargs='+',choices=ARMS,default=ARMS[:-1])
p.add_argument('--cases',nargs='+')
p.add_argument('--timeout',type=int,default=300)
p.add_argument('--service-python',default=str(ROOT/'Backend/.venv-document-analysis/bin/python'))
p.add_argument('--docling-python',default=str(ROOT/'Backend/.venv-docling/bin/python'))
a=p.parse_args()
if a.timeout <= 0: p.error('--timeout must be positive')
manifest=a.manifest.resolve(); corpus=manifest.parent
cases=json.loads(manifest.read_text())['cases']
if len({c['id'] for c in cases}) != len(cases): p.error('Duplicate case IDs in manifest')
if a.cases:
    wanted=set(a.cases); known={c['id'] for c in cases}
    if wanted-known: p.error('Unknown case ids: '+str(sorted(wanted-known)))
    cases=[c for c in cases if c['id'] in wanted]
if len(set(a.arms)) != len(a.arms): p.error('Duplicate arms')

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

for c in cases:
    path=(corpus/c['input']).resolve()
    if not path.is_relative_to(corpus) or not path.is_file() or sha(path)!=c['sha256']: raise ValueError('Input hash mismatch: '+c['id'])
    gold=(corpus/c['gold']).resolve()
    if not gold.is_relative_to(corpus) or not gold.is_file() or sha(gold)!=c['gold_sha256']: raise ValueError('Gold hash mismatch: '+c['id'])
deployment=ROOT/'deployment-manifest.json'
if deployment.exists():
    for entry in json.loads(deployment.read_text())['files']:
        source=(ROOT/entry['path']).resolve()
        if not source.is_relative_to(ROOT) or not source.is_file() or sha(source)!=entry['sha256']:
            raise ValueError('Deployment source hash mismatch: '+entry['path'])
out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
context={'manifest_sha256':sha(manifest),'arms':a.arms,'case_ids':[c['id'] for c in cases],
    'timeout_seconds':a.timeout,'concurrency':1,'order':'alternating arm order per case',
    'model_lifecycle':'new process per request; model files may be cached', 'platform':platform.platform(),
    'source_hashes':{str(f.relative_to(ROOT)):sha(f) for f in sorted(HERE.glob('*.py'))},
    'environment_settings':{k:os.environ.get(k) for k in ['A4B_PDF_PARSER_MODE','A4B_PDF_DEVICE','A4B_PADDLE_DEVICE',
        'A4B_DOCUMENT_MAX_SUPPLEMENTS','MINERU_MODEL_SMALL_BACKEND','MINERU_MODEL_VLM_ENGINE','MINERU_LLAMA_CPP_BACKEND']},
    'python_executables':{'service':a.service_python,'docling':a.docling_python}}
context['deployment_manifest_sha256']=sha(deployment) if deployment.exists() else None
for command,key in [(['nvidia-smi','--query-gpu=name,driver_version,memory.total','--format=csv,noheader'],'gpu'),
                    ([a.docling_python,'--version'],'docling_python_version'),([a.service_python,'--version'],'service_python_version')]:
    try: context[key]=subprocess.check_output(command,text=True,stderr=subprocess.STDOUT,timeout=20).strip()
    except (OSError,subprocess.SubprocessError) as e: context[key]='unavailable: '+str(e)
(out/'context.json').write_text(json.dumps(context,ensure_ascii=False,indent=2)+'\n')
ledger=[]
def save():
    target=out/'ledger.json'; temp=out/'ledger.tmp'; temp.write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n');temp.replace(target)
for i,c in enumerate(cases):
    src=(corpus/c['input']).resolve()
    for arm in (a.arms if i%2==0 else list(reversed(a.arms))):
        entry={'case_id':c['id'],'arm':arm,'input_sha256':c['sha256'],'status':'failed','elapsed_seconds':None}
        folder=out/arm/c['id']; folder.mkdir(parents=True)
        if arm.startswith('docling-') and c['format']!='pdf':
            entry.update(status='not_applicable',reason='Docling option comparison is PDF-only')
            ledger.append(entry);save();continue
        env=os.environ.copy()
        env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True',MINERU_MODEL_SOURCE='local')
        if arm.startswith('service-'):
            env['A4B_DOCUMENT_MAX_SUPPLEMENTS']='0' if arm=='service-no-supplement' else '16'
            env.pop('A4B_DOCUMENT_BENCHMARK_ARM',None)
            cmd=[a.service_python,str(ROOT/'Backend/scripts/analyze_document.py'),str(src),str(folder/'result.json'),'--work-dir',str(folder/'work')]
        else:
            cmd=[a.docling_python,str(HERE/'docling_worker.py'),'--input',str(src),'--arm',arm,'--output',str(folder/'worker')]
        start=time.perf_counter()
        try:
            with (folder/'process.log').open('wb') as log:
                process=subprocess.Popen(cmd,env=env,stdout=log,stderr=log,cwd=ROOT,start_new_session=True)
                try:
                    return_code=process.wait(timeout=a.timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL)
                    process.wait()
                    raise
            entry['exit_code']=return_code
            if arm.startswith('service-') and (folder/'result.json').is_file():
                data=json.loads((folder/'result.json').read_text())
                entry['parser_status']=data.get('parser_status'); entry['error_code']=data.get('error_code')
                entry['status']='succeeded' if return_code==0 and data.get('parser_status')=='succeeded' else 'partial' if return_code==0 and data.get('parser_status')=='partial' else 'failed'
            elif not arm.startswith('service-') and (folder/'worker'/'metadata.json').is_file():
                meta=json.loads((folder/'worker'/'metadata.json').read_text())
                st=meta.get('status','').lower()
                entry['status']='partial' if 'partial' in st or meta.get('errors') else 'succeeded' if 'success' in st and return_code==0 else 'failed'
                entry['parser_status']=meta.get('status')
        except subprocess.TimeoutExpired:
            entry['status']='timeout';entry['reason']='worker timeout'
        except (OSError,ValueError,json.JSONDecodeError) as e:
            entry['status']='failed';entry['reason']=str(e)
        entry['elapsed_seconds']=time.perf_counter()-start
        entry['input_sha256_after']=sha(src)
        ledger.append(entry);save()
        print(json.dumps(entry,ensure_ascii=False),flush=True)
(out/'finished.json').write_text(json.dumps({'records':len(ledger),'status':'finished'},indent=2)+'\n')
