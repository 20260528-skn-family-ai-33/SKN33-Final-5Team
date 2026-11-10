"""Read-only comparison of completed, manually reviewed local evaluation runs.

This postprocessor is outside the frozen inference harness. It neither calls a
model nor changes gold, raw runs, review labels, or existing output files.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from qwen_live_support import digest

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(run, review, gold, accounting):
    assert run["status"] == "completed", "Incomplete runs cannot enter this comparison"
    rows = {row["id"]: row for row in run["cases"]}
    labels = review["cases"]
    assert len(rows) == len(run["cases"]), "Duplicate measured IDs"
    assert set(rows) == set(labels) == set(gold), "Require the entire frozen corpus"
    assert not accounting["live_filter_omitted_case_ids"], "Original metric live filter is incomplete"
    live = set(accounting["verified_case_ids"])
    assert live == {key for key, row in rows.items() if row.get("live_model_calls", 0) > 0}
    for key, row in rows.items():
        assert review["source_case_sha256"][key] == digest(row), key
        assert isinstance(labels[key]["whole_request_success"], bool), key

    def success(ids):
        ids = list(ids)
        passed = [key for key in ids if labels[key]["whole_request_success"]]
        return {"numerator": len(passed), "denominator": len(ids), "successful_ids": passed}

    classes = ("READY", "CLARIFY", "UNSUPPORTED")
    confusion = {expected: dict(Counter(rows[key]["actual_route"] for key in live
                                      if gold[key]["gold"]["routing"] == expected))
                 for expected in classes}
    by_class = {}
    for route in classes:
        tp = sum(gold[key]["gold"]["routing"] == route and rows[key]["actual_route"] == route for key in live)
        fp = sum(gold[key]["gold"]["routing"] != route and rows[key]["actual_route"] == route for key in live)
        fn = sum(gold[key]["gold"]["routing"] == route and rows[key]["actual_route"] != route for key in live)
        by_class[route] = {"tp": tp, "fp": fp, "fn": fn, "f1": 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0}

    calls = [call for row in rows.values() for call in row.get("model_calls", [])]
    usages = [call["usage"] for call in calls if call.get("usage")]
    execution_targets = [key for key in rows if gold[key]["gold"]["execution"]["mode"] == "local_synthetic"]
    final_ready_targets = [key for key in rows if gold[key]["gold"]["final_routing"] == "READY"]
    schema = lambda ids: {"valid": [key for key in ids if rows[key].get("schema_valid") is True],
                          "invalid": [key for key in ids if rows[key].get("schema_valid") is False],
                          "null": [key for key in ids if rows[key].get("schema_valid") is None]}
    return {
        "measured": len(rows), "worker_status_counts": dict(Counter(row["status"] for row in rows.values())),
        "model_cases": len(live), "code_only_ids": [key for key in rows if key not in live],
        "whole_request_success": success(rows), "whole_request_success_live": success(key for key in rows if key in live),
        "by_category": {category: success(key for key in rows if gold[key]["category"] == category)
                        for category in dict.fromkeys(gold[key]["category"] for key in rows)},
        "by_split": {split: success(key for key in rows if gold[key]["split"] == split) for split in ("development", "holdout")},
        "final_ready_target_whole_request_success": success(final_ready_targets),
        "final_ready_target_whole_request_success_live": success(key for key in final_ready_targets if key in live),
        "initial_routing_confusion_live": confusion, "initial_routing_per_class_live": by_class,
        "schema_final_draft_all": schema(rows), "schema_final_draft_live": schema([key for key in rows if key in live]),
        "execution_eligible_ids": execution_targets,
        "execution_attempted_ids": [key for key in rows if isinstance(rows[key].get("execution_success"), bool)],
        "execution_success_ids": [key for key in rows if rows[key].get("execution_success") is True],
        "failed_whole_request_ids": [key for key in rows if not labels[key]["whole_request_success"]],
        "worker_failed_ids": [key for key in rows if rows[key]["status"] == "failed"],
        "confirmed_returned_responses_including_probe": accounting["verified_returned_responses"],
        "confirmed_returned_response_scope": accounting["verified_returned_by_scope"],
        "recorded_usage_case_calls": len(usages),
        "recorded_prompt_tokens_subset": sum(u.get("prompt_eval_count", 0) for u in usages),
        "recorded_output_tokens_subset": sum(u.get("eval_count", 0) for u in usages),
        "case_calls_without_usage": len(calls)-len(usages),
        "usage_scope": "Recorded native case responses only; excludes probe and SDK usage gaps. Not total server usage or cost.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite existing comparison")
    corpus = read(ROOT / "docs/qwen-live-evaluation-cases.json")
    gold = {c["id"]: c for c in corpus["cases"] if c.get("eligible", True)}
    runs, reviews, result, hashes = {}, {}, {}, {}
    for phase, stem in (("baseline", "qwen-local-public9b-baseline-v2"), ("after", "qwen-local-public9b-after")):
        paths = {kind: ROOT / "docs" / f"{stem}{'-complete' if phase == 'after' and kind == 'run' else suffix}.json"
                 for kind, suffix in (("run", ""), ("review", "-review"), ("accounting", "-accounting"))}
        values = {kind: read(path) for kind, path in paths.items()}
        runs[phase], reviews[phase] = values["run"], values["review"]
        assert values["run"]["gold_sha256"] == values["review"]["gold_sha256"] == corpus["gold_sha256"]
        raw_sha = sha256(paths["run"].read_bytes()).hexdigest()
        assert values["review"]["source_run_sha256"] == raw_sha, "Review belongs to another raw run"
        assert values["accounting"]["source_run_sha256"] == raw_sha, "Accounting belongs to another raw run"
        assert values["accounting"]["source_status"] == values["run"]["status"]
        assert values["accounting"]["gold_sha256"] == values["run"]["gold_sha256"]
        assert values["accounting"]["source_harness_sha256"] == values["run"]["evaluation_harness_manifest"]["source_sha256"]
        result[phase] = summarize(values["run"], values["review"], gold, values["accounting"])
        hashes[phase] = {kind: {"path": str(path.relative_to(ROOT)), "bytes_sha256": sha256(path.read_bytes()).hexdigest()}
                         for kind, path in paths.items()}
    for key in ("gold_sha256", "evaluation_environment", "effective_process_settings", "evaluation_harness_manifest"):
        assert runs["baseline"][key] == runs["after"][key], f"Comparison mismatch: {key}"
    before, after = (reviews[p]["cases"] for p in ("baseline", "after"))
    result["transitions"] = {
        "improved_ids": [key for key in gold if not before[key]["whole_request_success"] and after[key]["whole_request_success"]],
        "regressed_ids": [key for key in gold if before[key]["whole_request_success"] and not after[key]["whole_request_success"]],
        "unchanged_success_ids": [key for key in gold if before[key]["whole_request_success"] and after[key]["whole_request_success"]],
        "unchanged_failure_ids": [key for key in gold if not before[key]["whole_request_success"] and not after[key]["whole_request_success"]],
    }
    result["artifact_byte_hashes"] = hashes
    result["comparison_scope"] = "Whole fixed local corpus; after repeat and abandoned/RunPod runs excluded. Labels are trace review, not independent human assessment."
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "transitions": result["transitions"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
