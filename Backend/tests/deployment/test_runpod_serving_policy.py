"""No user content/schema/sampling changes when stabilizing resident runners."""
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/runpod'))
from serving_request_policy import ensure_shift_support, pin_serving_request
from final_model_profile import SERVING_MODELS


class ServingPolicyTests(unittest.TestCase):
    def test_stock_runtime_cannot_silently_ignore_openai_shift(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"version":"0.40.13"}')):
            with self.assertRaises(RuntimeError):
                ensure_shift_support()

    def test_patched_runtime_accepted(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"version":"0.40.13-a4b-shift1"}')):
            ensure_shift_support()

    def apply(self, path, payload):
        return json.loads(pin_serving_request(path, json.dumps(payload).encode()))

    def test_native_preserves_evaluation_semantics(self):
        for model in SERVING_MODELS:
            payload = dict(model=model, shift=False, truncate=False, think=False,
                           stream=True, keep_alive='5m',
                           messages=[{'role': 'user', 'content': '한글 synthetic input'}],
                           format={'type': 'object', 'properties': {'ok': {'type': 'boolean'}}},
                           options={'num_ctx': 32768, 'num_predict': 32, 'seed': 42, 'temperature': 0})
            original = copy.deepcopy(payload)
            result = self.apply('/api/chat', payload)
            self.assertEqual(payload, original)
            original['keep_alive'] = -1
            self.assertEqual(result, original)

    def test_openai_preserves_tools_schema_and_streaming(self):
        payload = dict(model=SERVING_MODELS[1] + ':latest', stream=True,
                       stream_options={'include_usage': True}, max_tokens=128,
                       messages=[{'role': 'user', 'content': 'synthetic'}],
                       tools=[{'type': 'function', 'function': {'name': 'lookup'}}],
                       response_format={'type': 'json_schema', 'json_schema': {'schema': {'type': 'object'}}})
        result = self.apply('/v1/chat/completions', payload)
        self.assertEqual(result, {**payload, 'shift': False, 'truncate': False, 'keep_alive': -1})

    def test_defaults_match_warmup(self):
        result = self.apply('/api/chat?test=1', {'model': SERVING_MODELS[0]})
        self.assertIs(result['shift'], False)
        self.assertEqual(result['options']['num_ctx'], 32768)
        self.assertEqual(result['keep_alive'], -1)

    def test_conflicting_launch_config_rejected_before_upstream(self):
        for changes in [{'shift': True}, {'shift': 'false'}, {'truncate': True}, {'options': {'num_ctx': 16384}},
                        {'options': {'use_mmap': True}}, {'options': {'num_batch': 256}},
                        {'options': {'num_thread': 58}}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.apply('/api/chat', {'model': SERVING_MODELS[0], **changes})

    def test_other_models_and_routes_are_byte_preserved(self):
        body = b'{ "model":"qwen3.5:4b", "shift":true, "keep_alive":0 }'
        for path in ['/api/chat', '/v1/chat/completions', '/api/tags']:
            self.assertEqual(pin_serving_request(path, body), body)

    def test_invalid_body_rejected(self):
        for body in [b'bad json', b'[]', b'null']:
            with self.subTest(body=body), self.assertRaises(ValueError):
                pin_serving_request('/api/chat', body)


if __name__ == '__main__':
    unittest.main()
