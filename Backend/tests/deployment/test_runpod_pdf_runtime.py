"""Restore real archives without installing GPU dependencies (Linux deployment target)."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts/runpod'
pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='GNU tools on RunPod')


def fixture(tmp_path):
    venv = tmp_path / 'local/pdf'
    archive_dir = tmp_path / 'durable/runtime-archives'
    archive_dir.mkdir(parents=True)
    source = tmp_path / 'source/pdf'
    (source / 'bin').mkdir(parents=True)
    (source / 'bin/python').symlink_to(sys.executable)
    (source / '.a4b-runtime-prefix').write_text(str(venv) + '\n')
    archive = archive_dir / 'pdf.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        bundle.add(source, arcname='pdf')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (archive_dir / 'pdf.tar.gz.sha256').write_text(digest + '  pdf.tar.gz\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('A4B_', 'EASYFLOW_DOCLING_'))}
    env.update(A4B_BASE_DIR=str(tmp_path / 'durable'), A4B_PDF_VENV=str(venv))
    return venv, archive, digest, env


def restore(env):
    return subprocess.run(['bash', str(SCRIPTS / 'pdf-runtime.sh'), 'restore'], env=env, capture_output=True)


def test_fresh_container_restore_and_repeat(tmp_path):
    venv, archive, digest, env = fixture(tmp_path)
    assert restore(env).returncode == 0
    assert (venv / '.a4b-runtime-complete').read_text().strip() == digest
    (venv / 'keep').touch()
    assert restore(env).returncode == 0
    assert (venv / 'keep').exists()


def test_corrupt_archive_fails_before_install(tmp_path):
    venv, archive, digest, env = fixture(tmp_path)
    archive.write_bytes(archive.read_bytes() + b'corruption')
    assert restore(env).returncode != 0
    assert not venv.exists()


def test_incomplete_environment_is_preserved(tmp_path):
    venv, archive, digest, env = fixture(tmp_path)
    venv.mkdir(parents=True)
    (venv / 'keep').touch()
    assert restore(env).returncode != 0
    assert (venv / 'keep').exists()
    assert not (venv / '.a4b-runtime-complete').exists()


def test_prefix_mismatch_is_rejected(tmp_path):
    venv, archive, digest, env = fixture(tmp_path)
    env['A4B_PDF_VENV'] = str(tmp_path / 'different/pdf')
    assert restore(env).returncode != 0
    assert not Path(env['A4B_PDF_VENV']).exists()


def test_network_runtime_and_conflicting_engine_are_rejected(tmp_path):
    _, _, _, env = fixture(tmp_path)
    env['EASYFLOW_DOCLING_PYTHON'] = '/old/bin/python'
    assert restore(env).returncode != 0
    env.pop('EASYFLOW_DOCLING_PYTHON')
    env['A4B_PDF_VENV'] = '/workspace/a4b/envs/pdf'
    assert restore(env).returncode != 0
