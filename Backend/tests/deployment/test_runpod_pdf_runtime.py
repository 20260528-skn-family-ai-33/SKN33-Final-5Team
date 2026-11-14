"""Restore real archives without installing GPU dependencies (Linux deployment target)."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts/runpod'
sys.path.insert(0, str(SCRIPTS))
import pdf_runtime
from pdf_runtime import Runtime


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


def test_incomplete_environment_is_preserved_and_recovered(tmp_path):
    venv, archive, digest, env = fixture(tmp_path)
    venv.mkdir(parents=True)
    (venv / 'keep').touch()
    assert restore(env).returncode == 0
    backups = list(venv.parent.glob('pdf.backup-*'))
    assert len(backups) == 1 and (backups[0] / 'keep').exists()
    assert (venv / '.a4b-runtime-complete').read_text().strip() == digest


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


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    venv, archive, checksum, env = fixture(tmp_path)
    env.update(A4B_PDF_ROUTER_PYTHON=str(venv / 'bin/python'),
               EASYFLOW_DOCLING_PYTHON=str(venv / 'bin/python'))
    runtime = Runtime(env)
    def install(command, **kwargs):
        if command[0] == 'bash':
            prefix = Path(kwargs['env']['A4B_PDF_VENV'])
            (prefix / 'bin').mkdir(parents=True)
            python = prefix / 'bin/python'
            python.symlink_to(sys.executable)
            # A console entrypoint with a genuinely absolute shebang.
            (prefix / 'bin/tool').write_text('#!' + str(python) + '\nimport os; print(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))\n')
            (prefix / 'bin/tool').chmod(0o700)
        elif 'stdout' in kwargs:
            kwargs['stdout'].write('fixture==1\n')
    monkeypatch.setattr(pdf_runtime.subprocess, 'run', install)
    runtime.archives.mkdir(parents=True, exist_ok=True)
    runtime.prepare()
    runtime.restore()
    return runtime, install


@pytest.mark.parametrize('failure', ['install', 'cuda', 'freeze', 'archive', 'manifest', 'publish'])
def test_prepare_failure_keeps_previous_generation_usable(prepared, monkeypatch, failure):
    runtime, install = prepared
    previous = runtime.current.resolve()
    prefix = runtime.alias.resolve()
    marker = (prefix / '.a4b-runtime-complete').read_bytes()
    def run(command, **kwargs):
        if ((failure == 'install' and command[0] == 'bash') or
            (failure == 'cuda' and '-c' in command) or
            (failure == 'freeze' and 'stdout' in kwargs)):
            raise RuntimeError('injected preparation failure')
        return install(command, **kwargs)
    monkeypatch.setattr(pdf_runtime.subprocess, 'run', run)
    original_add = tarfile.TarFile.add
    def add(self, *args, **kwargs):
        original_add(self, *args, **kwargs)
        if failure == 'archive':
            raise RuntimeError('interrupted after archive write')
    monkeypatch.setattr(tarfile.TarFile, 'add', add)
    original_text = pdf_runtime.atomic_text
    def write(path, value):
        if failure == 'manifest' and path.name == 'manifest.json':
            raise RuntimeError('interrupted before checksum publication')
        original_text(path, value)
    monkeypatch.setattr(pdf_runtime, 'atomic_text', write)
    original_replace = os.replace
    def replace(source, target):
        if failure == 'publish' and Path(target) == runtime.current:
            raise RuntimeError('interrupted before selector switch')
        original_replace(source, target)
    monkeypatch.setattr(pdf_runtime.os, 'replace', replace)
    with pytest.raises(RuntimeError):
        runtime.prepare()
    assert runtime.current.resolve() == previous
    assert runtime.alias.resolve() == prefix
    assert (prefix / '.a4b-runtime-complete').read_bytes() == marker
    # Force recovery from the old archive, as on an empty container.
    import shutil
    shutil.rmtree(prefix)
    runtime.restore()
    assert runtime.alias.resolve() == prefix
    assert (prefix / '.a4b-runtime-complete').read_bytes() == marker


def test_successful_prepare_keeps_live_alias_until_restore(prepared):
    runtime, _ = prepared
    previous_prefix = runtime.alias.resolve()
    previous_bundle = runtime.current.resolve()
    runtime.prepare()
    new_prefix = Path(runtime.check_bundle(runtime.current.resolve())['prefix'])
    assert runtime.alias.resolve() == previous_prefix
    assert previous_prefix.exists() and previous_bundle.exists()
    # Reconstruct the new environment from the archive at its original version prefix.
    import shutil
    shutil.rmtree(new_prefix)
    runtime.restore()
    assert runtime.alias.resolve() == new_prefix
    assert previous_prefix.exists() and previous_bundle.exists()
    assert (runtime.alias / 'bin/tool').read_text().startswith('#!' + str(new_prefix / 'bin/python'))
    process = subprocess.Popen([str(runtime.alias / 'bin/tool')], stdout=subprocess.PIPE, text=True)
    result, _ = process.communicate(timeout=10)
    assert process.returncode == 0 and result.strip() == str(new_prefix)


@pytest.mark.parametrize('after_switch', [False, True])
def test_sigkill_during_publication_recovers_complete_generation(prepared, after_switch):
    runtime, _ = prepared
    previous_bundle = runtime.current.resolve()
    previous_prefix = runtime.alias.resolve()
    # Make another valid immutable bundle, then reset the selector to the old one.
    runtime.prepare()
    new_bundle = runtime.current.resolve()
    runtime.publish(previous_bundle)
    code = """
import os,signal,sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import pdf_runtime
r=pdf_runtime.Runtime(json.loads(sys.argv[2]))
original=os.replace
def replace(source,target):
    if Path(target)==r.current:
        if sys.argv[4]=='True': original(source,target)
        os.kill(os.getpid(),signal.SIGKILL)
    original(source,target)
pdf_runtime.os.replace=replace
r.publish(Path(sys.argv[3]))
"""
    process = subprocess.Popen([sys.executable, '-c', code, str(SCRIPTS),
                                __import__('json').dumps(runtime.env), str(new_bundle), str(after_switch)])
    assert process.wait(timeout=10) == -9
    expected = new_bundle if after_switch else previous_bundle
    assert runtime.current.resolve() == expected
    runtime.restore()
    assert runtime.alias.resolve() == Path(runtime.check_bundle(expected)['prefix'])
    assert previous_prefix.exists() and previous_bundle.exists()


def test_interrupted_legacy_directory_activation_is_retryable(tmp_path, monkeypatch):
    venv, _, checksum, env = fixture(tmp_path)
    env.update(A4B_PDF_ROUTER_PYTHON=str(venv / 'bin/python'),
               EASYFLOW_DOCLING_PYTHON=str(venv / 'bin/python'))
    runtime = Runtime(env)
    venv.mkdir(parents=True)
    (venv / 'keep').touch()
    original = os.replace
    def replace(source, target):
        if Path(target) == venv:
            raise RuntimeError('interrupted after old directory preservation')
        original(source, target)
    monkeypatch.setattr(pdf_runtime.os, 'replace', replace)
    with pytest.raises(RuntimeError):
        runtime.restore()
    assert not venv.exists()
    monkeypatch.setattr(pdf_runtime.os, 'replace', original)
    runtime.restore()
    assert (venv / '.a4b-runtime-complete').read_text().strip() == checksum
    assert any((p / 'keep').exists() for p in venv.parent.glob('pdf.backup-*'))


def test_interrupted_legacy_to_versioned_activation_is_retryable(prepared, monkeypatch):
    runtime, _ = prepared
    selected = runtime.alias.resolve()
    runtime.alias.unlink()
    runtime.alias.mkdir()
    (runtime.alias / 'legacy-data').touch()
    original = os.replace
    def replace(source, target):
        if Path(target) == runtime.alias:
            raise RuntimeError('interrupted before alias activation')
        original(source, target)
    monkeypatch.setattr(pdf_runtime.os, 'replace', replace)
    with pytest.raises(RuntimeError):
        runtime.restore()
    assert not runtime.alias.exists()
    monkeypatch.setattr(pdf_runtime.os, 'replace', original)
    runtime.restore()
    assert runtime.alias.resolve() == selected
    assert any((p / 'legacy-data').exists() for p in runtime.alias.parent.glob('pdf.backup-*'))
