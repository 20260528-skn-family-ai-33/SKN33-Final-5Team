"""Export saved standalone MinerU blocks for fixed OmniDocBench evaluation."""
import argparse
import hashlib
import json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
corpus=a.manifest.resolve().parent;run=a.run.resolve();context=json.loads((run/'context.json').read_text());manifest=json.loads(a.manifest.read_text())
if sha(a.manifest)!=context['manifest_sha256']:raise ValueError('manifest mismatch')
cases=[c for c in manifest['cases'] if c['suite']=='omnidocbench' and c['id'] in context['case_ids']]
ledger={(r['case_id'],r['arm']):r for r in json.loads((run/'ledger.json').read_text())}
out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);folder=out/'mineru-only';folder.mkdir();records=[]
for case in cases:
 state=ledger.get((case['id'],'mineru-only'),{'status':'missing'});source=run/'mineru-only'/case['id']/'response.json';pieces=[];error=None
 if state['status'] in {'succeeded','partial'} and source.exists():
  try:
   data=json.loads(source.read_text())
   for block in data['blocks']:
    kind=block.get('kind','');text=block.get('text','') or ''
    if kind=='table':
     table=block.get('data',{}).get('html') or (text if '<table' in text.lower() else '')
     if table:pieces.append(table)
    elif kind in {'formula','equation','display_formula','equation_isolated'} and text.strip():pieces.append('$$\n'+text+'\n$$')
    elif kind in {'title','section_header'} and text.strip():pieces.append('## '+text)
    elif text.strip():pieces.append(text)
  except Exception as e:error=type(e).__name__+': '+str(e)
 pred=folder/(Path(case['image_id']).stem+'.md');pred.write_text('\n\n'.join(pieces),encoding='utf-8')
 records.append({'case_id':case['id'],'arm':'mineru-only','image_id':case['image_id'],'run_status':state['status'],'export_error':error,
  'prediction_sha256':sha(pred),'prediction_bytes':pred.stat().st_size})
(out/'export-ledger.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
(out/'metadata.json').write_text(json.dumps({'manifest_sha256':sha(a.manifest),'source_context_sha256':sha(run/'context.json'),
 'export_code_sha256':sha(Path(__file__)),'gold_sha256':sha(corpus/'gold/omni-selected.json'),
 'interpretation':'MinerU adapter block order with HTML tables; failed/missing exported empty'},indent=2))
print(json.dumps({'records':len(records),'errors':sum(bool(r['export_error']) for r in records),'output':str(out)}))
