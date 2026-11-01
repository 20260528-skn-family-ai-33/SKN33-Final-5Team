"""Small integration tests using fake workers only; no model inference."""
import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
class BenchmarkTests(unittest.TestCase):
    def test_fake_success_failed_and_manual_pending(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);corpus=root/'corpus';corpus.mkdir()
            inp=corpus/'test.pdf';inp.write_bytes(b'fixed input')
            gold=corpus/'gold.json';gold.write_text(json.dumps({'anchors':['Title','Section']}))
            digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            case={'id':'fake','format':'pdf','input':'test.pdf','sha256':digest(inp),'gold':'gold.json','gold_sha256':digest(gold),
                  'suite':'synthetic_control','family':'fake','variant':'native'}
            manifest=corpus/'manifest.json';manifest.write_text(json.dumps({'cases':[case]}))
            worker=root/'fake-python'
            worker.write_text('#!'+sys.executable+'\n'+'''import json, pathlib, sys
args=sys.argv[1:]
if args == ['--version']:
 print('fake worker');raise SystemExit(0)
if 'analyze_document.py' in args[0]:
 source=pathlib.Path(args[1]); output=pathlib.Path(args[2])
 import hashlib
 payload={'text':'Title Section','parser_status':'succeeded','document_analysis':{'contract_version':'a4b.document_analysis.v1','document_id':hashlib.sha256(source.read_bytes()).hexdigest(),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_format':'pdf','primary_processor':'mineru','status':'succeeded','warnings':[],'review_required':False,'review_reasons':[],'units':[],'blocks':[],'routes':[],'routing_decisions':[],'coverage':{}}}
 output.write_text(json.dumps(payload))
else:
 raise SystemExit(2)
''')
            worker.chmod(0o755);out=root/'run'
            proc=subprocess.run([sys.executable,str(HERE/'run.py'),'--manifest',str(manifest),'--output',str(out),
                '--arms','service-default','docling-base','--service-python',str(worker),'--docling-python',str(worker)],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            ledger=json.loads((out/'ledger.json').read_text());self.assertEqual([r['status'] for r in ledger],['succeeded','failed'])
            proc=subprocess.run([sys.executable,str(HERE/'score.py'),'--manifest',str(manifest),'--run',str(out)],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            scores=json.loads((out/'scoring/scores.json').read_text())
            self.assertEqual(scores[0]['anchor_presence'],{'found':2,'total':2})
            self.assertTrue(scores[0]['anchor_order']['correct'])
            self.assertEqual(scores[0]['contract_integrity'],'pass')
            self.assertEqual(scores[1]['status'],'failed')
            with (out/'scoring/manual-review.csv').open(encoding='utf-8-sig',newline='') as f: reviews=list(csv.DictReader(f))
            self.assertTrue(reviews);self.assertEqual({r['decision'] for r in reviews},{'PENDING'})
            report=out/'review.json'
            proc=subprocess.run([sys.executable,str(HERE/'summarize_review.py'),str(out/'scoring/manual-review.csv'),'--output',str(report)],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertTrue(all(x['observed_pass_rate'] is None and x['pending']==1 for x in json.loads(report.read_text())))
            comparison=out/'comparison.json'
            proc=subprocess.run([sys.executable,str(HERE/'compare.py'),'--scores',str(out/'scoring/scores.json'),
                '--a','service-default','--b','docling-base','--output',str(comparison)],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            paired=json.loads(comparison.read_text())
            self.assertEqual(paired['anchor_comparable'],0)
            self.assertEqual(paired['pairs'][0]['b_status'],'failed')
            inp.write_bytes(b'changed')
            bad=subprocess.run([sys.executable,str(HERE/'run.py'),'--manifest',str(manifest),'--output',str(root/'bad'),
                '--arms','service-default','--service-python',str(worker)],capture_output=True,text=True)
            self.assertNotEqual(bad.returncode,0);self.assertIn('Input hash mismatch',bad.stderr)
if __name__=='__main__':unittest.main()
