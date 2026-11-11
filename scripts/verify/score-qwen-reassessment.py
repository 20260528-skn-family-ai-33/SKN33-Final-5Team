"""Score a frozen original or holdout corpus, preserving unobserved and unreviewed results."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import runpy
from statistics import median

from qwen_live_support import digest

DIRECTORY = Path(__file__).resolve().parent
adjudicate = runpy.run_path(str(DIRECTORY / "adjudicate-qwen-live.py"))["score"]
ledger_score = runpy.run_path(str(DIRECTORY / "score-qwen-ledgers.py"))["summarize"]
reconcile = runpy.run_path(str(DIRECTORY / "reconcile-qwen-live-accounting.py"))["reconcile"]


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def score(run, corpus, labels=None, ledger_labels=None):
    sha = corpus["gold_sha256"]
    if sha != digest({k: v for k, v in corpus.items() if k != "gold_sha256"}):
        raise ValueError("Frozen corpus digest mismatch")
    if run.get("gold_sha256") != sha:
        raise ValueError("Run and corpus differ")
    cases = {case["id"]: case for case in corpus["cases"]}
    ids = [row["id"] for row in run["cases"]]
    if len(ids) != len(set(ids)) or set(ids) - cases.keys():
        raise ValueError("Unexpected or duplicate case ID in run")
    rows = [r for r in run["cases"] if r["status"] in ("completed", "failed")]
    measured = {r["id"] for r in rows}
    if run["status"] == "completed" and measured != cases.keys():
        # The runner also marks a selected-case shard completed. Preserve its scope.
        if run.get("requested_scenarios") != len(measured):
            raise ValueError("Completed report is missing requested cases")
    labels = labels if labels is not None else {"gold_sha256": sha, "cases": {}}
    if labels.get("gold_sha256") != sha or labels["cases"].keys() - measured:
        raise ValueError("Review refers to different gold or an unmeasured case")
    ledger_labels = ledger_labels if ledger_labels is not None else {"gold_sha256": sha, "cases": {}}
    ledger = ledger_score(rows, corpus, ledger_labels)
    counts = Counter()
    opportunities = Counter()
    for row in rows:
        case_id = row["id"]
        label = labels["cases"].get(case_id)
        if label is None:
            continue
        row_sha = labels.get("source_case_sha256", {}).get(case_id, label.get("source_case_sha256"))
        if row_sha != digest(row):
            raise ValueError(f"{case_id}: semantic review requires a matching source case digest")
        if type(label.get("whole_request_success")) is not bool:
            raise ValueError(f"{case_id}: whole-request verdict must be boolean")
        reviewed = adjudicate(dict(row), cases[case_id], label)
        counts["whole_request_success"] += label["whole_request_success"]
        opportunities["whole_request_success"] += 1
        for name, value in reviewed["adjudicated_metrics"].items():
            counts[name] += value["numerator"]
            opportunities[name] += value["denominator"]
        index = 0
        for i, turn in enumerate(row.get("turns", [])):
            response = turn["response"]
            questions = response.get("pending_questions") or (response.get("draft") or {}).get("questions", [])
            for _ in questions:
                judgment = label["questions"][index]
                if i > 0:
                    opportunities["unnecessary_repeated_followup_questions"] += 1
                    counts["unnecessary_repeated_followup_questions"] += bool(
                        judgment["unnecessary"] and judgment.get("repeated", False))
                index += 1
    validations = [stage["parsed_state"] for row in rows for stage in row.get("stages", [])
                   if stage["stage"] == "validate" and
                   (((stage["parsed_state"].get("draft") or {}).get("workflow") or {}).get("nodes"))]
    valid = sum(stage.get("status") == "ready" and not stage.get("issues") for stage in validations)
    schemas = [r["schema_valid"] for r in rows if type(r.get("schema_valid")) is bool]
    accounting = reconcile(run)
    times = sorted(r["elapsed_seconds"] for r in rows if type(r.get("elapsed_seconds")) in (float, int))
    per_model = {r["id"] for r in rows if r.get("live_model_calls", 0) > 0}
    rates = {name: ratio(counts[name], opportunities[name]) for name in
             ("whole_request_success", "necessary_question_recall", "unnecessary_repeated_followup_questions",
              "unnecessary_question_rate", "answer_incorporation_accuracy", "requirement_coverage")}
    rates.update(
        confirmed_condition_conflict=ledger["metrics"]["accepted_owner_conflict_rate"],
        duplicate_accepted_task_rate=ledger["metrics"]["accepted_duplicate_task_rate"],
        validator_pass_per_generated_draft=ratio(valid, len(validations)),
        final_draft_validation=ratio(sum(schemas), len(schemas)),
        verified_case_model_responses=(accounting["verified_returned_by_scope"].get("case", 0) if rows else None),
        verified_probe_model_responses=accounting["verified_returned_by_scope"].get("probe", 0),
        observed_case_model_call_records=sum(len(r.get("model_calls", [])) for r in rows) if rows else None,
        elapsed_seconds={"cases": len(times), "p50": median(times) if times else None,
                         "p95": times[math.ceil(.95 * len(times)) - 1] if times else None})
    return {"gold_sha256": sha, "status": run["status"], "frozen_cases": len(cases), "measured_cases": len(rows),
            "attempted_model_cases": len(per_model),
            "verified_response_cases": len(accounting["verified_case_ids"]),
            "backend_cases_without_model_dispatch": [r["id"] for r in rows if not r.get("live_model_calls", 0)],
            "missing_case_ids": [case_id for case_id in cases if case_id not in measured],
            "semantic_reviewed_cases": len(labels["cases"]), "ledger_reviewed_cases": len(ledger["reviewed_case_ids"]),
            "metrics": rates}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", required=True, type=Path)
    p.add_argument("--run", type=Path, help="Omit only to report a cohort before running it")
    p.add_argument("--labels", type=Path)
    p.add_argument("--ledger-labels", type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    paths = {"corpus": args.corpus, "run": args.run, "labels": args.labels, "ledger_labels": args.ledger_labels}
    raw = {k: path.read_bytes() for k, path in paths.items() if path is not None}
    data = {k: json.loads(v) for k, v in raw.items()}
    if "run" in raw and (expected := data.get("labels", {}).get("source_run_sha256")):
        if expected != hashlib.sha256(raw["run"]).hexdigest():
            raise ValueError("Semantic review is tied to another run file")
    corpus = data["corpus"]
    run = data.get("run", {"status": "not_run", "gold_sha256": corpus["gold_sha256"], "cases": []})
    result = score(run, corpus, data.get("labels"), data.get("ledger_labels"))
    result["source_bytes_sha256"] = {k: hashlib.sha256(v).hexdigest() for k, v in raw.items()}
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()
