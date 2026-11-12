"""Explicit open access must preserve the same resident-model request policy."""
import io
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import MagicMock

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts/runpod'
sys.path.insert(0, str(SCRIPTS))
from final_model_profile import SERVING_MODELS


@pytest.mark.parametrize('setting', [None, '1', '0', '', 'false'])
@pytest.mark.parametrize('authorization', ['', 'Bearer wrong', 'Bearer test-token'])
def test_auth_modes_preserve_request_and_response(monkeypatch, setting, authorization):
    monkeypatch.setenv('A4B_GATEWAY_TOKEN', 'test-token')
    if setting is None:
        monkeypatch.delenv('A4B_API_AUTH_REQUIRED', raising=False)
    else:
        monkeypatch.setenv('A4B_API_AUTH_REQUIRED', setting)
    gateway = runpy.run_path(str(SCRIPTS / 'runpod-ollama-proxy.py'))['Gateway']
    handler = gateway.__new__(gateway)
    payload = {'model': SERVING_MODELS[0], 'think': False,
               'messages': [{'role': 'user', 'content': 'synthetic'}],
               'options': {'temperature': 0.7, 'num_predict': 32}}
    body = json.dumps(payload).encode()
    handler.path = '/api/chat'
    handler.command = 'POST'
    handler.headers = {'Authorization': authorization, 'Content-Length': str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    for name in ('send_error', 'send_response', 'send_header', 'end_headers'):
        setattr(handler, name, MagicMock())
    response = MagicMock(status=200)
    response.getheader.return_value = 'application/json'
    response.read1.side_effect = [b'{"done":true}', b'']
    connection = MagicMock()
    connection.getresponse.return_value = response
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr('http.client.HTTPConnection', factory)
    handler.forward()
    if setting != '0' and authorization != 'Bearer test-token':
        handler.send_error.assert_called_once_with(401, 'Bearer authentication required')
        factory.assert_not_called()
        assert handler.rfile.tell() == 0
    else:
        handler.send_error.assert_not_called()
        sent = json.loads(connection.request.call_args.kwargs['body'])
        assert sent == {**payload, 'shift': False, 'truncate': False, 'keep_alive': -1,
                        'options': {**payload['options'], 'num_ctx': 32768}}
        assert handler.wfile.getvalue() == b'{"done":true}'
        connection.close.assert_called_once()
