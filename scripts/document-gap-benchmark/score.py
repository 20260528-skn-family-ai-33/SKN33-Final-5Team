"""Score saved results without running parsers; emit a separate manual review template."""
import argparse
import csv
import hashlib
import json
import statistics
import sys
import unicodedata
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Backend'))
from document_analysis.contracts import validate
p=argparse.ArgumentParser()
p.add_argument('--manifest',type=Path,required=True)
p.add_argument('--run',type=Path,required=True)
a=p.parse_args(); corpus=a.manifest.resolve().parent; run=a.run.resolve()
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
context=json.loads((run/'context.json').read_text())
if sha(a.manifest)!=context['manifest_sha256']: raise ValueError('manifest changed after inference')
cases={c['id']:c for c in json.loads(a.manifest.read_text())['cases']}
ledger=json.loads((run/'ledger.json').read_text())
expected={(case_id,arm) for case_id in context['case_ids'] for arm in context['arms']}
observed=[(r['case_id'],r['arm']) for r in ledger]
if len(observed)!=len(set(observed)): raise ValueError('Duplicate run records')
if set(observed)-expected: raise ValueError('Unexpected run records')
output=run/'scoring'
output.mkdir(exist_ok=False)
schema=json.loads((ROOT/'Backend/document_analysis/result.schema.json').read_text())
try:
    import jsonschema
except ImportError:
    jsonschema=None

def norm(s): return ' '.join(unicodedata.normalize('NFC',s).split())
scores=[]; review=[]
for item in ledger:
    case=cases[item['case_id']]; folder=run/item['arm']/case['id']
    record={k:item.get(k) for k in ['case_id','arm','status','parser_status','elapsed_seconds']}
    record.update(suite=case['suite'],family=case['family'],variant=case['variant'])
    gold_path=(corpus/case['gold']).resolve()
    if not gold_path.is_relative_to(corpus) or sha(gold_path)!=case['gold_sha256']: raise ValueError('Gold changed: '+case['id'])
    gold=json.loads(gold_path.read_text())
    text=''; result_file=folder/'result.json'; markdown_file=folder/'worker'/'prediction.md'
    if result_file.exists():
        payload=json.loads(result_file.read_text()); text=payload.get('text','')
        if 'document_analysis' in payload:
            data=payload['document_analysis']
            record['source_hash_match']=data.get('source_sha256')==case['sha256']
            try:
                validate(data); record['contract_integrity']='pass'
            except Exception as e: record['contract_integrity']='fail';record['contract_error']=str(e)
            if jsonschema is None: record['json_schema']='not_checked_dependency_missing'
            else:
                try: jsonschema.validate(data,schema);record['json_schema']='pass'
                except jsonschema.ValidationError as e: record['json_schema']='fail';record['schema_error']=e.message
            record['review_required']=data.get('review_required')
            record['route_statuses']=[{'phase':r.get('phase'),'processor':r.get('processor'),'status':r.get('status'),'elapsed_seconds':r.get('elapsed_seconds')} for r in data.get('routes',[])]
    elif markdown_file.exists(): text=markdown_file.read_text(encoding='utf-8')
    if case['suite']=='synthetic_control':
        anchors=gold['anchors']; target=norm(text)
        present=[norm(x) in target for x in anchors]
        # This checks literal positions only; missing or repeated anchors make order ambiguous.
        counts=[target.count(norm(x)) for x in anchors]
        positions=[target.find(norm(x)) for x in anchors]
        record['anchor_presence']={'found':sum(present),'total':len(anchors)}
        record['anchor_order']={'comparable':all(v==1 for v in counts),'correct': positions==sorted(positions) if all(v==1 for v in counts) else None}
        checks=['formula_detection','formula_semantics','table_values_positions','table_merged_header','chart_detection','chart_axes_labels','chart_values','heading_hierarchy','reading_order','provenance']
    elif case['suite']=='failure_input':
        checks=['failure_handling']
        record['failure_reported']=item['status']=='failed'
    elif case['suite']=='existing_hwp':
        checks=['important_fields','table_structure','reading_order','conversion_preservation','provenance']
    else:
        checks=['important_fields','formula_detection','formula_semantics','table_values_positions','table_merged_header',
                'chart_detection','chart_axes_labels','chart_values','heading_hierarchy','reading_order','provenance']
    if item['status']=='not_applicable':
        record['anchor_presence']=None
    scores.append(record)
    if item['status']!='not_applicable':
        for check in checks:
            review.append({'case_id':case['id'],'arm':item['arm'],'status':item['status'],'check':check,
                'decision':'PENDING','evidence_path':'','reason':'','reviewer':''})
(output/'scores.json').write_text(json.dumps(scores,ensure_ascii=False,indent=2)+'\n')
with (output/'manual-review.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(review[0]) if review else ['case_id','arm','status','check','decision','evidence_path','reason','reviewer'])
    writer.writeheader();writer.writerows(review)
summary={}
for arm in sorted({r['arm'] for r in scores}):
    group=[r for r in scores if r['arm']==arm]; measured=[r for r in group if r['elapsed_seconds'] is not None]
    summary[arm]={'total':len(group),'succeeded':sum(r['status']=='succeeded' for r in group),
        'partial':sum(r['status']=='partial' for r in group),'failed':sum(r['status'] in {'failed','timeout'} for r in group),
        'not_applicable':sum(r['status']=='not_applicable' for r in group),
        'elapsed_median_all_attempts':statistics.median(r['elapsed_seconds'] for r in measured) if measured else None,
        'schema_dependency_available':jsonschema is not None}
(output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
completion={'planned':len(expected),'observed':len(observed),'missing':[{'case_id':c,'arm':arm} for c,arm in sorted(expected-set(observed))],
            'finished_marker_exists':(run/'finished.json').exists()}
(output/'completion.json').write_text(json.dumps(completion,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'output':str(output),'records':len(scores),'review_rows':len(review),'completion':completion,'summary':summary},ensure_ascii=False))
