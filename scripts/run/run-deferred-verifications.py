"""Run the verifier model over records saved with A4B_VERIFIER_MODE=defer.

Reads the repository-root .env (A4B_ENV_FILE selects another file; exported variables win),
uses EASYFLOW_DATA_DIR for the design DB, and fills in pending verdicts one by one.
Run it while no one is designing workflows: the verifier shares the GPU with the main model.
Usage: Backend/.venv-agent/bin/python scripts/run/run-deferred-verifications.py [--limit N]
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'Backend'))


def load_env():
    path = ROOT / os.environ.get('A4B_ENV_FILE', '.env')
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.split(' #', 1)[0].strip().strip('"\''))


def main():
    parser = argparse.ArgumentParser(description='Fill in deferred verifier verdicts.')
    parser.add_argument('--limit', type=int, default=None, help='verify at most N pending records')
    args = parser.parse_args()
    load_env()
    from verification.deferred import run_pending
    directory = ROOT / os.environ.get('EASYFLOW_DATA_DIR', 'data')
    done = run_pending(directory, limit=args.limit)
    routes = {}
    for item in done:
        routes[item['route']] = routes.get(item['route'], 0) + 1
    print(json.dumps({'data_dir': str(directory), 'verified': len(done), 'routes': routes}, ensure_ascii=False))


if __name__ == '__main__':
    main()
