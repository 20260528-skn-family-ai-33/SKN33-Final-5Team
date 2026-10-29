"""Paired descriptive comparison; missing/failed outcomes are never quality wins."""
import argparse
import collections
import json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--scores',type=Path,required=True);p.add_argument('--a',required=True);p.add_argument('--b',required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();rows=json.loads(a.scores.read_text());index={(r['case_id'],r['arm']):r for r in rows}
ids=sorted({r['case_id'] for r in rows if r['arm'] in {a.a,a.b}});pairs=[]
for case_id in ids:
 x=index.get((case_id,a.a));y=index.get((case_id,a.b))
 pair={'case_id':case_id,'a_status':x.get('status') if x else 'missing','b_status':y.get('status') if y else 'missing'}
 if x and y and x['status'] in {'succeeded','partial'} and y['status'] in {'succeeded','partial'}:
  left=x.get('anchor_presence');right=y.get('anchor_presence')
  if left and right and left['total']==right['total']:
   pair['anchor_found_delta_b_minus_a']=right['found']-left['found']
  if x.get('elapsed_seconds') is not None and y.get('elapsed_seconds') is not None:
   pair['elapsed_delta_b_minus_a']=y['elapsed_seconds']-x['elapsed_seconds']
 pairs.append(pair)
c=collections.Counter('improved' if p['anchor_found_delta_b_minus_a']>0 else 'worse' if p['anchor_found_delta_b_minus_a']<0 else 'unchanged' for p in pairs if 'anchor_found_delta_b_minus_a' in p)
result={'a':a.a,'b':a.b,'pairs':pairs,'anchor_comparable':sum(c.values()),'anchor_directions':dict(c),
        'interpretation':'Literal anchor presence only, on successful/partial pairs; not formula/table/chart accuracy. Failures remain in pairs.'}
if a.output.exists():raise FileExistsError(a.output)
a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'output':str(a.output),'pairs':len(pairs),'anchor_comparable':sum(c.values())}))
