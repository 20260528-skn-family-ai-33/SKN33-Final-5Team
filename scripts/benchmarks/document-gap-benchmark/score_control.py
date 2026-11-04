"""Score fixed synthetic formula and table controls directly from Docling JSON."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree
from docling_core.types.doc import DoclingDocument,TableItem,PictureItem
p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
corpus=a.manifest.resolve().parent; run=a.run.resolve(); cases={c['id']:c for c in json.loads(a.manifest.read_text())['cases'] if c['suite']=='synthetic_control' and c['format']=='pdf'}
ledger=json.loads((run/'ledger.json').read_text());scores=[]
for entry in ledger:
 if entry['case_id'] not in cases or not entry['arm'].startswith('docling-'):continue
 case=cases[entry['case_id']];raw=run/entry['arm']/entry['case_id']/'worker/raw.json';gold_path=corpus/case['gold']
 if hashlib.sha256(gold_path.read_bytes()).hexdigest()!=case['gold_sha256']:raise ValueError('gold changed')
 gold=json.loads(gold_path.read_text());row={'case_id':case['id'],'arm':entry['arm'],'status':entry['status'],'family':case['family'],'variant':case['variant']}
 if entry['status'] not in {'succeeded','partial'} or not raw.is_file():scores.append(row);continue
 doc=DoclingDocument.model_validate_json(raw.read_text());items=list(doc.iterate_items())
 formula=[getattr(item,'text','') or '' for item,_ in items if getattr(item.label,'value','')=='formula']
 normalize=lambda x: re.sub(r'\s+','',x)
 row['formula_region_count']=len(formula);row['formula_nonempty']=sum(bool(x.strip()) for x in formula)
 row['formula_exact_after_whitespace_removal']=any(normalize(x)==normalize(gold['formula']['latex']) for x in formula)
 row['formula_predicted']=formula
 tables=[]
 for item,_ in items:
  if isinstance(item,TableItem):
   html=item.export_to_html(doc=doc); el=ElementTree.fromstring(html)
   rows=[[(''.join(cell.itertext()).strip(),cell.attrib.get('colspan','1')) for cell in tr] for tr in el.findall('.//tr')]
   tables.append({'html':html,'rows':rows})
 expected=[[(gold['merged_header'],'2')]]+ [[(cell,'1') for cell in line] for line in gold['table']]
 row['table_expected_structure_and_values']=any(t['rows']==expected for t in tables)
 row['tables_detected']=len(tables);row['pictures_detected']=sum(isinstance(item,PictureItem) for item,_ in items)
 row['chart_values_extracted']='not_evaluated'
 scores.append(row)
if a.output.exists():raise FileExistsError(a.output)
a.output.write_text(json.dumps({'description':'fixed authored controls; exact formula after whitespace removal, exact HTML cell grid/spans; not independent or general accuracy',
  'score_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scores':scores},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'records':len(scores),'output':str(a.output)}))
