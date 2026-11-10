import json
from copy import deepcopy

import pytest

from verification.workflow import client
from verification.workflow.contracts import VerificationInput
from verification.workflow.validation import output_checks
from tests.verification.test_verifier_bridge import EXAMPLES


def test_compact_grammar_keeps_all_sources_and_runtime_quote_validation(monkeypatch):
    data=VerificationInput.model_validate_json((EXAMPLES/'inputs/01_ready.json').read_text())
    # Repeating an actual source is a context-size fixture, not a model answer.
    data=data.model_copy(deep=True)
    data.documents[0].chunks[0].text+='\n'+('정책 원문을 유지합니다. '*35)
    before=data.model_dump(mode='json');calls=[]
    out=json.loads((EXAMPLES/'model_outputs/01_ready.json').read_text())
    def http(config,path,body):
        calls.append(body)
        return {'done':True,'done_reason':'stop','message':{'content':json.dumps(out)}}
    monkeypatch.setattr(client,'http_json',http)
    # Use a limit between the full and compact grammars to exercise fallback.
    raw=data.model_dump(mode='json')
    used={n.tool for draft in (data.previous_draft,data.candidate_draft) if draft for n in draft.workflow.nodes}
    raw['tools']=[{k:v for k,v in t.items() if v not in ({},[])} if t['id'] not in used else t for t in raw['tools']]
    content=json.dumps({'input':raw,'evidence_bank':client.evidence_bank(data)},ensure_ascii=False,separators=(',',':'))
    base=len((client.SYSTEM+content).encode())+4096+2048
    size=lambda schema:len(json.dumps(schema,ensure_ascii=False,separators=(',',':')).encode())
    limit=base+size(client.grounded_output_schema(data,compact=True))+1
    assert limit<base+size(client.grounded_output_schema(data))
    result,_=client.call_qwen(data,client.OllamaConfig(num_ctx=limit))
    body=calls[0];wire=json.loads(body['messages'][1]['content'])
    assert wire['evidence_bank']==client.evidence_bank(data)
    assert wire['input']['documents']==before['documents']
    assert data.model_dump(mode='json')==before
    assert body['truncate'] is body['shift'] is False
    assert body['format']['$defs']['EvidenceRef']['properties']['evidence_id']['enum']
    result.evidence[0].quote='원문에 없는 인용'
    assert any(f.code=='invalid_quote' for f in output_checks(data,result))
    calls.clear()
    with pytest.raises(RuntimeError,match='context_budget_exceeded'):
        client.call_qwen(data,client.OllamaConfig(num_ctx=4096))
    assert not calls
