"""Compare focused Qwen 9B runs against the same frozen semantic oracle; never execute workflows."""
import argparse
import json
from collections import Counter
from pathlib import Path
import runpy
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / 'scripts/verify'
sys.path.insert(0, str(VERIFY))
from qwen_live_support import digest

quality = runpy.run_path(str(VERIFY / 'check-focused-qwen-outcomes.py'))['check']
profile = runpy.run_path(str(VERIFY / 'profile-qwen-stages.py'))
FROZEN = ROOT / 'docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json'
MODEL = 'a4b-public-qwen35-9b-32k'
WEIGHTS = 'dfd32b09718e2aa05f622795b2ab575efc817c6129928209c532c55023f4f2df'


def load(path):
    return json.loads(path.read_bytes())


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def validate(run, corpus, frozen_sha):
    require(run['status'] == 'completed', 'Run is incomplete')
    require(run['gold_sha256'] == frozen_sha, 'Frozen gold differs')
    ids = [c['id'] for c in run['cases']]
    require(ids and len(ids) == len(set(ids)) and set(ids) <= {'H01', 'H21', 'H32'},
            'Unknown or duplicate case for this experiment')
    require(run['requested_scenarios'] == len(ids) and not run.get('not_run_case_ids'),
            'Requested cases were not all evaluated')
    probe = run['probe']
    require(probe['structured_probe'] == 'passed' and probe['actual_model'].removesuffix(':latest') == MODEL,
            '9B model preflight missing')
    loaded = probe.get('gpu_residency_after_probe') or []
    require(len(loaded) == 1 and loaded[0]['digest'] == WEIGHTS and loaded[0]['size_vram'] > 0,
            'Wrong 9B weights or missing GPU measurement')
    for name in ('code_manifest', 'evaluation_harness_manifest'):
        require(digest(run[name]['files']) == run[name]['source_sha256'], f'{name} digest mismatch')
    scores = {}
    for case in run['cases']:
        require(case['id'] in corpus, 'Case absent from frozen gold')
        for call in case['model_calls']:
            if call['model_response_observed']:
                require(call['actual_model'].removesuffix(':latest') == MODEL, 'Unexpected serving model')
        try:
            quality(case, corpus[case['id']])
        except (KeyError, TypeError, ValueError) as error:
            scores[case['id']] = str(error)
        else:
            scores[case['id']] = 'pass'
    return scores


def call_totals(calls):
    stats = Counter()
    for call in calls:
        profile['add_call'](stats, call)
    return compact(stats)


def compact(stats):
    native = stats.get('server_load_duration_seconds_samples', 0)
    return dict(calls=stats.get('dispatches', 0), responses=stats.get('responses', 0),
                input_tokens=stats.get('input_tokens', 0), output_tokens=stats.get('output_tokens', 0),
                token_samples=stats.get('with_tokens', 0), model_wall_s=stats.get('call_seconds', 0),
                measured_load_s=stats.get('server_load_duration_seconds') if native else None,
                load_samples=native)


def case_rows(run, wanted, quality_results):
    reports = {}
    for case in run['cases']:
        cid = case['id']
        if cid not in wanted:
            continue
        timings = profile['summarize'](run, [cid])
        turns = []
        for attempt in case.get('turn_attempts', []):
            t = attempt['turn_index']
            wall = attempt.get('elapsed_seconds')
            require(wall is not None, f'{cid} turn {t} has no message-to-response timer')
            turns.append(dict(turn=t, route=attempt.get('response_route'), status=attempt['status'],
                              wall_s=wall, gap_to_30_s=wall - 30,
                              **call_totals([c for c in case['model_calls'] if c['turn_index'] == t])))
        stages = {name: dict(wall_s=s['stage_seconds'], **compact(s)) for name, s in timings['stages'].items()}
        reports[cid] = dict(oracle=quality_results[cid], scenario_wall_s=case['elapsed_seconds'],
                            user_turns=turns, stages=stages, **call_totals(case['model_calls']))
    return reports


def sampling_options(run, ids):
    pairs = {}
    for case in run['cases']:
        if case['id'] in ids:
            for call in case['model_calls']:
                pairs.setdefault(call['transport'], set()).add(digest(call['request_settings']))
    return pairs


def compare(base, candidate, base_scores, candidate_scores):
    ids = list(candidate_scores)
    require([cid for cid in base_scores if cid in ids] == ids, 'Case inclusion/order differs from baseline')
    for setting in ('evaluation_environment', 'effective_process_settings', 'corpus_profile', 'catalog_fingerprint',
                    'evaluation_harness_manifest', 'connector_policy'):
        require(base[setting] == candidate[setting], f'{setting} differs')
    require(base['probe']['gpu_residency_after_probe'] == candidate['probe']['gpu_residency_after_probe'],
            'GPU model residency differs after probe')
    base_sampling = sampling_options(base, ids)
    current_sampling = sampling_options(candidate, ids)
    require(all(options <= base_sampling.get(transport, set()) for transport, options in current_sampling.items()),
            'Effective transport sampling parameters changed')
    start_files = base['code_manifest']['files']
    end_files = candidate['code_manifest']['files']
    differences = {n: dict(before=start_files.get(n), after=end_files.get(n))
                   for n in sorted(start_files.keys() | end_files.keys()) if start_files.get(n) != end_files.get(n)}
    before, after = case_rows(base, ids, base_scores), case_rows(candidate, ids, candidate_scores)
    before_p50 = median(c['scenario_wall_s'] for c in before.values())
    after_p50 = median(c['scenario_wall_s'] for c in after.values())
    matched_turn_deltas = {}
    for cid in ids:
        if candidate_scores[cid] != 'pass':
            continue
        old_turns = {turn['turn']: turn for turn in before[cid]['user_turns']}
        matched_turn_deltas[cid] = [dict(turn=t['turn'], route=t['route'],
                                         delta_s=t['wall_s'] - old_turns[t['turn']]['wall_s'])
                                    for t in after[cid]['user_turns'] if t['turn'] in old_turns
                                    and t['route'] == old_turns[t['turn']]['route']]
    return dict(cases=ids, baseline_run_cases=list(base_scores),
                full_case_sequence_matches=ids == list(base_scores),
                all_semantic_checks_passed=all(v == 'pass' for v in candidate_scores.values()),
                baseline_app_sha=base['code_manifest']['source_sha256'],
                candidate_app_sha=candidate['code_manifest']['source_sha256'], changed_app_files=differences,
                scenario_p50_s=dict(before=before_p50, after=after_p50, delta=after_p50-before_p50),
                case_wall_delta_s={cid: after[cid]['scenario_wall_s'] - before[cid]['scenario_wall_s'] for cid in ids},
                matched_successful_turn_wall_delta_s=matched_turn_deltas, baseline=before, candidate=after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidates', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, help='Optional JSON destination; refuses to overwrite.')
    args = parser.parse_args()
    frozen = load(FROZEN)
    gold_sha = digest({k: v for k, v in frozen.items() if k != 'gold_sha256'})
    require(gold_sha == frozen['gold_sha256'], 'Frozen corpus digest mismatch')
    corpus = {case['id']: case for case in frozen['cases']}
    base = load(args.baseline)
    base_scores = validate(base, corpus, gold_sha)
    require(all(v == 'pass' for v in base_scores.values()), 'Baseline did not meet frozen outcomes')
    results = {}
    for file in args.candidates:
        current = load(file)
        scores = validate(current, corpus, gold_sha)
        results[str(file)] = compare(base, current, base_scores, scores)
    encoded = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open('x', encoding='utf-8') as output:
            output.write(encoded + '\n')
    else:
        print(encoded)
    return int(any(not result['all_semantic_checks_passed'] for result in results.values()))


if __name__ == '__main__':
    sys.exit(main())
