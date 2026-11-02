"""Reproduce the supplied 56-label CPU experiment with a synthetic overlay.

Original queries/labels/splits and threshold=0 stay frozen. Added groups never
cross folds. All metrics are development diagnostics, NOT human-gold test scores.
The output model is a candidate and is never installed in the service hook.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
    os.environ.setdefault(key, '2')

import joblib
import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC
from sklearn.pipeline import FeatureUnion
from threadpoolctl import threadpool_limits

SEED = 20261030
DATA_SHA = 'a5f0537b4315e9b46534084ab0036a36b9f6ba1c8cd4d37c96a034076180fe36'
CATALOG_SHA = 'cd956b51658051ecd95c642b5738238b3ae7f819c89799668280ba6d96f5dd2a'
SPLITS_SHA = '8293778325da23ebb5c1d83ff3b5b0d564513080fe73b3969c5f786cc04a0087'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def supplement(labels):
    out = []
    groups = read(Path(__file__).with_name('supplemental_cases.json'))
    for index, group in enumerate(groups):
        positive, negative = set(group['positive']), set(group.get('negative', []))
        if (positive | negative) - set(labels) or positive & negative:
            raise ValueError('Supplement contains labels outside the frozen catalogue')
        for n, query in enumerate(group['queries']):
            out.append({'record_id': f'supplement:{group["group"]}:{n}', 'query_ko': query,
                        'source': {'group_id': group['group']}, 'development_fold': index % 5 + 1,
                        'labels_1_0_minus1_draft': {t: 1 if t in positive else 0 if t in negative else -1 for t in labels},
                        'human_approved': False, 'execution_verified': False,
                        'label_source': 'AI-authored synthetic service-contract examples; review pending'})
    return out


def validate_supplement(data, extra):
    """Do not let an added paraphrase of the same numeric template cross folds."""
    def key(query):
        text = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', query)).strip().casefold()
        return re.sub(r'\d+', '<n>', text)
    original = {key(r['query_ko']) for r in data}
    added = {}
    for r in extra:
        text = key(r['query_ko'])
        if text in original:
            raise ValueError('Supplement duplicates an original request; assign its source group before training')
        group = (r['source']['group_id'], r['development_fold'])
        if text in added and added[text] != group:
            raise ValueError('Supplement duplicate crosses source groups')
        added[text] = group


def fit(data, labels, compound_positive_weight=1., feature_mode='char'):
    vec = TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 4), min_df=1,
                         sublinear_tf=True, max_features=30000)
    if feature_mode in ('hybrid', 'hybrid_char'):
        # Word phrases distinguish actions sharing the same short character
        # fragments. Each branch remains fitted only on the current train fold.
        word_weight = .2 if feature_mode == 'hybrid_char' else .5
        vec = FeatureUnion([('char', vec), ('word', TfidfVectorizer(
            ngram_range=(1, 2), min_df=1, sublinear_tf=True, max_features=20000))],
            transformer_weights={'char': (1-word_weight)**.5, 'word': word_weight**.5})
    elif feature_mode == 'char_long':
        vec = TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 6), min_df=1,
                             sublinear_tf=True, max_features=50000)
    elif feature_mode != 'char':
        raise ValueError('Unknown feature mode')
    x = vec.fit_transform([r['query_ko'] for r in data])
    y = np.array([[r['labels_1_0_minus1_draft'][t] for t in labels] for r in data])
    heads = []
    for j in range(len(labels)):
        known = y[:, j] >= 0
        if len(np.unique(y[known, j])) < 2:
            heads.append(None)
        else:
            head = LinearSVC(C=1., class_weight='balanced', max_iter=10000,
                             random_state=SEED, dual='auto')
            # Weight only known positives in compound requests. Unknown labels
            # still never become negatives, and no validation row supplies weight.
            weights = np.where((y[:, j] == 1) & ((y == 1).sum(axis=1) > 1),
                               compound_positive_weight, 1.)
            head.fit(x[known], y[known, j], sample_weight=weights[known])
            heads.append(head)
    return {'method': feature_mode+'_svm', 'vectorizer': vec, 'heads': heads, 'labels': labels,
            'threshold': 0., 'candidate_only': True,
            'meaning': 'AI-reference development candidate; no human gold or execution validation'}


def predict(model, data):
    if not data:
        return []
    x = model['vectorizer'].transform([r['query_ko'] for r in data])
    chosen = [set() for _ in data]
    for tool, head in zip(model['labels'], model['heads']):
        if head is not None:
            for i in np.flatnonzero(head.decision_function(x) >= 0.):
                chosen[i].add(tool)
    return chosen


def metrics(data, predictions, labels):
    tp = fp = fn = 0
    complete = []
    compound = []
    per_tool = {t: {'positive': 0, 'tp': 0, 'fp': 0, 'fn': 0} for t in labels}
    for r, pred in zip(data, predictions):
        y = r['labels_1_0_minus1_draft']
        gold = {t for t in labels if y[t] == 1}
        negative = {t for t in labels if y[t] == 0}
        tp += len(gold & pred); fp += len(negative & pred); fn += len(gold - pred)
        for t in labels:
            per_tool[t]['positive'] += t in gold
            per_tool[t]['tp'] += t in gold & pred
            per_tool[t]['fp'] += t in negative & pred
            per_tool[t]['fn'] += t in gold - pred
        if gold and all(y[t] != -1 for t in labels):
            complete.append(gold <= pred)
            if len(gold) > 1:
                compound.append(gold <= pred)
    return {'rows': len(data), 'masked_micro_f1': 2*tp/(2*tp+fp+fn) if tp else 0.,
            'complete_rows': len(complete), 'complete_coverage': float(np.mean(complete)) if complete else None,
            'compound_rows': len(compound), 'compound_coverage': float(np.mean(compound)) if compound else None,
            'known_positive_coverage': float(np.mean([all(t in p for t in labels if r['labels_1_0_minus1_draft'][t] == 1)
                                                     for r, p in zip(data, predictions)])) if data else None,
            'per_tool': per_tool}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='08 service integration package')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compound-positive-weight', type=float, default=1.)
    parser.add_argument('--feature-mode', choices=['char', 'hybrid', 'hybrid_char', 'char_long'], default='char')
    args = parser.parse_args()
    if not 1. <= args.compound_positive_weight <= 5.:
        parser.error('compound positive weight must be between 1 and 5')
    package, output = args.baseline.resolve(), args.output.resolve()
    if package == output or package in output.parents or output.exists():
        parser.error('Use a new output directory outside the baseline package')
    output.mkdir(parents=True)
    files = {'data': package/'data/labelled_training_queries.jsonl',
             'catalog': package/'catalog/catalog_56.json', 'splits': package/'evaluation/SPLITS.json'}
    for key, expected in [('data', DATA_SHA), ('catalog', CATALOG_SHA), ('splits', SPLITS_SHA)]:
        if digest(files[key]) != expected:
            raise ValueError('Baseline hash mismatch: ' + key)
    data = rows(files['data']); labels = [t['id'] for t in read(files['catalog'])]
    extra = supplement(labels)
    validate_supplement(data, extra)
    byid = {r['record_id']: r for r in data}; splits = read(files['splits'])
    all_ids = set(byid)
    if len(byid) != len(data):
        raise ValueError('Duplicate baseline IDs')
    validations = []
    for split in splits:
        train_ids, dev_ids = set(split['train_ids']), set(split['development_ids'])
        if train_ids & dev_ids or train_ids | dev_ids != all_ids:
            raise ValueError('Invalid baseline split')
        validations.extend(dev_ids)
    if len(validations) != len(set(validations)) or set(validations) != all_ids:
        raise ValueError('Each original row must have exactly one OOF prediction')
    plan = {'baseline_hashes': {k: digest(p) for k, p in files.items()},
            'trainer_sha256': digest(Path(__file__)),
            'runtime_versions': {'numpy': np.__version__, 'sklearn': sklearn.__version__, 'joblib': joblib.__version__},
            'supplement_sha256': digest(Path(__file__).with_name('supplemental_cases.json')),
            'original_rows': len(data), 'supplement_rows': len(extra), 'labels': labels,
            'threshold': 0., 'seed': SEED, 'scope': 'development validation only; not independent final evaluation',
            'compound_positive_weight': args.compound_positive_weight,
            'feature_mode': args.feature_mode,
            'human_gold': False, 'install_runtime_model': False}
    (output/'PLAN.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    (output/'supplement.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in extra))
    original_predictions = {}; extra_predictions = {}; fold_metrics = []; lineage = []
    with threadpool_limits(limits=2):
        for split in splits:
            fold = split['fold']
            train = [byid[i] for i in split['train_ids']] + [r for r in extra if r['development_fold'] != fold]
            dev = [byid[i] for i in split['development_ids']]
            extra_dev = [r for r in extra if r['development_fold'] == fold]
            model = fit(train, labels, args.compound_positive_weight, args.feature_mode)
            model_file = f'outer-{fold}.joblib'
            joblib.dump(model, output/model_file)
            lineage.append({'fold': fold, 'train_ids': [r['record_id'] for r in train],
                            'validation_ids': [r['record_id'] for r in dev + extra_dev], 'model_file': model_file})
            pp = predict(model, dev); ep = predict(model, extra_dev)
            original_predictions.update({r['record_id']: p for r, p in zip(dev, pp)})
            extra_predictions.update({r['record_id']: p for r, p in zip(extra_dev, ep)})
            fold_metrics.append({'fold': fold, 'train_rows': len(train), 'original': metrics(dev, pp, labels),
                                 'synthetic': metrics(extra_dev, ep, labels)})
            print('Completed fold', fold, flush=True)
        model = fit(data + extra, labels, args.compound_positive_weight, args.feature_mode)
        joblib.dump(model, output/'candidate.joblib')
    result = {'original_oof': metrics(data, [original_predictions[r['record_id']] for r in data], labels),
              'synthetic_oof': metrics(extra, [extra_predictions[r['record_id']] for r in extra], labels),
              'folds': fold_metrics, 'model_sha256': digest(output/'candidate.joblib'),
              'untrained_tools': [t for t, h in zip(labels, model['heads']) if h is None],
              'production_model_changed': False, 'independent_test': False}
    (output/'RESULTS.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    with (output/'compound_review_queue.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['record_id', 'query', 'required', 'predicted', 'missing',
                                               'human_approved', 'review_status'])
        writer.writeheader()
        for row in data:
            y = row['labels_1_0_minus1_draft']
            gold = {t for t in labels if y[t] == 1}
            pred = original_predictions[row['record_id']]
            if len(gold) > 1 and all(y[t] != -1 for t in labels) and gold - pred:
                writer.writerow({'record_id': row['record_id'], 'query': row['query_ko'],
                                 'required': ','.join(sorted(gold)), 'predicted': ','.join(sorted(pred)),
                                 'missing': ','.join(sorted(gold - pred)), 'human_approved': False,
                                 'review_status': 'pending; reference labels not automatically changed'})
    with (output/'oof_predictions.jsonl').open('w') as f:
        for r in data + extra:
            preds = original_predictions if r['record_id'] in byid else extra_predictions
            f.write(json.dumps({'record_id': r['record_id'], 'selected': sorted(preds[r['record_id']]), 'fold': next(f['fold'] for f in lineage if r['record_id'] in f['validation_ids'])})+'\n')
    # Support the documented direct script invocation as well as -m execution.
    if __package__:
        from .provenance import write_manifest
    else:
        from provenance import write_manifest
    write_manifest(output, files, lineage)
    print(json.dumps({k: v for k, v in result.items() if k != 'folds' and 'oof' not in k}, indent=2))


if __name__ == '__main__':
    main()
