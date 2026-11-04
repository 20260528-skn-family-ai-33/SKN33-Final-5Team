"""Summarize explicitly completed manual labels; pending items never become passes."""
import argparse
import collections
import csv
import json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('review',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
with a.review.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
counts=collections.defaultdict(collections.Counter);issues=[]
for i,r in enumerate(rows,2):
    decision=r['decision'].strip().upper()
    if decision not in {'PASS','FAIL','NA','PENDING'}: issues.append(f'row {i}: invalid decision {decision}')
    elif decision in {'PASS','FAIL','NA'} and (not r['reason'].strip() or not r['reviewer'].strip()): issues.append(f'row {i}: reason and reviewer required')
    elif decision in {'PASS','FAIL'} and not r['evidence_path'].strip(): issues.append(f'row {i}: evidence_path required')
    else: counts[(r['arm'],r['check'])][decision]+=1
if issues: raise ValueError('\n'.join(issues))
result=[{'arm':arm,'check':check,'pass':v['PASS'],'fail':v['FAIL'],'na':v['NA'],'pending':v['PENDING'],
         'observed_pass_rate':v['PASS']/(v['PASS']+v['FAIL']) if v['PASS']+v['FAIL'] else None} for (arm,check),v in sorted(counts.items())]
if a.output.exists(): raise FileExistsError(a.output)
a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'groups':len(result),'output':str(a.output)},ensure_ascii=False))
