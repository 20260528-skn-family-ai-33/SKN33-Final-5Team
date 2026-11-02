"""Nested grouped development comparison of tool-specific SVM policies.

Outer validation labels never select thresholds or model heads. Inner OOF selects
only policies that do not add known false positives or lose known positives
relative to the configured reference weight at threshold zero on that inner data.
"""
import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import joblib
import numpy as np
from sklearn.model_selection import GroupKFold
from threadpoolctl import threadpool_limits

from ml.training.train_candidate import (read, rows, digest, supplement, validate_supplement,
                                        fit, metrics, DATA_SHA, CATALOG_SHA, SPLITS_SHA)

GRID = (-.3, -.2, -.1, 0., .1, .2, .3, .4)
WEIGHTS = (1., 2.)
MIN_POSITIVES = 20


def groups_for(data):
    parent = list(range(len(data))); seen = {}
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, row in enumerate(data):
        source = row.get('source') or {}
        keys = [(k, source[k]) for k in ('source_id', 'group_id', 'source_group_original',
                'request_sha256', 'raw_sha256', 'native_record_sha256', 'workflow_sha256')
                if isinstance(source.get(k), str) and source[k].strip()]
        q = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', row['query_ko'])).strip().casefold()
        keys += [('query', re.sub(r'\d+', '<n>', q))]
        for key in keys:
            if key in seen: parent[find(i)] = find(seen[key])
            else: seen[key] = i
    return np.array([find(i) for i in range(len(data))])


def scores(model, data):
    x = model['vectorizer'].transform([r['query_ko'] for r in data])
    out = np.full((len(data), len(model['labels'])), np.nan)
    for j, head in enumerate(model['heads']):
        if head is not None: out[:, j] = head.decision_function(x)
    return out


def choose_policies(y, score_arrays, weights=WEIGHTS):
    policies = []
    for j in range(y.shape[1]):
        positive, negative = y[:, j] == 1, y[:, j] == 0
        default = np.isfinite(score_arrays[0][:, j]) & (score_arrays[0][:, j] >= 0.)
        base_tp, base_fp = int((default & positive).sum()), int((default & negative).sum())
        best = (0, 0., base_tp, base_fp)
        def quality(item):
            variant, threshold, tp, fp = item
            fn = int(positive.sum()) - tp
            f1 = 2*tp/(2*tp+fp+fn) if tp else 0.
            # Equal quality retains the baseline rather than tuning a pointless cutoff.
            return f1, -fp, tp, -(variant != 0 or threshold != 0.), -abs(threshold)
        if positive.sum() >= MIN_POSITIVES and negative.sum() >= 100:
            for variant, matrix in enumerate(score_arrays):
                for threshold in GRID:
                    pred = np.isfinite(matrix[:, j]) & (matrix[:, j] >= threshold)
                    tp, fp = int((pred & positive).sum()), int((pred & negative).sum())
                    item = (variant, threshold, tp, fp)
                    if tp >= base_tp and fp <= base_fp and quality(item) > quality(best):
                        best = item
        variant, threshold, tp, fp = best
        policies.append({'variant': variant, 'compound_positive_weight': weights[variant],
                         'threshold': threshold, 'inner_tp': tp, 'inner_fp': fp,
                         'baseline_inner_tp': base_tp, 'baseline_inner_fp': base_fp,
                         'known_positives': int(positive.sum())})
    return policies


def calibrate(data, labels, feature_mode='char', weights=WEIGHTS):
    y = np.array([[r['labels_1_0_minus1_draft'][t] for t in labels] for r in data])
    arrays = [np.full(y.shape, np.nan) for _ in weights]
    groups = groups_for(data)
    splits = []
    for train_idx, dev_idx in GroupKFold(n_splits=3).split(np.arange(len(data)), groups=groups):
        assert not set(groups[train_idx]) & set(groups[dev_idx])
        train, dev = [data[i] for i in train_idx], [data[i] for i in dev_idx]
        for variant, weight in enumerate(weights):
            arrays[variant][dev_idx] = scores(fit(train, labels, weight, feature_mode), dev)
        splits.append({'train_ids': [r['record_id'] for r in train],
                       'validation_ids': [r['record_id'] for r in dev]})
    return choose_policies(y, arrays, weights), splits


def fit_selected(data, labels, policies, feature_mode='char', weights=WEIGHTS):
    models = [fit(data, labels, weight, feature_mode) for weight in weights]
    np.testing.assert_array_equal(models[0]['vectorizer'].get_feature_names_out(),
                                  models[1]['vectorizer'].get_feature_names_out())
    sample = [r['query_ko'] for r in data[:10]]
    assert (models[0]['vectorizer'].transform(sample) != models[1]['vectorizer'].transform(sample)).nnz == 0
    result = dict(models[0])
    result['heads'] = [models[p['variant']]['heads'][j] for j, p in enumerate(policies)]
    result['thresholds'] = [p['threshold'] for p in policies]
    result['selection_policy'] = 'nested_grouped_pareto_v1'
    result['policy_details'] = policies
    return result


def selected(model, data):
    matrix = scores(model, data)
    mask = np.isfinite(matrix) & (matrix >= np.array(model['thresholds']))
    return [{t for t, flag in zip(model['labels'], row) if flag} for row in mask]


def comparison(data, preds, labels):
    result = metrics(data, preds, labels)
    tp = sum(p['tp'] for p in result['per_tool'].values())
    fp = sum(p['fp'] for p in result['per_tool'].values())
    fn = sum(p['fn'] for p in result['per_tool'].values())
    result.update(tp=tp, fp=fp, fn=fn, precision=tp/(tp+fp) if tp else 0.,
                  recall=tp/(tp+fn) if tp else 0., mean_selected=float(np.mean([len(p) for p in preds])))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--feature-mode', choices=['char', 'hybrid_char'], default='char')
    parser.add_argument('--base-weight', type=int, choices=[1, 2], default=1)
    args = parser.parse_args(); package, output = args.baseline.resolve(), args.output.resolve()
    weights = (float(args.base_weight), float(3-args.base_weight))
    if package == output or package in output.parents or output.exists():
        parser.error('Use a new output directory outside the baseline')
    files = {'data': package/'data/labelled_training_queries.jsonl',
             'catalog': package/'catalog/catalog_56.json', 'splits': package/'evaluation/SPLITS.json'}
    for key, expected in [('data', DATA_SHA), ('catalog', CATALOG_SHA), ('splits', SPLITS_SHA)]:
        if digest(files[key]) != expected: raise ValueError('Baseline hash mismatch: '+key)
    data = rows(files['data']); labels = [r['id'] for r in read(files['catalog'])]
    extra = supplement(labels); validate_supplement(data, extra)
    byid = {r['record_id']: r for r in data}; splits = read(files['splits'])
    output.mkdir(parents=True)
    plan = {'source_hashes': {k: digest(p) for k, p in files.items()},
            'trainer_sha256': digest(Path(__file__)), 'base_trainer_sha256': digest(Path(__file__).with_name('train_candidate.py')),
            'supplement_sha256': digest(Path(__file__).with_name('supplemental_cases.json')),
            'threshold_grid': GRID, 'weights': weights, 'feature_mode': args.feature_mode, 'min_positives': MIN_POSITIVES,
            'selection': 'Inner OOF only; no extra known FP and no lost known TP versus configured reference weight at zero',
            'outer_folds': 5, 'inner_folds': 3, 'independent_test': False,
            'note': 'Same previously inspected development corpus; nested selection does not make this human-gold or independent.'}
    (output/'PLAN.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    predictions = {}; extra_predictions = {}; records = []; lineage = []
    with threadpool_limits(limits=2):
        for split in splits:
            fold = split['fold']; train_ids, dev_ids = set(split['train_ids']), set(split['development_ids'])
            assert not train_ids & dev_ids and train_ids | dev_ids == set(byid)
            train = [byid[i] for i in split['train_ids']] + [r for r in extra if r['development_fold'] != fold]
            dev = [byid[i] for i in split['development_ids']]
            extra_dev = [r for r in extra if r['development_fold'] == fold]
            all_rows = train + dev + extra_dev; gg = groups_for(all_rows)
            assert not set(gg[:len(train)]) & set(gg[len(train):])
            policies, inner_splits = calibrate(train, labels, args.feature_mode, weights)
            model = fit_selected(train, labels, policies, args.feature_mode, weights)
            model_file = f'outer-{fold}.joblib'
            joblib.dump(model, output/model_file)
            lineage.append({'fold': fold, 'train_ids': [r['record_id'] for r in train],
                            'validation_ids': [r['record_id'] for r in dev + extra_dev], 'model_file': model_file})
            pp, ep = selected(model, dev), selected(model, extra_dev)
            predictions.update({r['record_id']: p for r, p in zip(dev, pp)})
            extra_predictions.update({r['record_id']: p for r, p in zip(extra_dev, ep)})
            records.append({'fold': fold, 'outer_validation_ids': split['development_ids'],
                            'policies': dict(zip(labels, policies)), 'inner_splits': inner_splits,
                            'metrics': comparison(dev, pp, labels)})
            # Keep progress artifacts even if a later fit fails.
            (output/f'fold-{fold}.json').write_text(json.dumps(records[-1], ensure_ascii=False, indent=2))
            print('Completed outer fold', fold, flush=True)
        policies, inner_splits = calibrate(data + extra, labels, args.feature_mode, weights)
        model = fit_selected(data + extra, labels, policies, args.feature_mode, weights)
        joblib.dump(model, output/'candidate.joblib')
    result = {'original_oof': comparison(data, [predictions[r['record_id']] for r in data], labels),
              'synthetic_oof': comparison(extra, [extra_predictions[r['record_id']] for r in extra], labels),
              'policies': dict(zip(labels, policies)), 'final_inner_splits': inner_splits,
              'model_sha256': digest(output/'candidate.joblib'), 'independent_test': False,
              'production_model_changed': False}
    (output/'RESULTS.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    with (output/'oof_predictions.jsonl').open('w') as f:
        for r in data + extra:
            p = predictions if r['record_id'] in byid else extra_predictions
            f.write(json.dumps({'record_id': r['record_id'], 'selected': sorted(p[r['record_id']]), 'fold': next(f['fold'] for f in lineage if r['record_id'] in f['validation_ids'])})+'\n')
    (output/'supplement.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in extra))
    from ml.training.provenance import write_manifest
    write_manifest(output, files, lineage)
    print(json.dumps({k:v for k,v in result['original_oof'].items() if k!='per_tool'}, indent=2))


if __name__ == '__main__':
    main()
