import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "run-local-orchestration-reassessment.py"
spec = importlib.util.spec_from_file_location("local_orchestration_reassessment", SCRIPT)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def test_local_alias_uses_original_weights_and_frozen_settings(monkeypatch):
    frozen = host.read_json(host.ROOT / "docs/qwen-local-public9b-model.json")
    upstream = {"modelfile": f"FROM C:/models/sha256-{host.WEIGHTS}\n", "template": "same"}
    alias = {**upstream, "parameters": frozen["parameters"]}
    infos = {("/api/version", None): {"version": "test"},
             ("/api/tags", None): {"models": []}, ("/api/show", host.UPSTREAM): upstream,
             ("/api/show", host.ALIAS): alias}
    monkeypatch.setattr(host, "api", lambda endpoint, model=None: infos[endpoint, model])
    calls = []
    monkeypatch.setattr(host.subprocess, "run", lambda args, **kwargs: calls.append(args))
    host.prepare_model()
    assert calls[0][:3] == ["ollama", "create", host.ALIAS]
    infos["/api/tags", None]["models"] = [{"name": host.ALIAS + ":latest"}]
    alias["parameters"] = alias["parameters"].replace("32768", "2048")
    with pytest.raises(ValueError, match="parameters"):
        host.prepare_model()
    assert len(calls) == 1


def test_resume_retains_first_failure_and_runs_only_remaining_cases(tmp_path, monkeypatch):
    historical = host.read_json(host.ROOT / "docs/qwen-local-public9b-after-complete.json")
    corpus = {"gold_sha256": "synthetic", "cases": [{"id": "R01"}, {"id": "R02"}]}
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    hashes = tuple(historical[key]["source_sha256"] for key in ("code_manifest", "evaluation_harness_manifest"))
    monkeypatch.setattr(host, "OUTPUT", tmp_path)
    calls = []
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[0] != "node":
            return real_run(args, **kwargs)
        requested = [args[i + 1] for i, arg in enumerate(args) if arg == "--case"]
        calls.append(requested)
        first = len(calls) == 1
        row = dict(historical["cases"][len(calls)-1], status="failed" if first else "completed")
        raw = {**historical, "phase": "reassessment", "status": "incomplete" if first else "completed",
               "gold_sha256": "synthetic", "probe": {}, "cases": [row]}
        args[args.index("--output") + 1].write_text(json.dumps(raw), encoding="utf-8")

    monkeypatch.setattr(host.subprocess, "run", run)
    host.run_cohort("sample", corpus_path, [], hashes)
    host.run_cohort("sample", corpus_path, [], hashes)
    assert calls == [["R01", "R02"], ["R02"]]
    merged = host.read_json(tmp_path / "sample.complete.json")
    assert [row["status"] for row in merged["cases"]] == ["failed", "completed"]
    with pytest.raises(ValueError, match="Application or harness changed"):
        host.run_cohort("sample", corpus_path, [], ("different", hashes[1]))
