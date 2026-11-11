"""Run or resume the frozen 52 + 35 public Qwen cases on the Windows Ollama host."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
VERIFY = Path(__file__).resolve().parent
OUTPUT = ROOT / "docs/local-public9b-rebuild-20261111"
HOST = "http://127.0.0.1:11434"
UPSTREAM = "qwen3.5:9b"
ALIAS = "a4b-public-qwen35-9b-32k"
WEIGHTS = "02d45dc1cf451ba2475ac33b301c2dd8f985abe4c182ce04a1f2f5bf0260278d"
HOLDOUT_SHA = "93c821ded55570fda569ceb983f22fd2ebf8d6b8c7d395d445e68e4931bc7e35"
COHORTS = (
    ("legacy52", ROOT / "docs/qwen-live-evaluation-cases.json", []),
    ("independent35", ROOT / "docs/runpod-custom-rebuild-20261111/independent-evaluation-cases.json",
     ["--corpus-profile", "runpod-rebuild", "--expected-gold-sha", HOLDOUT_SHA]),
)


def read_json(path):
    return json.loads(path.read_bytes())


def api(endpoint, model=None):
    body = json.dumps({"model": model}).encode() if model else None
    req = urllib.request.Request(HOST + endpoint, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=15) as response:
        return json.load(response)


def check_weights(info):
    source = re.search(r"^FROM\s+(.+)$", info["modelfile"], re.MULTILINE | re.IGNORECASE)
    if not source or not source.group(1).strip().lower().endswith(WEIGHTS):
        raise ValueError("Local Ollama model source differs from the frozen public 9B GGUF.")


def prepare_model():
    try:
        version = api("/api/version")["version"]
    except urllib.error.URLError as error:
        if not isinstance(error.reason, ConnectionRefusedError):
            raise
        subprocess.Popen(["ollama", "serve"], env={**os.environ, "OLLAMA_HOST": HOST},
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        for attempt in range(20):
            time.sleep(1)
            try:
                version = api("/api/version")["version"]
                break
            except urllib.error.URLError as retry_error:
                if not isinstance(retry_error.reason, ConnectionRefusedError) or attempt == 19:
                    raise
    tags = {m["name"]: m for m in api("/api/tags")["models"]}
    upstream = api("/api/show", UPSTREAM)
    check_weights(upstream)
    if ALIAS + ":latest" not in tags:
        subprocess.run(["ollama", "create", ALIAS, "-f", str(VERIFY / "Modelfile.a4b-public-qwen35-9b-32k")],
                       env={**os.environ, "OLLAMA_HOST": HOST}, cwd=ROOT, check=True)
    alias = api("/api/show", ALIAS)
    check_weights(alias)
    frozen = read_json(ROOT / "docs/qwen-local-public9b-model.json")

    def parameters(value):
        return dict(line.split(maxsplit=1) for line in value.splitlines() if line.strip())

    if (parameters(alias["parameters"]) != parameters(frozen["parameters"])
            or alias.get("template") != upstream.get("template")
            or alias.get("system") != upstream.get("system")
            or alias.get("messages") != upstream.get("messages")
            or re.search(r"^ADAPTER\s", alias["modelfile"], re.MULTILINE | re.IGNORECASE)):
        raise ValueError("The existing evaluation alias has different parameters, template, or instructions.")
    print(f"Ollama {version}, public {UPSTREAM} Q4_K_M, SHA256 {WEIGHTS}, alias {ALIAS}, 32K context.",
          flush=True)


@contextmanager
def exclusive_run():
    import msvcrt

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".reassessment.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("A host evaluation is already running.") from error
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def recover_snapshot(path, raw):
    # The wrapper's lock is released on exit. A separately surviving worker
    # must also exit before this immutable interrupted snapshot can be used.
    import psutil

    for process in psutil.process_iter(["cmdline"]):
        argv = process.info["cmdline"] or []
        if any("verify-qwen-live-evaluation.py" in a or "run-qwen-live-evaluation.mjs" in a for a in argv):
            if str(path).lower() in [a.lower() for a in argv]:
                raise RuntimeError(f"The worker for {path.name} is still active.")
    derived = path.with_suffix(".interrupted.json")
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    if not derived.exists():
        raw.update(status="paused", reason="recovered_interrupted_worker_snapshot",
                   original_snapshot_sha256=fingerprint)
        with derived.open("x", encoding="utf-8") as stream:
            json.dump(raw, stream, ensure_ascii=False)
    elif read_json(derived).get("original_snapshot_sha256") != fingerprint:
        raise ValueError(f"The original snapshot changed after recovery: {path.name}")
    return derived


def collect_shards(label, corpus, manifests):
    sources, done = [], set()
    for path in sorted(OUTPUT.glob(f"{label}.part[0-9][0-9][0-9].json")):
        raw = read_json(path)
        if raw["gold_sha256"] != corpus["gold_sha256"]:
            raise ValueError(f"Gold mismatch: {path.name}")
        if raw["status"] == "running":
            path = recover_snapshot(path, raw)
        # Keep failed preflight files intact. Only a successful structured
        # probe establishes the environment identity for a measured shard.
        if "probe" not in raw:
            if raw.get("cases") and any(r["status"] != "not_run" for r in raw["cases"]):
                raise ValueError(f"Attempted cases without a completed preflight: {path.name}")
            continue
        if (raw["code_manifest"]["source_sha256"], raw["evaluation_harness_manifest"]["source_sha256"]) != manifests:
            raise ValueError(f"Application or harness changed since {path.name}. Use a separate --output-dir.")
        ids = [r["id"] for r in raw["cases"] if r["status"] in ("completed", "failed")]
        if len(set(ids)) != len(ids) or done.intersection(ids):
            raise ValueError(f"Duplicate terminal case in {path.name}")
        sources.append(path)
        done.update(ids)
    return sources, done


def run_cohort(label, corpus_path, options, manifests):
    corpus = read_json(corpus_path)
    all_ids = [case["id"] for case in corpus["cases"]]
    final_path = OUTPUT / f"{label}.complete.json"
    sources, done = collect_shards(label, corpus, manifests)
    while True:
        remaining = [case_id for case_id in all_ids if case_id not in done]
        if not remaining:
            if final_path.exists():
                result = read_json(final_path)
                if (result["status"] != "completed" or result["gold_sha256"] != corpus["gold_sha256"]
                        or [r["id"] for r in result["cases"]] != all_ids):
                    raise ValueError(f"Unexpected prior aggregate: {final_path.name}")
            else:
                subprocess.run([sys.executable, VERIFY / "merge-qwen-live-runs.py", *sources,
                                "--corpus", corpus_path, "--output", final_path], cwd=ROOT, check=True)
            return final_path
        if final_path.exists():
            raise ValueError(f"The prior aggregate for {label} lacks its original terminal case shards.")
        number = next(i for i in range(1, 1000) if not (OUTPUT / f"{label}.part{i:03}.json").exists())
        path = OUTPUT / f"{label}.part{number:03}.json"
        case_args = [arg for case_id in remaining for arg in ("--case", case_id)]
        print(f"{label}: {len(done)} terminal, {len(remaining)} remaining — {path.name}", flush=True)
        subprocess.run(["node", VERIFY / "run-qwen-live-evaluation.mjs", "--python", sys.executable,
                        "--profile", "local-public-9b-deepagent", "--allow-local-endpoint", HOST + "/v1",
                        "--phase", "reassessment", "--live", "--corpus", corpus_path, *options,
                        "--max-calls", "600", "--output", path, *case_args], cwd=ROOT, check=True,
                       env={**os.environ, "A4B_EVALUATION_REQUIRE_GPU": "1"})
        raw = read_json(path)
        sources, new_done = collect_shards(label, corpus, manifests)
        if raw["status"] in ("blocked", "blocked_instrumentation", "paused") or new_done == done:
            raise RuntimeError(f"{path.name}: {raw['status']}, {raw.get('reason')}. Resume with the same command.")
        done = new_done


def main():
    global OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="check the local model and prepare its alias")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT, help="separate destination for a different build")
    args = parser.parse_args()
    OUTPUT = args.output_dir.resolve()
    with exclusive_run():
        prepare_model()
        if not args.prepare_only:
            harness = runpy.run_path(str(VERIFY / "verify-qwen-live-evaluation.py"))
            manifests = tuple(harness[name]()["source_sha256"] for name in ("code_manifest", "harness_manifest"))
            for label, corpus, options in COHORTS:
                result = run_cohort(label, corpus, options, manifests)
                observed = OUTPUT / f"{label}.observed-metrics.json"
                if not observed.exists():
                    subprocess.run([sys.executable, VERIFY / "score-qwen-reassessment.py", "--run", result,
                                    "--corpus", corpus, "--output", observed], cwd=ROOT, check=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
