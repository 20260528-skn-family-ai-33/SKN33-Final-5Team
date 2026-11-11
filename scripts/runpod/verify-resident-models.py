"""Check alternating native/OpenAI requests through the authenticated public URL.

Run inside the Pod. Never print credentials, prompts, or generated text.
"""
import argparse
import datetime
import json
from pathlib import Path
import time
import urllib.request

from final_model_profile import required_models

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
token = Path('/run/a4b-auth/gateway-token').read_text().strip()
expected = required_models()
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)


def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:11435/api/ps', timeout=10) as response:
        models = json.load(response)['models']
    actual = {m['name'].removesuffix(':latest'): m for m in models}
    assert set(actual) == set(expected), 'Both resident models must be ready'
    for name, m in actual.items():
        assert m['digest'] == expected[name], 'Model digest changed'
        assert m['context_length'] == 32768 and m['size_vram'] >= m['size'] * .99
    runners = {}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = p.joinpath('cmdline').read_bytes().decode().split('\0')
            if cmd and Path(cmd[0]).name == 'llama-server' and '--model' in cmd:
                runners[Path(cmd[cmd.index('--model') + 1]).name] = int(p.name)
                assert '--context-shift' not in cmd, 'Runner unexpectedly enables context shift'
        except (OSError, UnicodeError):
            continue
    assert len(runners) == 2, 'Expected two stable runners'
    return {'runners': runners, 'models': [{k: m.get(k) for k in
            ['name', 'digest', 'context_length', 'size', 'size_vram']} for m in models]}


before = snapshot()
log = Path('/workspace/a4b/logs/ollama.log')
offset = log.stat().st_size
result = {'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'base_url': args.base_url, 'before': before, 'cases': [], 'passed': False}


def save():
    output.write_text(json.dumps(result, indent=2) + '\n')


save()
try:
    # Alternate model on EVERY request, including transitions between API styles.
    for kind in ['plain', 'schema', 'stream']:
        for path in ['/api/chat', '/v1/chat/completions']:
            for model in expected:
                stream = kind == 'stream'
                schema = {'type': 'object', 'properties': {'status': {'type': 'string', 'enum': ['ok']}},
                          'required': ['status'], 'additionalProperties': False}
                prompt = 'Return exactly the JSON object {"status":"ok"}.' if kind == 'schema' else 'Reply with the single word OK.'
                payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                           'stream': stream}
                if path == '/api/chat':
                    payload.update(shift=False, think=False, truncate=False, keep_alive='5m',
                                   options={'num_ctx': 32768, 'num_predict': 32, 'temperature': 0, 'seed': 42})
                    if kind == 'schema':
                        payload['format'] = schema
                else:
                    payload.update(max_tokens=32, temperature=0, seed=42, reasoning_effort='none')
                    if stream:
                        payload['stream_options'] = {'include_usage': True}
                    if kind == 'schema':
                        payload['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'health', 'schema': schema}}
                request = urllib.request.Request(args.base_url.rstrip('/') + path,
                    data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + token, 'User-Agent': 'A4B/0.1'})
                started = time.monotonic()
                parts, done, metrics = [], False, {}
                with urllib.request.urlopen(request, timeout=90) as response:
                    status = response.status
                    if stream:
                        for line in response:
                            line = line.strip()
                            if not line:
                                continue
                            if path.startswith('/v1'):
                                if not line.startswith(b'data: '):
                                    continue
                                line = line[6:]
                                if line == b'[DONE]':
                                    done = True
                                    continue
                            item = json.loads(line)
                            assert not item.get('error'), 'Stream returned an error'
                            if path == '/api/chat':
                                parts.append(item.get('message', {}).get('content', ''))
                                done |= bool(item.get('done'))
                                if item.get('done'):
                                    metrics = {k: item[k] for k in ['load_duration', 'eval_count'] if k in item}
                            else:
                                for choice in item.get('choices', []):
                                    parts.append(choice.get('delta', {}).get('content') or '')
                    else:
                        item = json.load(response)
                        assert not item.get('error'), 'Response returned an error'
                        if path == '/api/chat':
                            parts = [item.get('message', {}).get('content', '')]
                            done = item.get('done') is True
                            metrics = {k: item[k] for k in ['load_duration', 'eval_count'] if k in item}
                        else:
                            parts = [item['choices'][0]['message'].get('content') or '']
                            done = item['choices'][0].get('finish_reason') is not None
                elapsed = time.monotonic() - started
                content = ''.join(parts).strip()
                assert status == 200 and done and content, 'Incomplete or empty response'
                if kind == 'schema':
                    assert json.loads(content) == {'status': 'ok'}, 'Schema response failed'
                after = snapshot()
                assert after['runners'] == before['runners'], 'A runner was reloaded'
                case = {'model': model, 'path': path, 'kind': kind, 'http': status,
                        'seconds': round(elapsed, 3), 'completed': done,
                        'nonempty': bool(content), 'same_runners': True, **metrics}
                result['cases'].append(case)
                save()
                print(json.dumps(case), flush=True)
    with log.open('rb') as file:
        file.seek(offset)
        new_log = file.read().decode(errors='replace')
    result['runner_start_events'] = new_log.count('starting llama-server')
    result['load_failed_events'] = new_log.count('msg="Load failed"')
    assert result['runner_start_events'] == result['load_failed_events'] == 0
    result['after'] = snapshot()
    result['passed'] = True
except Exception as error:
    result['failure_type'] = type(error).__name__
    raise
finally:
    save()
print(json.dumps({'passed': True, 'cases': len(result['cases']), 'output': str(output)}), flush=True)
