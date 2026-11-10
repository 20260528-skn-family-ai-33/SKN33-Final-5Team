"""Require an authenticated response with the pinned models before publishing ready."""
import json
import os
import sys
import urllib.request

from final_model_profile import required_models


try:
    expected = required_models()
    request = urllib.request.Request('http://127.0.0.1:11434/api/tags', headers={
        'Authorization': 'Bearer ' + os.environ['A4B_GATEWAY_TOKEN']})
    with urllib.request.urlopen(request, timeout=2) as response:
        models = {m['name'].removesuffix(':latest'): m['digest']
                  for m in json.load(response)['models']}
    assert all(models.get(tag) == digest for tag, digest in expected.items())
except Exception:
    sys.exit(1)
