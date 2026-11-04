"""Score authored HWP fixture field literals and table cells, without changing predictions."""
import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
corpus=a.manifest.resolve().parent;run=a.run.resolve();cases={x['id']:x for x in json.loads(a.manifest.read_text())['cases'] if x['suite']=='existing_hwp'}
ledger=json.loads((run/'ledger.json').read_text());scores=[]
def norm(x):return unicodedata.normalize('NFC',str(x))
for r in ledger:
 if r['case_id'] not in cases:continue
 c=cases[r['case_id']];path=corpus/c['gold'];hashvalue=hashlib.sha256(path.read_bytes()).hexdigest()
 if hashvalue!=c['gold_sha256']:raise ValueError('gold mismatch '+c['id'])
 g=json.loads(path.read_text());result=run/r['arm']/c['id']/'result.json';v={'case_id':c['id'],'arm':r['arm'],'status':r['status'],'family':c['family']}
 if r['status'] not in {'succeeded','partial'} or not result.exists():scores.append(v);continue
 pred=json.loads(result.read_text());text=norm(pred.get('text',''));fields={k:norm(s) in text for k,s in g['fields'].items() if s}
 v['fields_found']=sum(fields.values());v['fields_total']=len(fields);v['fields']=fields
 compact=lambda s: re.sub(r'\s+','',norm(s))
 compact_fields={k:compact(s) in compact(text) for k,s in g['fields'].items() if s}
 v['fields_found_without_whitespace']=sum(compact_fields.values());v['fields_without_whitespace']=compact_fields
 if g.get('tables'):
  target=g['tables'][0]; expected={(int(z['row'])-1,int(z['column'])-1):z for z in target['cells']}
  choices=[]
  for t in pred.get('tables',[]):
   observed={(int(cell['start_row_offset_idx']),int(cell['start_col_offset_idx'])):cell for cell in t.get('data',{}).get('cells',[]) if 'start_row_offset_idx' in cell and 'start_col_offset_idx' in cell}
   matches=sum(norm(observed[k].get('text',''))==norm(z['text']) for k,z in expected.items() if k in observed)
   merges=[(k,z) for k,z in expected.items() if z['row_span']>1 or z['col_span']>1]
   merged=sum(int(observed[k].get('row_span',1))==z['row_span'] and int(observed[k].get('col_span',1))==z['col_span'] for k,z in merges if k in observed)
   choices.append({'cells_exact':matches,'cells_total':len(expected),'merges_exact':merged,'merges_total':len(merges),'block_id':t.get('block_id')})
  v['table_candidates']=choices
  v['best_table']=max(choices,key=lambda x:x['cells_exact']) if choices else {'cells_exact':0,'cells_total':len(expected),'merges_exact':0,'merges_total':sum(z['row_span']>1 or z['col_span']>1 for z in expected.values()),'block_id':None}
 scores.append(v)
if a.output.exists():raise FileExistsError(a.output)
a.output.write_text(json.dumps({'interpretation':'authored fixtures; exact contiguous NFC field presence and separate whitespace-removed presence, not field binding; per-table best anchor cell match; no general accuracy; no independent human review',
 'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scores':scores},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'output':str(a.output),'records':len(scores)}))
