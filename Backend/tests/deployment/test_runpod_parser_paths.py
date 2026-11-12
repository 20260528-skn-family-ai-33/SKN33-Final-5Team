"""Preparation and startup share paths; no CUDA packages are installed by these tests."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts/runpod'
sys.path.insert(0, str(SCRIPTS))
from parser_readiness import executable, pdf_readiness, populated_directory


def clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith('A4B_')}


@pytest.mark.parametrize('custom', [False, True])
def test_shared_install_and_runtime_paths(tmp_path, custom):
    env = clean_env()
    if custom:
        env.update(A4B_BASE_DIR=str(tmp_path), A4B_PDF_VENV=str(tmp_path / 'venv'),
                   A4B_PDF_MODELS=str(tmp_path / 'models'))
    script = 'source "$1/parser-paths.sh"; exec "$2" -c \'import os,json; print(json.dumps({k:v for k,v in os.environ.items() if k.startswith("A4B_")}))\''
    result = subprocess.run(['bash', '-c', script, 'fixture', str(SCRIPTS), sys.executable],
                            env=env, check=True, capture_output=True, text=True)
    paths = json.loads(result.stdout)
    base = str(tmp_path) if custom else '/workspace/a4b'
    venv = str(tmp_path / 'venv') if custom else base + '/envs/pdf'
    models = str(tmp_path / 'models') if custom else base + '/models/pdf'
    assert paths['A4B_PDF_ROUTER_PYTHON'] == venv + '/bin/python'
    assert paths['A4B_PDF_ARTIFACTS_DIR'] == models + '/docling'
    assert paths['A4B_PDF_OCR_MODEL_DIR'] == models + '/docling/EasyOcr'
    assert paths['A4B_PARSER_SOURCE_ROOT'] == str(SCRIPTS.parent.parent.resolve())


def test_setup_produces_files_at_runtime_paths(tmp_path):
    env = clean_env()
    env['A4B_BASE_DIR'] = str(tmp_path)
    python = tmp_path / 'envs/pdf/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text('#!' + sys.executable + '\n' + '''
import sys
from pathlib import Path
if sys.argv[1] == '-':
    print('torch==fixture')
elif '--output' in sys.argv:
    root = Path(sys.argv[sys.argv.index('--output') + 1])
    (root / 'docling/EasyOcr').mkdir(parents=True)
    (root / 'docling/EasyOcr/model.pth').touch()
''')
    python.chmod(0o700)
    subprocess.run(['bash', str(SCRIPTS / 'setup-pdf-router.sh')], env=env, check=True, capture_output=True)
    assert (tmp_path / 'models/pdf/.docling-2.133.0-ready').is_file()
    assert (tmp_path / 'models/pdf/docling/EasyOcr/model.pth').is_file()


def test_setup_rejects_conflicting_paths_before_install(tmp_path):
    env = {**clean_env(), 'A4B_BASE_DIR': str(tmp_path), 'A4B_PDF_ROUTER_PYTHON': '/missing/python'}
    result = subprocess.run(['bash', str(SCRIPTS / 'setup-pdf-router.sh')], env=env, capture_output=True)
    assert result.returncode != 0 and not (tmp_path / 'envs').exists()


def test_readiness_requires_executable_and_nonempty_model_directory(tmp_path, monkeypatch):
    python = tmp_path / 'python'
    python.touch()
    assert not executable(str(python))
    assert not populated_directory(str(tmp_path / 'missing'))
    directory = tmp_path / 'empty'
    directory.mkdir()
    assert not populated_directory(str(directory))
    monkeypatch.setenv('A4B_PDF_ROUTER_PYTHON', str(python))
    assert not pdf_readiness()['ready']


@pytest.mark.parametrize('failure', ['missing_paths', 'old_server', 'wrong_release', 'pdf_unready'])
def test_supervisor_readiness_rejects_missing_runtime_or_wrong_server(monkeypatch, failure):
    import io
    import parser_readiness
    monkeypatch.delenv('A4B_PARSER_SOURCE_ROOT', raising=False)
    monkeypatch.setattr('final_model_profile.required_models', lambda: {'model': 'digest'})
    monkeypatch.setattr(parser_readiness, 'pdf_readiness', lambda: {'ready': failure != 'missing_paths'})
    health = dict(ok=True, device='gpu:0', pdf_jobs=True,
                  pdf_readiness={'ready': True, 'source_root': str(parser_readiness.SOURCE_ROOT)})
    if failure == 'old_server':
        health.pop('pdf_readiness')
    elif failure == 'wrong_release':
        health['pdf_readiness']['source_root'] = '/legacy/source'
    elif failure == 'pdf_unready':
        health['pdf_readiness']['ready'] = False
    def get(url, **kwargs):
        data = health if url.endswith('/health') else {'models': [{'name': 'model', 'digest': 'digest'}]}
        return io.BytesIO(json.dumps(data).encode())
    monkeypatch.setattr('urllib.request.urlopen', get)
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(SCRIPTS / 'check-final-ready.py'), run_name='__main__')
    assert error.value.code == 1
