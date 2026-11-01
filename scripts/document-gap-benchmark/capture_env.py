"""Capture package, GPU and optional model-file hashes without secrets."""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
p.add_argument('--model-dir',type=Path,action='append',default=[])
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)

def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
 return h.hexdigest()

data={'platform':platform.platform(),'python':sys.version,'model_directories':[],'environments':{},'gpu':None}
try:data['gpu']=subprocess.check_output(['nvidia-smi','--query-gpu=name,uuid,driver_version,memory.total','--format=csv'],text=True,stderr=subprocess.STDOUT).strip()
except (OSError,subprocess.SubprocessError) as e:data['gpu']='unavailable: '+str(e)
for name in ['document-analysis','docling','mineru','paddle','bmt']:
 python=ROOT/'Backend'/('.venv-'+name)/'bin/python'
 if not python.exists(): data['environments'][name]={'error':'python not found'};continue
 script='import importlib.metadata,json,sys;print(json.dumps({"python":sys.version,"packages":{d.metadata.get("Name",""):d.version for d in importlib.metadata.distributions()}},sort_keys=True))'
 try:
  raw=subprocess.check_output([str(python),'-c',script],text=True,stderr=subprocess.PIPE,timeout=60)
  data['environments'][name]=json.loads(raw)
 except (subprocess.SubprocessError,OSError,ValueError) as e:data['environments'][name]={'error':str(e)}
for source in a.model_dir:
 source=source.resolve(); entry={'directory':str(source),'files':[]}
 if not source.is_dir(): entry['error']='directory not found';data['model_directories'].append(entry);continue
 for file in sorted(source.rglob('*')):
  if file.is_file():
   try:entry['files'].append({'path':str(file.relative_to(source)),'bytes':file.stat().st_size,'sha256':digest(file)})
   except OSError as e:entry['files'].append({'path':str(file.relative_to(source)),'error':str(e)})
 data['model_directories'].append(entry)
(a.output/'environment.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'output':str(a.output),'model_files':sum(len(x['files']) for x in data['model_directories'])}))
