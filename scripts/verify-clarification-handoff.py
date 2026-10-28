"""Current frontend producer -> draft Python boundary. No model/DB/server."""
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Backend'))
from agent_builder.clarification_contract import ClarificationInput,ClarificationOutput,to_builder_state
from agent_builder.condition_readiness import input_issues

base=ROOT/'docs/clarification-handoff'
records=json.loads(subprocess.check_output(['node','--import','tsx','scripts/clarification-contract-fixtures.ts'],cwd=ROOT/'Frontend',text=True,timeout=60))
checks=[]
for record in records:
    payload=json.loads((base/'examples/01-ready.input.json').read_text())
    payload['resources']=[record['asset']]
    state=to_builder_state(payload,expected_session_id=payload['session_id'],expected_revision=payload['revision'])
    issues=input_issues(state)
    if record['name']=='normalizer-partial-pdf':assert issues
    else:assert not issues
    checks.append({'fixture':record['name'],'producer':record['producer'],'boundary_valid':True,'input_issues':issues,
                   'page_numbers_verified':False,'real_pdf_parsing':False})
index=json.loads((base/'examples/index.json').read_text())
for example in index['examples']:
    name=example['name']
    ClarificationInput.model_validate_json((base/f'examples/{name}.input.json').read_text())
    ClarificationOutput.model_validate_json((base/f'examples/{name}.output.json').read_text())
report={'status':'passed','frontend_producer_cases':len(checks),'published_example_pairs':len(index['examples']),
    'checks':checks,'real_model_calls':0,'database_writes':False,'service_endpoint_added':False,
    'human_contract_agreement':'pending','scope':'메타데이터 규격 연결 검사. 실제 파서·DB·모델 E2E 검증이 아님.'}
(base/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
