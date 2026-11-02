"""Inject named failures into real routing runs; remaining engines run normally."""
import argparse
import json
import sys
import time
import traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Backend'))
from document_analysis.adapters import LocalEngines
from document_analysis.contracts import AnalysisError
from document_analysis.pipeline import run
p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--scenario',choices=['mineru','mineru-and-docling','layout','paddleocr'],required=True)
a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
fails={'mineru':{'mineru'},'mineru-and-docling':{'mineru','docling'},'layout':{'layout'},'paddleocr':{'paddleocr'}}[a.scenario]
class FaultEngine(LocalEngines):
    def run(self, engine, *, source=None, sources=None, scope=None, phase='primary'):
        if engine in fails:
            self.attempts.append({'id':'attempt-'+str(len(self.attempts)+1),'phase':phase,'processor':engine,
                'scope':scope,'status':'failed','elapsed_seconds':0,'error_code':'injected_failure'})
            raise AnalysisError('injected_failure','injected for fault verification')
        return super().run(engine,source=source,sources=sources,scope=scope,phase=phase)
started=time.perf_counter(); engines=FaultEngine(out/'work')
try:
    payload=run(a.input,out/'work',engines=engines)
    (out/'result.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    analysis=payload['document_analysis']; triggered=any(r.get('error_code')=='injected_failure' for r in analysis['routes'])
    status={'scenario':a.scenario,'fault_injected':triggered,'parser_status':payload['parser_status'],
            'review_required':analysis['review_required'],'review_reasons':analysis['review_reasons'],
            'fallback_processor':analysis.get('fallback_processor'),'elapsed_seconds':time.perf_counter()-started,
            'interpretation':'failure injection test, not real engine reliability or accuracy'}
    expected_fallback={'mineru':'docling','mineru-and-docling':'native'}.get(a.scenario)
    fallback_ok=expected_fallback is None or analysis.get('fallback_processor')==expected_fallback
    status['expected_fallback']=expected_fallback
    status['fallback_matches']=fallback_ok
    status['check']='not_exercised' if not triggered else 'pass' if payload['parser_status']=='partial' and analysis['review_required'] and fallback_ok else 'fail'
except Exception as e:
    status={'scenario':a.scenario,'check':'error','error_type':type(e).__name__,'message':str(e),
        'elapsed_seconds':time.perf_counter()-started,'traceback':traceback.format_exc(),
        'interpretation':'failure injection test, not real engine reliability or accuracy'}
(out/'probe.json').write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(status,ensure_ascii=False))
