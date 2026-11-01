"""Compare stable references and selected content across the two service arms."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run=a.run.resolve()
ledger=json.loads((run/'ledger.json').read_text());ix={(r['case_id'],r['arm']):r for r in ledger};cases=sorted({r['case_id'] for r in ledger});pairs=[]
for case in cases:
 x=ix.get((case,'service-no-supplement'));y=ix.get((case,'service-default'))
 row={'case_id':case,'baseline_status':x['status'] if x else 'missing','supplement_status':y['status'] if y else 'missing'}
 if not x or not y or x['status'] not in {'succeeded','partial'} or y['status'] not in {'succeeded','partial'}:pairs.append(row);continue
 try:
  left=json.loads((run/'service-no-supplement'/case/'result.json').read_text())['document_analysis']
  right=json.loads((run/'service-default'/case/'result.json').read_text())['document_analysis']
  def chosen(d):return [b for b in d['blocks'] if b.get('selected',True) and b.get('text','').strip()]
  def fingerprint(b):return (b['engine'],json.dumps(b['native_location'],sort_keys=True,ensure_ascii=False),tuple(b['content_types']),b['text'])
  l,r=chosen(left),chosen(right);lc=Counter(map(fingerprint,l));rc=Counter(map(fingerprint,r))
  original_ids={b['id']:b for b in left['blocks']};new_ids={b['id']:b for b in right['blocks']}
  changes=[];parent_changes=[]
  for key in original_ids.keys() & new_ids.keys():
   before,after=original_ids[key],new_ids[key]
   if (before['engine'],before['content_types'],before['native_location']) != (after['engine'],after['content_types'],after['native_location']):changes.append(key)
   if (before.get('parent_object_id'),before.get('unit_id')) != (after.get('parent_object_id'),after.get('unit_id')):parent_changes.append(key)
  row.update(source_same=left['source_sha256']==right['source_sha256'],baseline_selected=len(l),supplement_selected=len(r),
    selected_missing_from_supplement=sum((lc-rc).values()),selected_added_by_supplement=sum((rc-lc).values()),
    exact_selected_order_same=[fingerprint(z) for z in l]==[fingerprint(z) for z in r],
    common_ids_with_changed_identity=changes,common_ids_with_changed_parent_or_unit=parent_changes,
    baseline_block_count=len(left['blocks']),supplement_block_count=len(right['blocks']))
 except (OSError,KeyError,ValueError) as e:row['comparison_error']=str(e)
 pairs.append(row)
if a.output.exists():raise FileExistsError(a.output)
a.output.write_text(json.dumps({'interpretation':'descriptive comparison of selected text blocks by engine/location/type/literal and ID identity; not proof of correct reading order, semantic hierarchy, or complete content',
 'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'pairs':pairs},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'pairs':len(pairs),'output':str(a.output)}))
