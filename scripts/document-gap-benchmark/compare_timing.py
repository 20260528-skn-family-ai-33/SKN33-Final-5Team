"""Paired, file-level elapsed times on identical frozen public PDF case IDs."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--docling-run',type=Path,required=True);p.add_argument('--mineru-run',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
ctx=[json.loads((r/'context.json').read_text()) for r in [a.docling_run,a.mineru_run]]
if any(c['manifest_sha256']!=sha(a.manifest) for c in ctx):raise ValueError('manifest mismatch')
cases=[c for c in json.loads(a.manifest.read_text())['cases'] if c['suite']=='omnidocbench'];ids={c['id'] for c in cases}
allrows=[]
for root in [a.docling_run,a.mineru_run]:allrows.extend(json.loads((root/'ledger.json').read_text()))
arms=['docling-base','docling-formula','docling-table-fast','mineru-only'];result={}
for arm in arms:
 rows=[r for r in allrows if r['arm']==arm and r['case_id'] in ids];times=sorted(r['elapsed_seconds'] for r in rows if r['status'] in {'succeeded','partial'})
 def pct(p):
  x=(len(times)-1)*p;lo=int(x);return times[lo]+(times[min(lo+1,len(times)-1)]-times[lo])*(x-lo)
 result[arm]={'observed':len(rows),'planned':len(ids),'success':sum(r['status']=='succeeded' for r in rows),
  'partial':sum(r['status']=='partial' for r in rows),'failed_or_timeout':sum(r['status'] in {'failed','timeout'} for r in rows),
  'mean_success_partial_seconds':statistics.mean(times) if times else None,'median_success_partial_seconds':statistics.median(times) if times else None,
  'p95_success_partial_seconds':pct(.95) if times else None,'sum_success_partial_seconds':sum(times) if times else None,
  'per_case':[{k:r.get(k) for k in ['case_id','status','elapsed_seconds','input_sha256']} for r in rows]}
if a.output.exists():raise FileExistsError(a.output)
a.output.write_text(json.dumps({'results':result,'sample':'18 fixed OmniDocBench pages','method':'fresh process, same hardware, sequential arm groups, one pass; initialization included; thread and software environments differ; not controlled pure GPU ranking',
 'manifest_sha256':sha(a.manifest),'code_sha256':sha(Path(__file__))},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'output':str(a.output),'arms':list(result)}))
