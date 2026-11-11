"""Required serving models; registration receipts remain immutable provenance."""
import json
import re
from pathlib import Path

SERVING_MODELS = (
    'a4b-qwen35-9b-previous-pilot32k',
    'a4b-qwen35-9b-final664-pilot32k',
)


def required_models(receipt=Path('/workspace/a4b/receipts/models-registered.json')):
    registered = json.loads(Path(receipt).read_text())
    expected = {}
    for name in SERVING_MODELS:
        digest = registered.get(name)
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError(f'Missing or invalid registered digest: {name}')
        expected[name] = digest
    return expected
