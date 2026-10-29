"""Pinned OmniDocBench v1_0 quick_match evaluation of Docling exports."""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--exports',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();corpus=a.manifest.resolve().parent;export=a.exports.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
public=ROOT/'data/bmt-public/OmniDocBench-v1_0';sys.path.insert(0,str(public))
from dataset.end2end_dataset import End2EndDataset
from metrics.cal_metric import call_Edit_dist, call_TEDS

def save(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=lambda x:float(x))+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
meta=json.loads((export/'metadata.json').read_text());manifest=json.loads(a.manifest.read_text())
if sha(a.manifest)!=meta['manifest_sha256']:raise ValueError('manifest mismatch')
cases={c['id']:c for c in manifest['cases'] if c['suite']=='omnidocbench'}
ledger=json.loads((export/'export-ledger.json').read_text());gold_all=json.loads((corpus/'gold/omni-selected.json').read_text());gold_by_image={Path(g['page_info']['image_path']).name:g for g in gold_all}
# Exercise official metric functions with exact and empty known answers before corpus scoring.
with tempfile.TemporaryDirectory() as d:
 old=Path.cwd();os.chdir(d);Path('result').mkdir()
 try:
  for pred,target in [('ABC',0.),('',1.)]:
   samples=[dict(gt='ABC',pred=pred,norm_gt='ABC',norm_pred=pred,img_id='fixture.jpg',gt_attribute=[])]
   _,agg=call_Edit_dist(samples).evaluate(save_name='known-answer');assert agg['Edit_dist']['ALL_page_avg']==target
  table='<html><body><table><tr><td>00123</td></tr></table></body></html>'
  for pred,target in [(table,1.),('',0.)]:
   samples=[dict(gt=table,pred=pred,norm_gt=table,norm_pred=pred,img_id='fixture.jpg',gt_idx=[0],gt_attribute=[])]
   _,agg=call_TEDS(samples).evaluate(save_name='known-answer');assert agg['TEDS']['all']==target
 finally:os.chdir(old)
results={}
for arm in sorted({r['arm'] for r in ledger}):
 rows=[r for r in ledger if r['arm']==arm];subset=[]
 for r in rows:
  case=cases[r['case_id']];pred=export/arm/(Path(case['image_id']).stem+'.md')
  if sha(pred)!=r['prediction_sha256']:raise ValueError('prediction hash mismatch')
  subset.append(gold_by_image[case['image_id']])
 goldpath=out/(arm+'-gold.json');save(goldpath,subset)
 cfg=dict(dataset=dict(ground_truth=dict(data_path=str(goldpath)),prediction=dict(data_path=str(export/arm)),match_method='quick_match'))
 old=Path.cwd();os.chdir(out);Path('result').mkdir(exist_ok=True)
 try:
  dataset=End2EndDataset(cfg);scores={}
  for cat in ['text_block','display_formula','table','reading_order']:
   samples=dataset.samples.get(cat,[])
   sequence=samples if isinstance(samples,list) else samples.samples
   scores[cat]={'sample_count':len(sequence)}
   if not sequence:continue
   for name,func in ([('TEDS',call_TEDS),('Edit_dist',call_Edit_dist)] if cat=='table' else [('Edit_dist',call_Edit_dist)]):
    evaluated,aggregate=func(samples).evaluate(save_name=arm+'_'+cat)
    scores[cat][name]=aggregate
    sequence=evaluated if isinstance(evaluated,list) else evaluated.samples
    save(out/(arm+'-'+cat+'-'+name+'-samples.json'),sequence)
  results[arm]={'scores':scores,'input_count':len(rows),'status_counts':{v:sum(r['run_status']==v for r in rows) for v in sorted({r['run_status'] for r in rows})},
   'export_errors':sum(bool(r['export_error']) for r in rows)}
 finally:os.chdir(old)
save(out/'summary.json',{'metric_version':'OmniDocBench v1_0 commit 337cc26965893db3ef53ddc119a6d6bb5bde096f','match':'quick_match',
 'input_scope':'fixed sample, not full benchmark; blank prediction for failed or missing runs', 'formula_cdm':'not measured',
 'export_code_sha256':meta['export_code_sha256'],'score_code_sha256':sha(Path(__file__)),'arms':results})
print(json.dumps({'output':str(out),'arms':list(results)},ensure_ascii=False))
