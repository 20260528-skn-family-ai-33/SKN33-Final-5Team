"""Export selected service blocks for fixed OmniDocBench evaluation."""
import argparse
import hashlib
import json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
corpus=a.manifest.resolve().parent;run=a.run.resolve();ctx=json.loads((run/'context.json').read_text())
if sha(a.manifest)!=ctx['manifest_sha256']:raise ValueError('manifest mismatch')
cases=[c for c in json.loads(a.manifest.read_text())['cases'] if c['suite']=='omnidocbench' and c['id'] in ctx['case_ids']]
ledger={(r['case_id'],r['arm']):r for r in json.loads((run/'ledger.json').read_text())}
out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);records=[]
for arm in ctx['arms']:
 if not arm.startswith('service-'):continue
 dest=out/arm;dest.mkdir()
 for c in cases:
  state=ledger.get((c['id'],arm),{'status':'missing'});file=run/arm/c['id']/'result.json';pieces=[];error=None
  if state['status'] in {'succeeded','partial'} and file.exists():
   try:
    data=json.loads(file.read_text())['document_analysis']
    for block in data['blocks']:
     if not block.get('selected',True):continue
     text=block.get('text','') or '';kinds=block.get('content_types',[])
     if 'table' in kinds:text=block.get('data',{}).get('html') or text
     elif 'formula' in kinds and text.strip() and not text.lstrip().startswith('$$'):text='$$\n'+text+'\n$$'
     if text.strip():pieces.append(text)
   except Exception as e:error=type(e).__name__+': '+str(e)
  pred=dest/(Path(c['image_id']).stem+'.md');pred.write_text('\n\n'.join(pieces),encoding='utf-8')
  records.append({'case_id':c['id'],'arm':arm,'image_id':c['image_id'],'run_status':state['status'],
    'export_error':error,'prediction_sha256':sha(pred),'prediction_bytes':pred.stat().st_size})
(out/'export-ledger.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
(out/'metadata.json').write_text(json.dumps({'manifest_sha256':sha(a.manifest),'source_context_sha256':sha(run/'context.json'),
 'export_code_sha256':sha(Path(__file__)),'gold_sha256':sha(corpus/'gold/omni-selected.json'),
 'interpretation':'selected service blocks in stored order; failed/missing exported empty'},indent=2))
print(json.dumps({'records':len(records),'errors':sum(bool(r['export_error']) for r in records),'output':str(out)}))
