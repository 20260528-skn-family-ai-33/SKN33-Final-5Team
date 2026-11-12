import runpy
from pathlib import Path

import pytest


profile = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'profile-qwen-stages.py'))


def test_turn_boundaries_and_absolute_indices_do_not_mix_generation_and_repair():
    calls = [
        dict(stage='conversation', http_dispatch_attempts=1, model_response_observed=True,
             elapsed_seconds=2, usage=None,
             raw_output=[dict(usage_metadata=dict(input_tokens=8, output_tokens=3))]),
        dict(stage='conversation', http_dispatch_attempts=1, model_response_observed=True,
             elapsed_seconds=1, usage=dict(prompt_eval_count=4, eval_count=2)),
        dict(stage='conversation', http_dispatch_attempts=1, model_response_observed=True,
             elapsed_seconds=3, usage=None),
    ]
    stages = [dict(stage='conversation', turn_index=turn, elapsed_seconds=call['elapsed_seconds'],
                   model_call_indices=[index + 15], parsed_state={})
              for index, (turn, call) in enumerate(zip([0, 0, 1], calls))]
    case = dict(id='A', status='completed', elapsed_seconds=8, model_calls=calls, stages=stages,
                model_call_offset=15)
    report = profile['summarize'](dict(cases=[case], status='completed'), ['A'])
    assert report['model_dispatches'] == 3
    assert report['stages']['generation']['dispatches'] == 2
    assert report['stages']['generation']['with_tokens'] == 1
    assert report['stages']['generation']['input_tokens'] == 8
    assert report['stages']['repair']['output_tokens'] == 2
    assert report['p50_seconds'] == report['p95_seconds'] == 8


def test_comparison_rejects_missing_cases_and_partial_runs():
    for status in ('running', 'completed'):
        with pytest.raises(ValueError):
            profile['summarize'](dict(status=status, cases=[]), ['missing'])


def test_end_to_end_turn_times_are_reported_only_when_measured():
    case = dict(id='H21', status='completed', elapsed_seconds=75, model_calls=[], stages=[],
                turn_attempts=[dict(turn_index=0, status='returned', response_route='CLARIFY', elapsed_seconds=20),
                               dict(turn_index=1, status='returned')])
    turns = profile['summarize'](dict(status='completed', cases=[case]))['user_turns']
    assert turns[0]['response_route'] == 'CLARIFY' and turns[0]['elapsed_seconds'] == 20
    assert turns[1]['elapsed_seconds'] is None


def test_only_present_server_timings_contribute_and_review_uses_actual_token_usage():
    native = dict(stage='request_meaning', turn_index=0, model_response_observed=True, http_dispatch_attempts=1,
                  elapsed_seconds=1.5, worker_cpu_seconds=.125, snapshot_wall_seconds=.1,
                  raw_output={'content':'{"faithful": true, "issues": []}'},
                  usage=dict(prompt_eval_count=10, eval_count=2, total_duration=1_200_000_000,
                             prompt_eval_duration=200_000_000, eval_duration=800_000_000))
    case = dict(id='A', status='completed', elapsed_seconds=2, worker_cpu_seconds=.25, snapshot_wall_seconds=.11,
                model_calls=[native], model_call_offset=1, stages=[dict(stage='request_meaning', turn_index=0,
                elapsed_seconds=1.8, worker_cpu_seconds=.2, snapshot_wall_seconds=.11,
                model_call_indices=[1], parsed_state={})])
    report = profile['summarize'](dict(status='completed',cases=[case]))
    assert report['stages']['request_meaning']['worker_cpu_seconds'] == .2
    metrics = report['meaning_calls_by_response']['review']
    assert metrics['input_tokens'] == 10 and metrics['output_tokens'] == 2
    assert metrics['call_worker_cpu_seconds'] == .125
    assert metrics['server_total_duration_seconds'] == 1.2 and metrics['server_eval_duration_seconds'] == .8
    assert 'server_load_duration_seconds' not in metrics
