"""Replay captured local Qwen responses through the service's message/checkpoint loop."""
import argparse
from collections import defaultdict, deque
from contextlib import ExitStack
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

VERIFY = Path(__file__).resolve().parent

from langchain_core.messages import AIMessage


def verify_session(case, archive, trace, run_case, scratch, response_prefix=None):
    from agent_builder.design import graph
    from agent_builder.design.nodes.direct_conversation import OllamaPlanner
    from service import worker

    if not case['resource_state']['synthetic_only'] or case['gold']['execution']['external_execution_allowed']:
        raise ValueError('Only frozen synthetic, external-execution-disabled cases can be replayed')
    calls = defaultdict(deque)
    for source in (archive, response_prefix):
        if not source:
            continue
        recorded = defaultdict(deque)
        for call in source['model_calls']:
            if not call['model_response_observed']:
                raise ValueError('Only complete recorded responses can be replayed')
            recorded[call['turn_index'], call['stage'], call['transport']].append(call['raw_output'])
        calls.update(recorded)
    used = []
    original_timed = graph._timed_node
    original_handle = worker.handle
    actions = []
    connection_attempts = []

    def observe_action(payload):
        started, cpu = time.perf_counter(), time.process_time()
        try:
            return original_handle(payload)
        finally:
            actions.append(dict(action=payload['action'], wall_s=time.perf_counter()-started,
                                worker_cpu_s=time.process_time()-cpu))

    def timed(name, function):
        wrapped = original_timed(name, function)

        def observed(state):
            old = trace.stage
            trace.stage = name
            try:
                return wrapped(state)
            finally:
                trace.stage = old

        return observed

    def consume(transport):
        key = trace.turn_index, trace.stage, transport
        if not calls[key]:
            raise AssertionError(f'{case["id"]}: no archived {key}')
        used.append(key)
        return calls[key].popleft()

    def native(planner, messages):
        return {'content': consume('ollama_native')['content']}

    def generate(payload, config):
        reply, = consume('existing_langchain_client')
        if reply.get('tool_calls'):
            raise ValueError('Tool-calling intermediate planner responses require a different replay')
        return {'messages': [AIMessage(content=reply['content'])]}

    def blocked(*args, **kwargs):
        connection_attempts.append(True)
        raise AssertionError('Archive replay cannot connect to a network endpoint')

    with ExitStack() as patches, tempfile.TemporaryDirectory(dir=scratch) as db:
        patches.enter_context(patch.object(graph, '_timed_node', timed))
        patches.enter_context(patch.object(worker, 'handle', observe_action))
        patches.enter_context(patch.object(OllamaPlanner, 'invoke', native))
        patches.enter_context(patch('agent_builder.design.nodes.conversation.create_deep_agent',
                                    lambda **kw: SimpleNamespace(invoke=generate)))
        patches.enter_context(patch.object(socket.socket, 'connect', blocked))
        patches.enter_context(patch.object(socket.socket, 'connect_ex', blocked))
        patches.enter_context(patch.object(socket, 'getaddrinfo', blocked))
        result = run_case(case, os.environ['A4B_MAIN_MODEL'], trace, Path(db))
    expected = case['gold']
    routes = result.get('actual_route'), result.get('final_route')
    if (result.get('status') != 'completed' or archive.get('status') != 'completed' or
            routes != (expected['routing'], expected['final_routing']) or
            result.get('external_execution') != 'NOT_RUN' or result['live_model_calls'] or connection_attempts or
            result.get('workflow') != archive.get('workflow') or
            len(result['turns']) != len(archive['turns'])):
        raise ValueError(f"{case['id']}: replay differs from gold route or archived workflow: "
                         f"{routes}, status={result['status']}, error={result.get('error_type')}, "
                         f"validation={result.get('validation')}")
    previous, archived_previous = {}, {}
    for replayed, recorded in zip(result['turns'], archive['turns']):
        response = replayed['response']
        original = recorded['response']
        draft = response.get('draft') or {}
        old_draft = original.get('draft') or {}
        question_fields = ('key',) if response_prefix else ('key', 'text')
        def displayed(item):
            return [tuple(q[field] for field in question_fields) for q in item.get('pending_questions', [])]
        if (replayed['user'] != recorded['user'] or response['status'] != original['status'] or
                draft.get('workflow') != old_draft.get('workflow') or draft.get('schedule') != old_draft.get('schedule') or
                displayed(response) != displayed(original)):
            raise ValueError(f"{case['id']}: replayed answers or questions differ")
        if recorded['question_id'] is not None:
            selected = previous.get(replayed['question_id'])
            if selected is None or selected != archived_previous[recorded['question_id']]:
                raise ValueError(f"{case['id']}: reply no longer belongs to the same visible question")
        previous = {q['id']: q['key'] for q in response.get('pending_questions', [])}
        archived_previous = {q['id']: q['key'] for q in original.get('pending_questions', [])}
    if expected['execution']['mode'] == 'local_synthetic' and result['execution_success'] is not True:
        raise ValueError(f"{case['id']}: synthetic rows differ")
    if routes[-1] == 'READY' and not result['schema_valid']:
        raise ValueError(f"{case['id']}: workflow validation failed")
    if routes[-1] == 'UNSUPPORTED':
        new = result['turns'][-1]['response']['capability_assessment']['missing_actions']
        old = archive['turns'][-1]['response']['capability_assessment']['missing_actions']
        if not new or new != old:
            raise ValueError(f"{case['id']}: unsupported actions differ")
    return dict(id=case['id'], route=routes, turns=len(result['turns']), matched_archived_workflow=True,
                synthetic_rows_match=result['execution_success'], replayed_responses=len(used),
                unused_responses=sum(map(len,calls.values())), network_calls=0,
                displayed_question_keys=[[q['key'] for q in t['response'].get('pending_questions', [])] for t in result['turns']],
                offline_approval_execution=[a for a in actions if a['action'] in ('confirm','test')])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--response-prefix', type=Path,
                        help='Take captured turn/stage replies from this run, then use --run for absent stages')
    parser.add_argument('--corpus', required=True, type=Path)
    parser.add_argument('--case', required=True, action='append')
    parser.add_argument('--scratch', required=True, type=Path)
    parser.add_argument('--source-root', type=Path, default=VERIFY.parent.parent,
                        help='Optional frozen application and service harness checkout')
    args = parser.parse_args()
    source = args.source_root.resolve()
    verify = source / 'scripts/verify'
    sys.path[:0] = [str(source / 'Backend'), str(verify)]
    from qwen_live_support import digest
    from qwen_live_trace import Trace
    run = json.loads(args.run.read_bytes())
    corpus = json.loads(args.corpus.read_bytes())
    if digest({k:v for k,v in corpus.items() if k != 'gold_sha256'}) != corpus['gold_sha256'] or run['gold_sha256'] != corpus['gold_sha256']:
        raise ValueError('Corpus digest mismatch')
    prefix = json.loads(args.response_prefix.read_bytes()) if args.response_prefix else None
    if prefix and prefix.get('gold_sha256') != corpus['gold_sha256']:
        raise ValueError('Response prefix and reference must use the same frozen corpus')
    profile_keys = {'A4B_MAIN_PROVIDER', 'A4B_MAIN_MODEL', 'A4B_CAPABILITY_MODEL', 'A4B_PLANNER_MODE',
                    'A4B_CAPABILITY_MODE', 'A4B_CONDITION_MODE', 'A4B_REQUIREMENT_MODE', 'A4B_VERIFIER_MODE',
                    'A4B_REQUIREMENT_GENERATION_MODE', 'A4B_TOOL_CATALOG_MODE', 'A4B_TOOL_ADVICE_MODE',
                    'A4B_TOOL_ADVICE_BACKEND', 'A4B_OLLAMA_NUM_CTX', 'A4B_OLLAMA_NUM_PREDICT',
                    'A4B_OLLAMA_TEMPERATURE', 'A4B_MAIN_TIMEOUT_S'}
    settings = {k:v for k,v in run['effective_process_settings'].items() if k in profile_keys and v is not None}
    if settings.get('A4B_MAIN_PROVIDER') != 'ollama' or settings.get('A4B_PLANNER_MODE') != 'deepagent':
        raise ValueError('This replay supports the local Ollama DeepAgent transport')
    settings.update(A4B_MAIN_BASE_URL='http://127.0.0.1:11434/v1', A4B_MAIN_API_KEY='',
                    A4B_STORAGE='sqlite', LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
    with patch.dict(os.environ, settings):
        for key in ('A4B_DATABASE_URL', 'A4B_BUSINESS_RECORDS', 'A4B_TIMING_LOG', 'A4B_OLLAMA_MODEL_ALIASES'):
            os.environ.pop(key, None)
        run_case = runpy.run_path(str(verify / 'verify-qwen-live-evaluation.py'))['run_case']
        args.scratch.mkdir(parents=True,exist_ok=True)
        by_id = {r['id']: r for r in run['cases']}
        cases = {c['id']: c for c in corpus['cases']}
        for cid in args.case:
            endpoint = settings['A4B_MAIN_BASE_URL']
            trace = Trace(endpoint, allow_local_endpoint=endpoint)
            captured = next(c for c in prefix['cases'] if c['id'] == cid) if prefix else None
            print(json.dumps(verify_session(cases[cid], by_id[cid], trace, run_case, args.scratch, captured)))
