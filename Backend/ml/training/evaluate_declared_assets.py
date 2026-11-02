"""Replay held-out selections using declared input formats, without fake uploads.

Resources here are offline fixtures. No gold label selects a resource kind, no
original file is fabricated, and no result is called workflow execution accuracy.
"""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from ml.advice.request_context import request_context
from ml.svm56.service_contracts import align_candidates
from ml.training.provenance import load_verified_predictions
from ml.training.declared_inputs import declared_resources
from ml.training.train_candidate import read, rows, digest, DATA_SHA, CATALOG_SHA
from ml.training.tune_candidate import comparison
from workflow_engine.catalog.registry import TOOLS


def replay(row, chosen, resources, registered):
    state = {'messages': [{'role': 'user', 'content': row['query_ko']}], 'resources': resources}
    query, context, reason = request_context(state)
    if reason:
        return set(), reason, []
    candidates = [{'tool_id': t, 'source': 'saved_oof_selection'} for t in sorted(chosen & registered)]
    aligned, changes = align_candidates(query, context, candidates, TOOLS)
    assert all('decision_score' not in r for r in aligned)
    return {r['tool_id'] for r in aligned}, None, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='08_서비스연동패키지')
    parser.add_argument('--candidate', type=Path, required=True, help='OOF experiment directory')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); package, output = args.baseline.resolve(), args.output.resolve()
    if output.exists() or output == package or package in output.parents:
        parser.error('Use a new output directory outside the source package')
    data_file, catalog_file = package/'data/labelled_training_queries.jsonl', package/'catalog/catalog_56.json'
    if digest(data_file) != DATA_SHA or digest(catalog_file) != CATALOG_SHA:
        raise ValueError('Source package hash mismatch')
    data = rows(data_file); labels = [r['id'] for r in read(catalog_file)]
    ids = {r['record_id'] for r in data}; registered = {r['id'] for r in TOOLS}; graded = sorted(registered & set(labels))
    oof_file = args.candidate/'oof_predictions.jsonl'
    chosen = load_verified_predictions(args.candidate,
        {'data': data_file, 'catalog': catalog_file, 'splits': package/'evaluation/SPLITS.json'},
        read(package/'evaluation/SPLITS.json'), ids, labels)
    # This frozen projection contains input descriptions only, not labels or
    # reference operations. Construct it before looking at prediction outcomes.
    fixtures = {r['record_id']: declared_resources(r['provided_inputs_ko']) for r in data}
    output.mkdir(parents=True)
    fixture_file = output/'declared_resources.jsonl'
    fixture_file.write_text(''.join(json.dumps({'record_id': r['record_id'],
        'provided_inputs_ko': r['provided_inputs_ko'], **fixtures[r['record_id']]}, ensure_ascii=False)+'\n' for r in data))
    counts = Counter(f['status'] for f in fixtures.values())
    plan = {'rows': len(data), 'data_sha256': digest(data_file), 'catalog_sha256': digest(catalog_file),
            'oof_sha256': digest(oof_file), 'fixtures_sha256': digest(fixture_file),
            'runner_sha256': digest(Path(__file__)), 'resource_resolver_sha256': digest(Path(__file__).with_name('declared_inputs.py')),
            'input_projection': 'provided_inputs_ko only, never gold labels or operation keys',
            'apply_resources': 'Only unambiguous declared_files; other rows remain unconfirmed, not proven attachment-free',
            'actual_uploads_verified': False, 'training_performed': False,
            'graded_labels': graded, 'fixture_status_counts': dict(counts)}
    (output/'PLAN.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    by_variant = {'query_only': [], 'declared_input_context': []}; details = []
    for row in data:
        fixture = fixtures[row['record_id']]
        for variant in by_variant:
            resources = fixture['resources'] if variant == 'declared_input_context' and fixture['status'] == 'declared_files' else []
            selected, reason, changes = replay(row, chosen[row['record_id']], resources, registered)
            by_variant[variant].append(selected & set(graded))
            details.append({'record_id': row['record_id'], 'variant': variant, 'input_status': fixture['status'],
                            'resource_kinds': sorted({r['kind'] for r in resources}),
                            'selected': sorted(selected), 'handoff_reason': reason, 'changes': changes})
    result = {'plan': plan, 'all_rows': {}, 'explicit_file_subset': {},
              'raw_model_oof': comparison(data, [chosen[r['record_id']] for r in data], labels),
              'warning': '16-ID reference-label agreement under simulated declared metadata; not real upload or execution accuracy.'}
    indices = [i for i, row in enumerate(data) if fixtures[row['record_id']]['status'] == 'declared_files']
    for variant, predictions in by_variant.items():
        result['all_rows'][variant] = comparison(data, predictions, graded)
        result['explicit_file_subset'][variant] = comparison([data[i] for i in indices], [predictions[i] for i in indices], graded)
    (output/'RESULTS.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (output/'replay.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in details))
    with (output/'unresolved_inputs.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['record_id', 'provided_inputs_ko', 'reason', 'status']); writer.writeheader()
        for row in data:
            fixture = fixtures[row['record_id']]
            for entry in fixture['ambiguous']:
                writer.writerow({'record_id': row['record_id'], 'provided_inputs_ko': entry['description'],
                                 'reason': entry['reason'], 'status': fixture['status']})
    assert digest(data_file) == DATA_SHA and digest(catalog_file) == CATALOG_SHA
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    for section in ['all_rows', 'explicit_file_subset']:
        for variant, metrics in result[section].items():
            print(section, variant, {k:v for k,v in metrics.items() if k != 'per_tool'})


if __name__ == '__main__': main()
