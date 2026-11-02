"""Compare saved OOF predictions without reusing their labels for training."""
import argparse
import csv
import json
from pathlib import Path

from ml.training.train_candidate import read, rows, digest, DATA_SHA, CATALOG_SHA
from ml.training.tune_candidate import comparison
from ml.training.provenance import load_verified_predictions


def checked_predictions(records, data_ids, labels, field):
    seen = set(); predictions = {}
    for row in records:
        identifier = row['record_id']
        if identifier in seen:
            raise ValueError('Duplicate prediction ID')
        seen.add(identifier)
        chosen = row[field]
        if not isinstance(chosen, list) or len(set(chosen)) != len(chosen) or set(chosen)-set(labels):
            raise ValueError('Invalid selected labels')
        if identifier in data_ids: predictions[identifier] = set(chosen)
    if set(predictions) != data_ids:
        raise ValueError('Missing original OOF predictions')
    return predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='Full 2026-10-31 reference package')
    parser.add_argument('--candidate', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); base = args.baseline.resolve(); output = args.output.resolve()
    if output.exists() or base == output or base in output.parents:
        parser.error('Use a new output directory outside the reference package')
    package = base/'08_서비스연동패키지'
    df, cf = package/'data/labelled_training_queries.jsonl', package/'catalog/catalog_56.json'
    if digest(df) != DATA_SHA or digest(cf) != CATALOG_SHA: raise ValueError('Reference hash mismatch')
    data = rows(df); ids = {r['record_id'] for r in data}; labels = [r['id'] for r in read(cf)]
    raw = rows(base/'04_ML_학습재현/기록된결과/results/methods/oof_predictions.jsonl')
    baseline = checked_predictions([r for r in raw if r['method']=='char_svm'], ids, labels,
                                   'selected_tool_ids_threshold_0')
    output.mkdir(parents=True)
    experiments = [('baseline', baseline)]
    for directory in args.candidate:
        experiments.append((directory.name, load_verified_predictions(directory,
            {'data': df, 'catalog': cf, 'splits': package/'evaluation/SPLITS.json'},
            read(package/'evaluation/SPLITS.json'), ids, labels)))
    results = {}
    for name, predictions in experiments:
        if name in results: raise ValueError('Duplicate experiment name')
        stats = comparison(data, [predictions[r['record_id']] for r in data], labels)
        stats['empty_predictions'] = sum(not p for p in predictions.values())
        stats['unknown_recommended'] = sum(sum(r['labels_1_0_minus1_draft'][t] == -1
                                              for t in predictions[r['record_id']]) for r in data)
        results[name] = stats
        with (output/(name+'-errors.csv')).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['record_id','query','false_positive_tools','missing_known_positive_tools',
                                                   'unknown_recommended_tools','complete_labels','human_approved'])
            writer.writeheader()
            for row in data:
                y = row['labels_1_0_minus1_draft']; chosen = predictions[row['record_id']]
                fp = sorted(t for t in chosen if y[t] == 0)
                fn = sorted(t for t in labels if y[t] == 1 and t not in chosen)
                if fp or fn:
                    writer.writerow({'record_id':row['record_id'],'query':row['query_ko'],
                                     'false_positive_tools':','.join(fp),'missing_known_positive_tools':','.join(fn),
                                     'unknown_recommended_tools':','.join(sorted(t for t in chosen if y[t] == -1)),
                                     'complete_labels':all(y[t] != -1 for t in labels),'human_approved':False})
    (output/'RESULTS.json').write_text(json.dumps({'scope':'Previously inspected development corpus; not independent test',
                                                 'human_gold':False,'results':results}, ensure_ascii=False,indent=2))
    for name, stats in results.items():
        print(name, {k:stats[k] for k in ('tp','fp','fn','masked_micro_f1','precision','recall','compound_coverage')})


if __name__ == '__main__': main()
