"""Check the frozen semantic outcomes of the four performance probes without executing a workflow."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Backend"))
from qwen_live_support import digest
from agent_builder.design.reader_sources import reader_sources
from workflow_engine.catalog.canonical import parse_arguments
from workflow_engine.execution_path import artifact_origin, artifact_output_ids, execution_path


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def linked(node, parent, input_name, output_name):
    return {"input": input_name, "output": output_name, "node_id": parent["id"]} in node["bindings"]


def graph_for(turn, expected_tools):
    graph = turn["response"]["draft"]["workflow"]
    path = execution_path(graph)
    counts = Counter(n["tool"] for n in path)
    if counts.get("finish") == 1:
        counts.pop("finish")
    require(len(path) == len(graph["nodes"]) and counts == Counter(expected_tools),
            "unexpected workflow tools, duplicate actions or unreachable node")
    return graph, {node["tool"]: node for node in path}


def table_result(row, gold):
    turn = row["turns"][-1]
    _, nodes = graph_for(turn, ["read_table", "filter_sort", "write_result"])
    source, select, writer = (nodes[t] for t in ("read_table", "filter_sort", "write_result"))
    draft = turn["response"]["draft"]
    assets = gold["resource_state"]["resources"]
    require(reader_sources(source, assets, draft["document_ids"]) == {r["id"] for r in assets}, "table source")
    plan = select["settings"].get("execution_plan") or draft["execution_plan"]
    slots = gold["gold"]["final_slots"]
    require(linked(select, source, "table", "table") and linked(writer, select, "table", "table"), "table lineage")
    require(writer["settings"]["format"] == slots["output.format"] and plan["missing_policy"] == "error", "table policy or output format")
    if "filter.field" in slots:
        filters = plan["filters"]
        require(len(filters) == 1 and all(filters[0][k] == slots["filter." + k] for k in ("field", "operator", "value"))
                and plan["duplicate_policy"] == "keep" and not plan["sort"], "filter or row order")
    else:
        require(not plan["filters"] and not plan["sort"] and plan["duplicate_policy"] == slots["duplicate.policy"]
                and plan["duplicate_field"] == slots["duplicate.field"], "deduplication operation")
    local = row.get("local_execution") or {}
    require(row["execution_success"] is True and local.get("rows") == gold["gold"]["execution"]["expected_rows"]
            and local.get("report_path", "").lower().endswith(".csv"), "synthetic CSV rows or format")


def gmail_revision(row, gold):
    contract = gold["gold"]["review_contract"]["initial_and_final_date_window"]
    window = {f"after:{contract['start_inclusive_epoch']}", f"before:{contract['end_exclusive_epoch']}"}
    for index, turn in enumerate(row["turns"]):
        _, nodes = graph_for(turn, ["email.list", "compose_answer"])
        reader = nodes["email.list"]
        require(reader["settings"]["operation"] == "read_messages" and linked(nodes["compose_answer"], reader, "documents", "documents"), "mail source or summary input")
        args = parse_arguments(reader["settings"])
        terms = args["query"].split()
        require(set(terms) == window | {"-in:sent", "-in:drafts"} and len(terms) == 4, "local calendar window or added filter")
        if index == 1:
            require(args["max_results"] == gold["gold"]["final_slots"]["gmail.limit"], "revised mail limit")
        else:
            require(args["max_results"] == 100, "initial service page limit")


def summary_delivery(row, gold):
    graph, nodes = graph_for(row["turns"][-1], ["read_document", "compose_answer", "export_result", "email.send"])
    source, summary, writer, send = (nodes[t] for t in ("read_document", "compose_answer", "export_result", "email.send"))
    require(source["settings"]["file_ids"] == [a["id"] for a in gold["resource_state"]["resources"]]
            and linked(summary, source, "documents", "documents"), "wrong summary source")
    origins = {artifact_origin(graph, item) for item in artifact_output_ids(graph)}
    require(origins == {writer["id"]} and writer["settings"]["format"] == "markdown" and len(writer["bindings"]) == 1
            and any(linked(writer, summary, port, "text") for port in ("text", "answer")), "Markdown did not consume summary text")
    require(send["settings"]["operation"] == "execute" and len(send["bindings"]) == 1
            and linked(send, summary, "body", "text"), "mail did not consume the same summary")
    args = parse_arguments(send["settings"])
    slots = gold["gold"]["final_slots"]
    require(set(args) == {"to", "subject", "body"} and args["to"] in (slots["recipient"], [slots["recipient"]])
            and args["subject"] == slots["subject"] and args["body"] == {"$binding": "body"}, "delivery arguments")


def check(row, gold):
    require(row["status"] == "completed" and row["actual_route"] == gold["gold"]["routing"]
            and row.get("final_route") == gold["gold"]["final_routing"], "initial or final route")
    require(row["schema_valid"] and not row["validation"]["issues"], "validator")
    require(row["external_execution"] == "NOT_RUN", "external execution")
    events = [entry for stage in row["stages"] if stage["stage"] == "validate"
              for entry in stage["parsed_state"].get("trace", []) if entry.get("node") == "requirement_coverage"]
    require(events and events[-1]["status"] == "completed" and events[-1].get("tasks")
            and all(t["status"] == "covered" for t in events[-1]["tasks"]), "requirement coverage")
    turns = row["turns"]
    require(len(turns) == (2 if row["id"] in ("H20", "H21") else 1), "missing or extra turn")
    for i, turn in enumerate(turns):
        questions = turn["response"].get("pending_questions", [])
        if row["id"] == "H21" and i == 0:
            require(len(questions) == 1 and questions[0]["key"] == "duplicate.policy"
                    and questions[0]["id"] == turns[1]["question_id"], "duplicate policy question or selected ID")
            slots = (turn["response"].get("condition_ledger") or {}).get("slots", {})
            require(slots.get("duplicate.policy", {}).get("status") != "specified", "policy prefilled before answer")
        else:
            require(not questions and not turn["response"]["draft"].get("questions"), "unrequested clarification")
    if gold["gold"]["execution"]["mode"] != "local_synthetic":
        require(row.get("execution_success") is None and "local_execution" not in row, "connector workflow executed")
    {"H01": table_result, "H21": table_result, "H20": gmail_revision, "H32": summary_delivery}[row["id"]](row, gold)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--corpus", type=Path, default=ROOT/"docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json")
    parser.add_argument("--case", choices=["H01","H20","H21","H32"], action="append", required=True)
    args = parser.parse_args()
    data = json.loads(args.run.read_bytes())
    corpus = json.loads(args.corpus.read_bytes())
    gold_sha = digest({k:v for k,v in corpus.items() if k != "gold_sha256"})
    if gold_sha != corpus["gold_sha256"] or data["gold_sha256"] != gold_sha or data["status"] != "completed":
        raise ValueError("Completed run and frozen corpus must match")
    cases = {c["id"]: c for c in data["cases"]}
    frozen = {c["id"]: c for c in corpus["cases"]}
    if len(cases) != len(data["cases"]) or len(args.case) != len(set(args.case)) or set(args.case) - cases.keys():
        raise ValueError("Missing requested case or duplicate trace")
    results = {}
    for cid in args.case:
        try:
            check(cases[cid], frozen[cid])
        except (KeyError, TypeError, ValueError) as error:
            results[cid] = str(error)
        else:
            results[cid] = "pass"
    print(json.dumps(results, ensure_ascii=False))
    sys.exit(int(any(result != "pass" for result in results.values())))
