"""Summarize retained Qwen calls and graph timings for the same named cases in each run."""
import argparse
from collections import Counter, defaultdict
import json
from math import ceil
from pathlib import Path
from statistics import median


def token_usage(call):
    """One HTTP result is one sample even if the SDK repeats its usage metadata."""
    usage = call.get("usage") or {}
    if usage.get("prompt_eval_count") is not None and usage.get("eval_count") is not None:
        return usage["prompt_eval_count"], usage["eval_count"]
    raw = call.get("raw_output")
    if isinstance(raw, list) and len(raw) == 1:
        message = raw[0].get("usage_metadata") or {}
        if message.get("input_tokens") is not None and message.get("output_tokens") is not None:
            return message["input_tokens"], message["output_tokens"]
    usage = usage.get("token_usage", usage)
    if usage.get("prompt_tokens") is not None and usage.get("completion_tokens") is not None:
        return usage["prompt_tokens"], usage["completion_tokens"]
    return None


def meaning_kind(call):
    raw = call.get("raw_output") or {}
    try:
        packet = json.loads(raw["content"])
    except (KeyError, TypeError, ValueError):
        return "unclassified"
    if isinstance(packet, dict):
        if "faithful" in packet:
            return "review"
        if "tasks" in packet:
            return "extraction"
    return "unclassified"


def requirement_cache(stage):
    before = stage.get("ledger_before", {})
    after = stage.get("ledger_after", {})
    if not before.get("requirement_ledger"):
        return "no_prior_ledger"
    if "requirement_fingerprint" not in after or after["requirement_fingerprint"] is None:
        return "cleared_or_unavailable"
    if before.get("requirement_fingerprint") != after["requirement_fingerprint"]:
        return "input_fingerprint_changed"
    if before.get("requirement_digest") != after.get("requirement_digest"):
        return "ledger_digest_changed"
    if "requirement_ledger" not in stage["parsed_state"] and not stage["model_call_indices"]:
        # Same checkpoint survived and the requirement node returned its route only.
        return "reused_unchanged"
    return "unchanged_or_reprojected"


def add_timing(stats, record, prefix=""):
    for field in ("worker_cpu_seconds", "snapshot_wall_seconds", "snapshot_cpu_seconds"):
        if field in record:
            stats[prefix + field] += record[field]
            stats[prefix + field + "_samples"] += 1


def add_call(stats, call):
    stats["records"] += 1
    stats["dispatches"] += call["http_dispatch_attempts"]
    stats["responses"] += int(call["model_response_observed"])
    stats["call_seconds"] += call.get("elapsed_seconds", 0)
    add_timing(stats, call, "call_")
    usage = call.get("usage") or {}
    for field in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration"):
        if usage.get(field) is not None:
            name = "server_" + field + "_seconds"
            stats[name] += usage[field] / 1e9
            stats[name + "_samples"] += 1
    tokens = token_usage(call)
    if tokens is not None:
        stats["input_tokens"] += tokens[0]
        stats["output_tokens"] += tokens[1]
        stats["with_tokens"] += 1


def summarize(run, requested=()):
    if run.get("status") != "completed":
        raise ValueError("Use a completed trace for a cohort comparison")
    cases = [c for c in run["cases"] if not requested or c["id"] in requested]
    if len(cases) != len({c["id"] for c in cases}) or set(requested) - {c["id"] for c in cases}:
        raise ValueError("Selected cases must each occur once in this trace")
    if not cases or any(c["status"] not in ("completed", "failed") for c in cases):
        raise ValueError("Selected cases must have terminal records")
    stages = defaultdict(Counter)
    meaning = defaultdict(Counter)
    caches = Counter()
    capability = Counter()
    case_timing = Counter()
    user_turns = []
    latencies = []
    for case in cases:
        latencies.append(case["elapsed_seconds"])
        add_timing(case_timing, case)
        for attempt in case.get("turn_attempts", []):
            # Older traces have graph timings but no end-to-end clock per message.
            user_turns.append({"case_id": case["id"], "turn_index": attempt["turn_index"],
                               "status": attempt["status"], "response_route": attempt.get("response_route"),
                               "elapsed_seconds": attempt.get("elapsed_seconds")})
        seen_generation_turns = set()
        calls = case["model_calls"]
        labels = {}
        for stage in case["stages"]:
            name = stage["stage"]
            if name == "conversation":
                turn = stage.get("turn_index")
                name = "repair" if turn in seen_generation_turns else "generation"
                seen_generation_turns.add(turn)
            if name == "requirements":
                caches[requirement_cache(stage)] += 1
            if name == "capability_check":
                assessment = stage["parsed_state"].get("capability_assessment") or {}
                outcome = assessment.get("answer_resolution")
                if not outcome:
                    outcome = "model" if stage["model_call_indices"] else "code_or_unavailable"
                capability[outcome] += 1
            stages[name]["visits"] += 1
            stages[name]["stage_seconds"] += stage["elapsed_seconds"]
            add_timing(stages[name], stage)
            for index in stage["model_call_indices"]:
                labels[index - case["model_call_offset"]] = name
        for index, call in enumerate(calls):
            name = labels.get(index, call["stage"])
            add_call(stages[name], call)
            if name == "request_meaning":
                add_call(meaning[meaning_kind(call)], call)
    return {
        "case_ids": [c["id"] for c in cases], "cases": len(cases),
        "p50_seconds": median(latencies), "p95_seconds": sorted(latencies)[ceil(.95 * len(latencies)) - 1],
        "case_seconds": sum(latencies), "model_dispatches": sum(s["dispatches"] for s in stages.values()),
        "case_timing": case_timing, "user_turns": user_turns,
        "stages": stages, "meaning_calls_by_response": meaning,
        "requirement_cache_observations": caches, "capability_observations": capability,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(summarize(json.loads(args.trace.read_bytes()), args.case), indent=2))
