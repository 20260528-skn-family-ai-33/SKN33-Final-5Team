"""Fail closed on incomplete, mixed-run or non-held-out candidate artifacts.

This is an integrity/lineage contract for trusted local trainers, not a proof
that an arbitrary author truthfully described their training procedure.
"""
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_manifest(output, source_files, folds):
    output = Path(output)
    names = ['PLAN.json', 'RESULTS.json', 'candidate.joblib', 'oof_predictions.jsonl', 'supplement.jsonl']
    names += [f['model_file'] for f in folds]
    manifest = {'schema': 1, 'status': 'complete',
                'source_hashes': {k: sha(v) for k, v in source_files.items()},
                'artifacts': {n: sha(output/n) for n in names}, 'folds': folds}
    # Published last: partial runs are never valid comparison inputs.
    (output/'COMPLETE.json').write_text(json.dumps(manifest, indent=2))


def load_verified_predictions(directory, source_files, splits, data_ids, labels):
    directory = Path(directory)
    manifest = json.loads((directory/'COMPLETE.json').read_text())
    if manifest.get('schema') != 1 or manifest.get('status') != 'complete':
        raise ValueError('Incomplete experiment')
    if manifest.get('source_hashes') != {k: sha(v) for k, v in source_files.items()}:
        raise ValueError('OOF source hash mismatch')
    artifacts = manifest['artifacts']
    required = {'PLAN.json', 'RESULTS.json', 'candidate.joblib', 'oof_predictions.jsonl', 'supplement.jsonl'}
    if not required <= artifacts.keys():
        raise ValueError('Missing required artifact')
    for name, expected in artifacts.items():
        if Path(name).name != name or sha(directory/name) != expected:
            raise ValueError('OOF artifact hash mismatch')
    extra = [json.loads(x) for x in (directory/'supplement.jsonl').read_text().splitlines() if x.strip()]
    extra_byid = {r['record_id']: r for r in extra}
    if len(extra_byid) != len(extra) or set(extra_byid) & data_ids:
        raise ValueError('Duplicate supplement IDs')
    expected_folds = {s['fold']: s for s in splits}
    seen = set(); assigned = {}
    for fold in manifest['folds']:
        number = fold['fold']
        if number in seen or number not in expected_folds:
            raise ValueError('Invalid OOF fold')
        seen.add(number); split = expected_folds[number]
        train, validation = fold['train_ids'], fold['validation_ids']
        expected_train = set(split['train_ids']) | {i for i, r in extra_byid.items() if r['development_fold'] != number}
        expected_dev = set(split['development_ids']) | {i for i, r in extra_byid.items() if r['development_fold'] == number}
        if (len(set(train)) != len(train) or len(set(validation)) != len(validation)
                or set(train) != expected_train or set(validation) != expected_dev or set(train) & set(validation)):
            raise ValueError('OOF training/validation lineage mismatch')
        if fold['model_file'] not in artifacts:
            raise ValueError('Missing fold model artifact')
        for identifier in validation:
            if identifier in assigned:
                raise ValueError('Duplicate OOF assignment')
            assigned[identifier] = number
    if seen != set(expected_folds) or set(assigned) != data_ids | set(extra_byid):
        raise ValueError('Incomplete OOF coverage')
    records = [json.loads(x) for x in (directory/'oof_predictions.jsonl').read_text().splitlines() if x.strip()]
    predictions = {}; seen_ids = set()
    for row in records:
        identifier = row['record_id']; chosen = row['selected']
        if identifier in seen_ids or identifier not in assigned or row.get('fold') != assigned[identifier]:
            raise ValueError('Invalid prediction fold/ID')
        seen_ids.add(identifier)
        if not isinstance(chosen, list) or len(set(chosen)) != len(chosen) or set(chosen)-set(labels):
            raise ValueError('Invalid selected labels')
        if identifier in data_ids:
            predictions[identifier] = set(chosen)
    if seen_ids != set(assigned):
        raise ValueError('Missing OOF predictions')
    # Trusted local joblib artifacts only: replay the saved fold models on their
    # held-out queries, rather than trusting a file merely named OOF.
    import joblib
    import numpy as np
    original = [json.loads(x) for x in Path(source_files['data']).read_text(encoding='utf-8-sig').splitlines() if x.strip()]
    byid = {r['record_id']: r for r in original + extra}
    recorded = {r['record_id']: set(r['selected']) for r in records}
    for fold in manifest['folds']:
        model = joblib.load(directory/fold['model_file'])
        if model['labels'] != list(labels) or len(model['heads']) != len(labels):
            raise ValueError('Fold model label mismatch')
        validation = fold['validation_ids']
        x = model['vectorizer'].transform([byid[i]['query_ko'] for i in validation])
        cutoffs = model.get('thresholds', [model.get('threshold', 0.)] * len(labels))
        if len(cutoffs) != len(labels) or not np.isfinite(cutoffs).all():
            raise ValueError('Invalid fold model thresholds')
        actual = [set() for _ in validation]
        for label, head, threshold in zip(labels, model['heads'], cutoffs):
            if head is not None:
                scores = head.decision_function(x)
                if not np.isfinite(scores).all():
                    raise ValueError('Nonfinite fold prediction')
                for i in np.flatnonzero(scores >= threshold):
                    actual[i].add(label)
        if any(recorded[i] != selected for i, selected in zip(validation, actual)):
            raise ValueError('OOF predictions disagree with held-out fold models')
    return predictions
