import json
from pathlib import Path
import runpy
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/verify"))
score = runpy.run_path(str(ROOT / "scripts/verify/score-qwen-reassessment.py"))["score"]


def read(path):
    return json.loads((ROOT / path).read_bytes())


def test_untested_holdout_does_not_turn_missing_measurements_into_successes():
    corpus = read("docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json")
    result = score({"gold_sha256": corpus["gold_sha256"], "cases": [], "status": "not_run"}, corpus)
    assert result["frozen_cases"] == len(result["missing_case_ids"]) == 35
    assert result["measured_cases"] == 0
    metrics = result["metrics"]
    for name in ("whole_request_success", "confirmed_condition_conflict", "duplicate_accepted_task_rate",
                 "validator_pass_per_generated_draft"):
        assert metrics[name] == {"numerator": 0, "denominator": 0, "value": None}
    assert metrics["verified_case_model_responses"] is None
    assert metrics["elapsed_seconds"]["p50"] is None


def test_historical_raw_and_review_reproduce_different_attempt_and_case_denominators():
    corpus = read("docs/qwen-live-evaluation-cases.json")
    run = read("docs/qwen-local-public9b-after-complete.json")
    labels = read("docs/qwen-local-public9b-after-review.json")
    result = score(run, corpus, labels)
    assert result["measured_cases"] == result["semantic_reviewed_cases"] == 52
    assert result["verified_response_cases"] == 50
    assert result["metrics"]["whole_request_success"]["numerator"] == 28
    assert result["metrics"]["validator_pass_per_generated_draft"]["denominator"] == 38
    assert result["metrics"]["final_draft_validation"]["denominator"] == 27
    assert result["metrics"]["verified_case_model_responses"] == 191
    assert result["metrics"]["unnecessary_repeated_followup_questions"]["numerator"] == 2
    labels["source_case_sha256"]["R01"] = "another raw trace"
    with pytest.raises(ValueError, match="source case digest"):
        score(run, corpus, labels)
