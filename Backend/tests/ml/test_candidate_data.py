import json
from pathlib import Path

import pytest

# Training is an optional development extra, matching requirements/ml-svm56.txt.
pytest.importorskip("joblib")
pytest.importorskip("sklearn")
np = pytest.importorskip("numpy")

from ml.training.train_candidate import supplement, validate_supplement, fit


def test_supplement_keeps_unreviewed_labels_unknown_and_groups_together():
    cases = json.loads((Path(__file__).parents[2]/'ml/training/supplemental_cases.json').read_text())
    labels = sorted({t for c in cases for t in c['positive']}) + ['unrelated_tool']
    data = supplement(labels)
    assert len(data) == 48
    groups = {}
    for row in data:
        assert not row['human_approved'] and not row['execution_verified']
        assert row['labels_1_0_minus1_draft']['unrelated_tool'] == -1
        assert set(row['labels_1_0_minus1_draft'].values()) <= {-1, 0, 1}
        if row['labels_1_0_minus1_draft'].get('review_summary') == 1:
            assert row['labels_1_0_minus1_draft']['reply_review'] == 0
        groups.setdefault(row['source']['group_id'], set()).add(row['development_fold'])
    assert len(groups) == 24 and all(len(folds) == 1 for folds in groups.values())
    validate_supplement([], data)


def test_numeric_duplicate_of_original_cannot_be_added_as_a_fresh_group():
    with pytest.raises(ValueError, match='original request'):
        validate_supplement([{'query_ko': '금액이 1000 이상인 행'}], [
            {'query_ko': '금액이 2000 이상인 행', 'source': {'group_id': 'new'}, 'development_fold': 1}])


def test_duplicate_supplement_groups_cannot_cross_folds():
    with pytest.raises(ValueError, match='crosses source groups'):
        validate_supplement([], [
            {'query_ko': '거래를 요약해줘', 'source': {'group_id': 'a'}, 'development_fold': 1},
            {'query_ko': '거래를 요약해줘', 'source': {'group_id': 'b'}, 'development_fold': 2}])


def test_training_weights_only_known_compound_positives_and_masks_unknown(monkeypatch):
    from unittest.mock import Mock
    import numpy as np
    factory = Mock(side_effect=[Mock(), Mock()])
    monkeypatch.setattr('ml.training.train_candidate.LinearSVC', factory)
    data = [{'query_ko': query, 'labels_1_0_minus1_draft': {'one': a, 'two': b}}
            for query, a, b in [('문서 검색 추출', 1, 1), ('검색만', 1, -1),
                                ('문자 변환', 0, 0), ('아직 확인 안 된 라벨', -1, 1)]]
    result = fit(data, ['one', 'two'], compound_positive_weight=2.)
    call = result['heads'][0].fit.call_args
    np.testing.assert_array_equal(call.args[1], [1, 1, 0])
    np.testing.assert_array_equal(call.kwargs['sample_weight'], [2., 1., 1.])
    assert data[-1]['labels_1_0_minus1_draft']['one'] == -1
