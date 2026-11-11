"""Evaluate the real semantic extractor and source adapter with synthetic requests.

No app configuration, credentials, account data, or workflow execution is read.
Example (from the repository root, with the backend dependencies installed):
  python scripts/verify/verify-request-meaning-live.py --model qwen3.5:4b --output data/request-meaning-4b.json
Use --model with the exact registered 9B name to compare the same corpus later.
On Windows, install tzdata or point PYTHONTZPATH to an IANA zoneinfo directory.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
import urllib.request
import urllib.error


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend'))

from agent_builder.design.request_meaning import MeaningReview, REVIEW_SYSTEM, RequestMeaning, SYSTEM, extract_meaning, gmail_selection
from agent_builder.design.temporal_context import TEMPORAL_INSTRUCTIONS, temporal_context
from agent_builder.design.gmail_summary import gmail_summary_reply


class OllamaTransport:
    def __init__(self, base_url, model, timeout, num_ctx=8192, num_predict=2048):
        self.base_url, self.model, self.timeout = base_url.rstrip('/'), model, timeout
        self.num_ctx, self.num_predict = num_ctx, num_predict
        self.schema = None
        self.calls = []

    def invoke(self, messages):
        body = dict(model=self.model, messages=messages, stream=False, think=False,
                    format=self.schema,
                    options=dict(temperature=0, num_ctx=self.num_ctx, num_predict=self.num_predict))
        request = urllib.request.Request(self.base_url + '/api/chat',
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'User-Agent': 'A4B-Synthetic-Eval/1.0'})
        start = time.monotonic()
        record = {'requested_model': self.model, 'schema_title': (self.schema or {}).get('title'),
                  'schema_sha256': hashlib.sha256(json.dumps(self.schema, sort_keys=True).encode()).hexdigest()}
        self.calls.append(record)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except Exception as error:
            record.update(error_type=type(error).__name__, wall_seconds=round(time.monotonic() - start, 3))
            if isinstance(error, urllib.error.HTTPError):
                try:
                    detail = json.loads(error.read(4096))
                    record['error_detail'] = str(detail.get('error', ''))[:1000]
                except (ValueError, OSError):
                    pass
            raise
        record.update({key: result.get(key) for key in (
            'model', 'done_reason', 'total_duration', 'load_duration',
            'prompt_eval_count', 'prompt_eval_duration', 'eval_count', 'eval_duration')})
        self.calls[-1]['wall_seconds'] = round(time.monotonic() - start, 3)
        self.calls[-1]['response_content'] = result['message']['content']
        return {'content': result['message']['content'], 'metrics': {'done_reason': result.get('done_reason')}}


def compare(actual, expected, path='meaning'):
    """Compare semantic subsets; evidence wording is intentionally not scored."""
    failures = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f'{path}: expected object, got {actual!r}']
        for key, value in expected.items():
            failures.extend(compare(actual.get(key), value, f'{path}.{key}'))
    elif isinstance(expected, list):
        # Actions and filters are sets; the validator normalizes their order.
        if sorted(actual or []) != sorted(expected):
            failures.append(f'{path}: expected {expected!r}, got {actual!r}')
    elif actual != expected:
        failures.append(f'{path}: expected {expected!r}, got {actual!r}')
    return failures


def evaluate(case, transport, reference):
    state = dict(messages=[dict(role='user', content=text) for text in case['messages']],
                 resources=[], user_timezone='Asia/Seoul')
    state['temporal_context'] = temporal_context(state, now=reference)
    start, before = time.monotonic(), len(transport.calls)
    result = dict(id=case['id'], group=case.get('group'), messages=case['messages'],
                  valid_json_and_schema=False)
    try:
        state.update(extract_meaning(state, transport))
        result['valid_json_and_schema'] = True
        meaning = state['request_meaning']
        adapter = gmail_selection(state)
        reply = gmail_summary_reply(state)
        workflow = reply['draft']['workflow'] if reply else None
        expected, failures = case['expected'], []
        tasks = meaning['tasks']
        if len(tasks) != expected['task_count']:
            failures.append(f'tasks: expected {expected["task_count"]}, got {len(tasks)}')
        gmail = next((task for task in tasks if task['source'] == 'gmail'), None)
        failures.extend(compare(gmail, expected['gmail_task']))
        if expected.get('other_requirements_nonempty') and not (gmail or {}).get('other_requirements'):
            failures.append('gmail other_requirements lost the extra requested work')
        if expected.get('unresolved_empty', True) and meaning['unresolved']:
            failures.append(f'unexpected clarification: {meaning["unresolved"]!r}')
        if bool(adapter) != expected['adapter_allowed']:
            failures.append(f'adapter_allowed: expected {expected["adapter_allowed"]}, got {bool(adapter)}')
        if bool(workflow) != expected['adapter_allowed']:
            failures.append(f'compiled_workflow: expected {expected["adapter_allowed"]}, got {bool(workflow)}')
        if expected.get('adapter'):
            failures.extend(compare(adapter, expected['adapter'], 'adapter'))
        if case.get('group') == 'recent5':
            baseline = gmail_summary_reply(dict(messages=[dict(role='user', content='gmail 에서 최근 5개 메일을 요약 정리해줘')], resources=[]))
            identical = bool(workflow and baseline and workflow == baseline['draft']['workflow'])
            result['same_workflow_as_registered_recipe'] = identical
            if not identical:
                failures.append('equivalent recent5 request did not compile to the registered recipe workflow')
        result.update(valid_json_and_schema=True, passed=not failures, failures=failures,
                      meaning=meaning, review=state.get('request_meaning_review'), adapter=adapter,
                      workflow=workflow)
    except Exception as error:
        # Error messages are bounded; requests are synthetic and contain no secrets.
        result.update(passed=False,
                      failures=[f'{type(error).__name__}: {str(error)[:500]}'])
        cause, is_transport = error, False
        while cause is not None:
            is_transport = is_transport or isinstance(cause, (OSError, urllib.error.URLError))
            cause = cause.__cause__
        result['transport_error'] = is_transport
    result.update(wall_seconds=round(time.monotonic() - start, 3),
                  model_calls=transport.calls[before:])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:11434',
                        help='Explicit Ollama endpoint. No .env or credentials are read.')
    parser.add_argument('--corpus', type=Path, default=ROOT / 'Backend/tests/fixtures/request-meaning/paraphrases.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--num-ctx', type=int, default=8192)
    parser.add_argument('--num-predict', type=int, default=2048)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--case', action='append', dest='case_ids', help='Run only named cases; repeatable.')
    parser.add_argument('--max-transport-errors', type=int, default=1,
                        help='Stop after this many connection/time-out failures; default 1.')
    args = parser.parse_args()
    if args.timeout <= 0 or args.num_ctx < 1024 or args.num_predict < 1 or args.max_transport_errors < 1:
        parser.error('timeout, num_predict and max-transport-errors must be positive; num-ctx must be at least 1024.')
    corpus = json.loads(args.corpus.read_text(encoding='utf-8'))
    reference = datetime.fromisoformat(corpus['reference_time'].replace('Z', '+00:00'))
    cases = [case for case in corpus['cases'] if not args.case_ids or case['id'] in args.case_ids]
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        parser.error('No corpus cases selected.')
    transport = OllamaTransport(args.base_url, args.model, args.timeout, args.num_ctx, args.num_predict)
    output = dict(model=args.model, endpoint=args.base_url, reference_time=corpus['reference_time'],
                  corpus_sha256=hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
                  schema_sha256=hashlib.sha256(json.dumps(RequestMeaning.model_json_schema(), sort_keys=True).encode()).hexdigest(),
                  prompt_sha256=hashlib.sha256((SYSTEM + '\n' + TEMPORAL_INSTRUCTIONS).encode()).hexdigest(),
                  review_schema_sha256=hashlib.sha256(json.dumps(MeaningReview.model_json_schema(), sort_keys=True).encode()).hexdigest(),
                  review_prompt_sha256=hashlib.sha256((REVIEW_SYSTEM + '\n' + TEMPORAL_INSTRUCTIONS).encode()).hexdigest(),
                  extractor_source_sha256=hashlib.sha256((ROOT / 'Backend/agent_builder/design/request_meaning.py').read_bytes()).hexdigest(),
                  description='Synthetic extraction + meaning review + Gmail adapter + compiled workflow equality; no live Gmail execution.',
                  settings=dict(temperature=0, think=False, num_ctx=args.num_ctx, num_predict=args.num_predict),
                  selected_case_count=len(cases),
                  results=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        result = evaluate(case, transport, reference)
        output['results'].append(result)
        print(json.dumps({key: result[key] for key in ('id', 'passed', 'wall_seconds', 'failures')}, ensure_ascii=False), flush=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
        if sum(r.get('transport_error', False) for r in output['results']) >= args.max_transport_errors:
            break
    results = output['results']
    output['metrics'] = dict(total=len(results), passed=sum(r['passed'] for r in results),
        valid_json_and_schema=sum(r['valid_json_and_schema'] for r in results),
        model_calls=sum(len(r['model_calls']) for r in results),
        median_seconds=round(statistics.median(r['wall_seconds'] for r in results), 3),
        total_seconds=round(sum(r['wall_seconds'] for r in results), 3))
    completed = [result for result in results if not result.get('transport_error')]
    output['metrics']['transport_failures'] = len(results) - len(completed)
    output['metrics']['pass_rate'] = output['metrics']['passed'] / len(completed) if completed else None
    output['metrics']['completed_corpus'] = len(results) == len(cases)
    output['metrics']['recent5_equivalent_workflows'] = sum(result.get('same_workflow_as_registered_recipe', False) for result in results)
    output['metrics']['recent5_selected_cases'] = sum(case.get('group') == 'recent5' for case in cases)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'metrics': output['metrics'], 'output': str(args.output.resolve())}, ensure_ascii=False), flush=True)
    return 0 if output['metrics']['passed'] == len(cases) else 1


if __name__ == '__main__':
    raise SystemExit(main())
