import json
from copy import deepcopy

from a4b_verifier.client import OllamaConfig
from a4b_verifier.contracts import VerificationOutput
from a4b_verifier.service import verify
from agent_builder.nodes.verification import make_verification_node
from agent_builder.storage import Storage
from agent_builder.test_verifier_bridge import EXAMPLES, seed


def test_bad_quote_is_retried_once_and_never_auto_corrected(tmp_path, monkeypatch):
    import a4b_verifier.service as service

    store = Storage(tmp_path)
    state, draft = seed(store)
    state['draft'] = draft
    valid = json.loads((EXAMPLES / 'model_outputs/01_ready.json').read_text(encoding='utf-8'))
    invalid = deepcopy(valid)
    invalid['evidence'][0]['quote'] = '요청에 없는 인용'
    calls = []

    def always_bad(data, config, *, evidence_retry=False):
        calls.append(evidence_retry)
        return VerificationOutput.model_validate(invalid), {}

    monkeypatch.setattr(service, 'call_qwen', always_bad)
    result = make_verification_node(
        store, verifier=lambda data: verify(data, config=OllamaConfig()), mode='enforce'
    )(state)
    assert calls == [False, True]
    assert result['verification_route'] == 'manual_review'
    assert any(f['code'] == 'invalid_quote' for f in result['verification']['findings'])
    store.close()
