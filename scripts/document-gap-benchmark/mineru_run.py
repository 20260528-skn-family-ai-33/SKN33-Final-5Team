"""Isolated MinerU-only reference on fixed PDF inputs, one fresh process per case."""
import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--timeout',type=int,default=300);p.add_argument('--cases',nargs='+');p.add_argument('--python',default=str(ROOT/'Backend/.venv-mineru/bin/python'));a=p.parse_args()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
corpus=a.manifest.resolve().parent;manifest=json.loads(a.manifest.read_text());cases=[c for c in manifest['cases'] if c['format']=='pdf']
if a.cases:
 missing=set(a.cases)-{c['id'] for c in cases}
 if missing:raise ValueError('unknown/non-PDF cases: '+str(missing))
 cases=[c for c in cases if c['id'] in a.cases]
for c in cases:
 for field,key in [('input','sha256'),('gold','gold_sha256')]:
  path=(corpus/c[field]).resolve()
  if not path.is_relative_to(corpus) or not path.is_file() or sha(path)!=c[key]:raise ValueError('hash mismatch: '+c['id'])
out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
context={'manifest_sha256':sha(a.manifest),'code_sha256':sha(Path(__file__)),'worker_sha256':sha(ROOT/'Backend/document_analysis/engine_worker.py'),
 'arms':['mineru-only'],'case_ids':[c['id'] for c in cases],'timeout_seconds':a.timeout,'model_lifecycle':'one fresh process per file',
 'settings':{k:os.environ.get(k) for k in ['MINERU_HOME','MINERU_MODEL_SMALL_BACKEND','MINERU_MODEL_VLM_ENGINE','MINERU_LLAMA_CPP_BACKEND','VK_DRIVER_FILES']}}
(out/'context.json').write_text(json.dumps(context,ensure_ascii=False,indent=2))
ledger=[]
for c in cases:
 folder=out/'mineru-only'/c['id'];folder.mkdir(parents=True)
 request={'engine':'mineru','source':str((corpus/c['input']).resolve()),'raw':str(folder/'raw.json')}
 (folder/'request.json').write_text(json.dumps(request));env={**os.environ,'MINERU_MODEL_SOURCE':'local','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
 cmd=[a.python,str(ROOT/'Backend/document_analysis/engine_worker.py'),str(folder/'request.json'),str(folder/'response.json')]
 result={'case_id':c['id'],'arm':'mineru-only','input_sha256':c['sha256'],'status':'failed'};start=time.perf_counter()
 try:
  with (folder/'process.log').open('wb') as f:
   proc=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=f,stderr=f,start_new_session=True)
   try:code=proc.wait(timeout=a.timeout)
   except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait();raise
  result['exit_code']=code
  if (folder/'response.json').is_file():
   value=json.loads((folder/'response.json').read_text());result['error_code']=value.get('error')
   if code==0 and not value.get('error'):result['status']='partial' if value.get('partial') else 'succeeded'
 except subprocess.TimeoutExpired:result['status']='timeout'
 except (OSError,ValueError) as e:result['reason']=str(e)
 result['elapsed_seconds']=time.perf_counter()-start;result['input_sha256_after']=sha(corpus/c['input'])
 ledger.append(result);tmp=out/'ledger.tmp';tmp.write_text(json.dumps(ledger,ensure_ascii=False,indent=2));tmp.replace(out/'ledger.json');print(json.dumps(result,ensure_ascii=False),flush=True)
(out/'finished.json').write_text(json.dumps({'records':len(ledger),'status':'finished'}))
