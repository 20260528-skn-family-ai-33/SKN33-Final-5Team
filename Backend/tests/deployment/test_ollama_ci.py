"""Execute the workflow step against old and new deployment checkouts."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
MARKER = 'tests/infrastructure/test_ollama_deployment.py'


def ollama_step():
    workflow = yaml.safe_load((ROOT / '.github/workflows/deploy.yml').read_text())
    return next(step['run'] for step in workflow['jobs']['verify']['steps']
                if step.get('name') == 'Test Ollama configuration, transport and output recovery')


def run_step(directory, **environment):
    # Match GitHub Actions' bash failure propagation; no AWS or model calls are made.
    return subprocess.run(['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c', ollama_step()],
                          cwd=directory, env={**os.environ, **environment}, text=True, capture_output=True)


def test_old_deployment_without_ollama_tests_skips_before_dependency_install(tmp_path):
    # Old branches need neither the new tests nor the associated integration requirements.
    result = run_step(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'skipping' in result.stdout


@pytest.mark.parametrize('pytest_status', [0, 17])
def test_new_deployment_runs_ollama_tests_and_propagates_failure(tmp_path, pytest_status):
    marker = tmp_path / MARKER
    marker.parent.mkdir(parents=True)
    marker.touch()
    python = tmp_path / '.venv-agent/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
with open(os.environ['A4B_CI_TEST_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
sys.exit(int(os.environ['A4B_CI_TEST_EXIT']) if sys.argv[1:3] == ['-m', 'pytest'] else 0)
''')
    python.chmod(0o755)
    log = tmp_path / 'calls.jsonl'
    result = run_step(tmp_path, A4B_CI_TEST_LOG=str(log), A4B_CI_TEST_EXIT=str(pytest_status))
    assert result.returncode == pytest_status, result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls == [
        ['-m', 'pip', 'install', '-r', 'requirements/local-integration.txt'],
        ['-m', 'pytest', '-q', MARKER, 'tests/design/test_ollama_planning.py',
         'tests/design/test_deep_agent_conversation.py', 'tests/design/test_direct_conversation.py'],
    ]
