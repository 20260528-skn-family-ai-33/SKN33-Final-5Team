"""Convert saved Docling JSON into ordered text + HTML tables for OmniDocBench."""
import argparse
import hashlib
import json
from pathlib import Path
from docling_core.types.doc import DoclingDocument, TableItem
p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();root=a.manifest.resolve().parent;run=a.run.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
manifest=json.loads(a.manifest.read_text());context=json.loads((run/'context.json').read_text())
if digest(a.manifest)!=context['manifest_sha256']:raise ValueError('manifest changed')
cases={c['id']:c for c in manifest['cases'] if c['suite']=='omnidocbench'}
gold=root/'gold/omni-selected.json'
if not all(digest(gold)==c['gold_sha256'] for c in cases.values()):raise ValueError('gold changed')
ledger=json.loads((run/'ledger.json').read_text());lookup={(r['case_id'],r['arm']):r for r in ledger}
selection=[c for c in cases.values() if c['id'] in context['case_ids']]
if len({x['image_id'] for x in selection})!=len(selection):raise ValueError('Duplicate image id')
records=[]
for arm in context['arms']:
 if not arm.startswith('docling-'):continue
 target=out/arm;target.mkdir()
 for c in selection:
  state=lookup.get((c['id'],arm),{'status':'missing'})
  raw=run/arm/c['id']/'worker/raw.json';pieces=[];error=None
  if state['status'] in {'succeeded','partial'} and raw.exists():
   try:
    doc=DoclingDocument.model_validate_json(raw.read_text(encoding='utf-8'))
    for item,_ in doc.iterate_items():
     label=getattr(item.label,'value',str(item.label));text=getattr(item,'text','') or ''
     if isinstance(item,TableItem): pieces.append(item.export_to_html(doc=doc))
     elif label=='formula' and text.strip():pieces.append('$$\n'+text+'\n$$')
     elif label in {'title','section_header'} and text.strip():pieces.append('## '+text)
     elif text.strip():pieces.append(text)
   except Exception as e:error=type(e).__name__+': '+str(e)
  pred=target/(Path(c['image_id']).stem+'.md');pred.write_text('\n\n'.join(pieces),encoding='utf-8')
  records.append({'case_id':c['id'],'arm':arm,'image_id':c['image_id'],'run_status':state['status'],
      'export_error':error,'prediction_sha256':digest(pred),'prediction_bytes':pred.stat().st_size})
(out/'export-ledger.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
(out/'metadata.json').write_text(json.dumps({'manifest_sha256':digest(a.manifest),'source_context_sha256':digest(run/'context.json'),
  'export_code_sha256':digest(Path(__file__)),'gold_sha256':digest(gold),'interpretation':'ordered Docling items with HTML tables; images omitted; failed/missing exported empty'},indent=2)+'\n')
print(json.dumps({'records':len(records),'errors':sum(r['export_error'] is not None for r in records),'output':str(out)}))
