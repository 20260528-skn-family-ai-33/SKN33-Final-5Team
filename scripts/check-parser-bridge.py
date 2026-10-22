"""Synthetic PDF -> real parser -> upload DB -> verifier document contract."""
import argparse,base64,hashlib,json,time
from pathlib import Path
from urllib.request import Request,urlopen
from agent_builder.nodes.verification import resource_document
from a4b_verifier.contracts import Document

p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:3043');args=p.parse_args()
root=Path(__file__).resolve().parents[1]
def call(path,data=None):
 body=json.dumps(data).encode() if data is not None else None
 with urlopen(Request(args.url+path,data=body,headers={'Content-Type':'application/json'}),timeout=360) as response:return json.load(response)
report={'scope':'synthetic PDF actual local integration; no model generation, AWS or external document uploads','started_at':time.time()}
try:
 connection=call('/api/agent-builder/connection')['connection']
 session=call('/api/agent-builder/sessions',{'model':connection['model'],'credentialId':connection['credential']['id']})
 pdf=(root/'output/parser-smoke-input.pdf').read_bytes()
 result=call('/api/agent-builder/upload',{'id':session['id'],'revision':session['revision'],'fileName':'parser-smoke.pdf','data':base64.b64encode(pdf).decode()})
 asset=result['resources'][0]
 # Session state reload traverses the actual Python worker and SQLite repository.
 reloaded=call('/api/agent-builder/session?id='+session['id'])
 restored=reloaded['resources'][0]
 document=Document.model_validate(resource_document(restored))
 partial=Document.model_validate(resource_document({**restored,'parser_status':'partial'}))
 report.update(parser_status=restored['parser_status'],pages=restored['page_count'],tables=len(restored['tables']),
   pipeline=restored['parser_metadata']['parser_pipeline_version'],source_hash_matches=restored['hash']==hashlib.sha256(pdf).hexdigest(),
   original_path=restored['original_path'],metadata_survived_database=restored['parser_metadata']==asset['parser_metadata'],
   table_html_preserved=all('html' in t for t in restored['tables']),verifier_status=document.parse_status,
   partial_not_promoted=partial.parse_status=='partial',no_agent_saved=reloaded.get('saved_agent_id') is None,
   expected_text_present='Invoice total 1500' in restored['text'])
 report['passed']=all(report[k] for k in ['source_hash_matches','metadata_survived_database','table_html_preserved','partial_not_promoted','no_agent_saved','expected_text_present']) and report['tables']>0
except Exception as e:report.update(passed=False,error_type=type(e).__name__,error=str(e)[:500],error_body=e.read().decode()[:1000] if hasattr(e,'read') else None)
(root/'output/parser-app-bridge-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
