"""Check the exact registered models and the GPU parser before opening the gateway."""
import json
import sys
import urllib.request
from final_model_profile import required_models


def get(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.load(response)


try:
    expected = required_models()
    models = {m['name'].removesuffix(':latest'): m['digest']
              for m in get('http://127.0.0.1:11435/api/tags')['models']}
    assert all(models.get(tag) == digest for tag, digest in expected.items())
    health = get('http://127.0.0.1:8100/health')
    assert health['ok'] and health['device'] == 'gpu:0' and health['pdf_jobs']
except Exception:
    sys.exit(1)
