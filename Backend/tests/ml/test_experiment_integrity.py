import json
import sys

import pytest

pytest.importorskip('joblib')
pytest.importorskip('sklearn')
pytest.importorskip('numpy')

from ml.training import train_candidate as trainer
from ml.training.provenance import load_verified_predictions, sha


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    package = tmp_path/'source'
    for folder in ['data', 'catalog', 'evaluation']:
        (package/folder).mkdir(parents=True)
    data = [{'record_id': str(i), 'query_ko': q, 'labels_1_0_minus1_draft': {'one': i % 2}}
            for i, q in enumerate(['문서 원문', '거래 집계', '지침 원문', '금액 집계', '정책 원문', '주문 집계', '규정 원문', '매출 집계'])]
    files = {'data': package/'data/labelled_training_queries.jsonl', 'catalog': package/'catalog/catalog_56.json',
             'splits': package/'evaluation/SPLITS.json'}
    files['data'].write_text(''.join(json.dumps(r)+'\n' for r in data))
    files['catalog'].write_text(json.dumps([{'id': 'one'}]))
    splits = [{'fold': n+1, 'train_ids': [str(i) for i in range(8) if i//4 != n],
               'development_ids': [str(i) for i in range(8) if i//4 == n]} for n in range(2)]
    files['splits'].write_text(json.dumps(splits))
    for key, name in [('data', 'DATA_SHA'), ('catalog', 'CATALOG_SHA'), ('splits', 'SPLITS_SHA')]:
        monkeypatch.setattr(trainer, name, sha(files[key]))
    monkeypatch.setattr(trainer, 'supplement', lambda labels: [])
    output = tmp_path/'experiment'
    monkeypatch.setattr(sys, 'argv', ['train', '--baseline', str(package), '--output', str(output)])
    return package, output, files, splits, {r['record_id'] for r in data}


def test_real_training_roundtrip_and_existing_run_cannot_be_overwritten(experiment):
    package, output, files, splits, ids = experiment
    trainer.main()
    predictions = load_verified_predictions(output, files, splits, ids, ['one'])
    assert set(predictions) == ids
    before = {p.name: sha(p) for p in output.iterdir()}
    with pytest.raises(SystemExit):
        trainer.main()
    assert before == {p.name: sha(p) for p in output.iterdir()}


@pytest.mark.parametrize('damage', ['predictions', 'source', 'lineage', 'completion', 'fabricated_oof'])
def test_mixed_corrupt_or_non_held_out_artifacts_are_rejected(experiment, damage):
    _, output, files, splits, ids = experiment
    trainer.main()
    if damage == 'predictions':
        (output/'oof_predictions.jsonl').write_text('{}\n')
    elif damage == 'source':
        files['data'].write_text('changed')
    elif damage == 'lineage':
        path = output/'COMPLETE.json'; manifest = json.loads(path.read_text())
        manifest['folds'][0]['train_ids'].append(manifest['folds'][0]['validation_ids'][0])
        path.write_text(json.dumps(manifest))
    elif damage == 'fabricated_oof':
        path = output/'oof_predictions.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]['selected'] = [] if rows[0]['selected'] else ['one']
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        manifest_path = output/'COMPLETE.json'; manifest = json.loads(manifest_path.read_text())
        manifest['artifacts']['oof_predictions.jsonl'] = sha(path)
        manifest_path.write_text(json.dumps(manifest))
    else:
        (output/'COMPLETE.json').unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        load_verified_predictions(output, files, splits, ids, ['one'])


def test_failed_training_never_publishes_completed_experiment(experiment, monkeypatch):
    _, output, files, splits, ids = experiment
    monkeypatch.setattr(trainer, 'fit', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('interrupted')))
    with pytest.raises(RuntimeError, match='interrupted'):
        trainer.main()
    assert not (output/'COMPLETE.json').exists()
    with pytest.raises(FileNotFoundError):
        load_verified_predictions(output, files, splits, ids, ['one'])
