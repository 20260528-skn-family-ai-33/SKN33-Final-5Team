import json
from unittest.mock import Mock

import pytest

# Training is an optional development extra, matching requirements/ml-svm56.txt.
pytest.importorskip("joblib")
pytest.importorskip("sklearn")
np = pytest.importorskip("numpy")

from ml.training.tune_candidate import choose_policies, groups_for
from ml.svm56.inference_adapter import ToolRecommender
from ml.svm56 import svm56_service_hook as hook
from ml.svm56.service_recommender_bridge import adapt_recommendation


def test_policy_cannot_purchase_recall_with_extra_known_false_positives():
    y = np.array([1]*20+[0]*100+[-1]*5)[:, None]
    base = np.array([.5]*19+[-.1]+[-.5]*100+[.9]*5)[:, None]
    weighted = np.array([.8]*20+[.8]+[-.5]*99+[.9]*5)[:, None]
    policy = choose_policies(y, [base, weighted])[0]
    assert policy['inner_tp'] >= policy['baseline_inner_tp']
    assert policy['inner_fp'] <= policy['baseline_inner_fp']
    assert policy['variant'] == 0  # weighted's extra true positive also buys a false positive


def test_sparse_tool_retains_default_instead_of_tuning_to_a_few_examples():
    y = np.array([1]*3+[0]*100)[:, None]
    base = np.full(y.shape, -.1)
    assert choose_policies(y, [base, -base])[0]['variant'] == 0
    assert choose_policies(y, [base, -base])[0]['threshold'] == 0.


def test_numeric_variants_and_lineage_stay_in_one_group():
    data = [{'record_id': 'a', 'query_ko': '금액 100 이상', 'source': {'group_id': 'origin'}},
            {'record_id': 'b', 'query_ko': '금액 200 이상'},
            {'record_id': 'c', 'query_ko': '별도 표현', 'source': {'group_id': 'origin'}}]
    assert len(set(groups_for(data))) == 1


@pytest.mark.parametrize('cutoffs', [[0.2], [True, 0.], [float('nan'), 0.]])
def test_bad_threshold_metadata_is_rejected_before_prediction(tmp_path, monkeypatch, cutoffs):
    monkeypatch.setattr('ml.svm56.inference_adapter.joblib.load', lambda p: {
        'labels': ['a', 'b'], 'heads': [None, None], 'threshold': 0., 'thresholds': cutoffs})
    catalog = tmp_path/'catalog.json'; catalog.write_text(json.dumps([{'id': 'a'}, {'id': 'b'}]))
    with pytest.raises(ValueError, match='thresholds'):
        ToolRecommender('unused', catalog)


def test_runtime_applies_tool_thresholds_instead_of_the_legacy_zero(tmp_path, monkeypatch):
    first, second = Mock(), Mock()
    first.decision_function.return_value = [.1]; second.decision_function.return_value = [.1]
    vectorizer = Mock()
    monkeypatch.setattr('ml.svm56.inference_adapter.joblib.load', lambda p: {
        'method': 'char_svm', 'vectorizer': vectorizer, 'labels': ['a', 'b'],
        'heads': [first, second], 'threshold': 0., 'thresholds': [.2, 0.]})
    catalog = tmp_path/'catalog.json'; catalog.write_text(json.dumps([{'id': 'a'}, {'id': 'b'}]))
    result = ToolRecommender('unused', catalog).predict('request')
    assert result['selected_tool_ids'] == ['b']
    assert result['ranked_tools'][0]['selection_threshold'] == .2


def test_explicit_candidate_hash_is_checked_before_deserialization(monkeypatch):
    hook._load_pinned.cache_clear()
    reader = Mock(side_effect=['c'*64, hook.CATALOG_SHA256])
    monkeypatch.setattr(hook, '_sha256', reader)
    loader = Mock(return_value='verified candidate')
    monkeypatch.setattr('ml.svm56.inference_adapter.ToolRecommender', loader)
    try:
        assert hook._load_pinned('candidate', 'catalog', 'c'*64) == 'verified candidate'
        loader.assert_called_once()
        with pytest.raises(ValueError):
            hook._load_pinned('candidate', 'catalog', 'not-a-digest')
    finally:
        hook._load_pinned.cache_clear()


def test_model_selection_and_reported_threshold_cannot_disagree():
    with pytest.raises(ValueError, match='threshold'):
        adapt_recommendation({'selected_tool_ids': ['a'], 'untrained_tool_ids': [],
                              'ranked_tools': [{'tool_id': 'a', 'decision_score': .1,
                                                'selection_threshold': .2, 'selected_by_threshold': True}]}, ['a'])
