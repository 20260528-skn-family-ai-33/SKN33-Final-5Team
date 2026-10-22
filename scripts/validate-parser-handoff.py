"""Validate only artifacts actually included in the handoff; no invented sources."""
import importlib.util,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
base=root/'Backend/integrations/parser_handoff/db-contract-v0.1'
spec=importlib.util.spec_from_file_location('parser_contract_checks',base/'validate_examples.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
schema=json.loads((base/'parser-result.schema.json').read_text())
records=[]
for name in ['samsung-p67.parser-result.json','failed.parser-result.example.json']:
 data=json.loads((base/name).read_text());module.validate_payload(data,schema)
 records.append({'file':name,'outcome':data['processing']['outcome'],'whole_document_complete':data['processing']['whole_document_complete'],'passed':True})
missing=[name for name in ['sources/docling-p67-document.json','sources/multilevel-reference.json','chunk-example.json','index.html'] if not (base/name).exists()]
report={'supplied_examples_passed':len(records),'records':records,'missing_optional_reference_artifacts':missing,'full_reference_suite_run':False,'database_or_network_writes':False}
(root/'output/parser-contract-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
