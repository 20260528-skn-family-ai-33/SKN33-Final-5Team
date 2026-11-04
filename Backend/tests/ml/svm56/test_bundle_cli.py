"""Installation/diagnostic behavior; no real model or network required."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from ml.svm56 import bundle_cli as cli
from ml.advice import tool_recommendation


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    for relative, contents in [(cli.MODEL_FILE, b'fixture-model'), (cli.CATALOG_FILE, b'[]')]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    monkeypatch.setattr(cli, 'MODEL_SHA256', hashlib.sha256(b'fixture-model').hexdigest())
    monkeypatch.setattr(cli, 'CATALOG_SHA256', hashlib.sha256(b'[]').hexdigest())
    monkeypatch.setattr(cli, 'DEFAULT_BUNDLE', root)
    for key in ('A4B_TOOL_ADVICE_MODE', 'A4B_TOOL_ADVICE_BACKEND', 'A4B_SVM_MODEL_PATH',
                'A4B_SVM_CATALOG_PATH', 'A4B_SVM_EXPECTED_MODEL_SHA256'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, '_dependency_checks', lambda: [{'status': 'ok'}])
    return root


def test_check_without_probe_never_loads_model(bundle, monkeypatch):
    monkeypatch.setattr(tool_recommendation, 'recommend_for_state', lambda _: pytest.fail('must not deserialize'))
    result = cli.check_environment()
    assert result['ok'] and result['status'] == 'ready_for_probe'
    assert 'probe' not in result


@pytest.mark.parametrize('key,value,status', [
    ('A4B_TOOL_ADVICE_MODE', 'off', 'disabled'),
    ('A4B_TOOL_ADVICE_MODE', '', 'invalid_mode'),
    ('A4B_TOOL_ADVICE_BACKEND', 'legacy', 'different_backend'),
    ('A4B_SVM_EXPECTED_MODEL_SHA256', 'not-a-hash', 'invalid_expected_hash'),
])
def test_configuration_failures_do_not_load(bundle, monkeypatch, key, value, status):
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(tool_recommendation, 'recommend_for_state', lambda _: pytest.fail('must not deserialize'))
    result = cli.check_environment(probe=True)
    assert not result['ok'] and result['status'] == status


@pytest.mark.parametrize('relative', [cli.MODEL_FILE, cli.CATALOG_FILE])
def test_missing_file_is_not_disabled(bundle, monkeypatch, relative):
    (bundle / relative).unlink()
    monkeypatch.setattr(tool_recommendation, 'recommend_for_state', lambda _: pytest.fail('must not deserialize'))
    result = cli.check_environment(probe=True)
    assert result['status'] == 'missing_files'
    assert not result['ok']


@pytest.mark.parametrize('relative', [cli.MODEL_FILE, cli.CATALOG_FILE])
def test_wrong_hash_stops_before_deserialization(bundle, monkeypatch, relative):
    (bundle / relative).write_bytes(b'untrusted')
    monkeypatch.setattr(tool_recommendation, 'recommend_for_state', lambda _: pytest.fail('must not deserialize'))
    assert cli.check_environment(probe=True)['status'] == 'hash_mismatch'


def test_explicit_missing_path_is_reported(bundle, monkeypatch):
    monkeypatch.setenv('A4B_SVM_MODEL_PATH', str(bundle / 'not-installed.joblib'))
    assert cli.check_environment()['status'] == 'missing_files'


def test_candidate_hash_is_explicit_and_not_baseline(bundle, monkeypatch):
    data = b'explicit-candidate'
    (bundle / cli.MODEL_FILE).write_bytes(data)
    monkeypatch.setenv('A4B_SVM_EXPECTED_MODEL_SHA256', hashlib.sha256(data).hexdigest())
    result = cli.check_environment()
    assert result['ok'] and result['model_variant'] == 'explicit_candidate'


def test_missing_dependency_is_not_model_failure(bundle, monkeypatch):
    monkeypatch.setattr(cli, '_dependency_checks', lambda: [{'package': 'scikit-learn', 'status': 'missing'}])
    monkeypatch.setattr(tool_recommendation, 'recommend_for_state', lambda _: pytest.fail('must not deserialize'))
    assert cli.check_environment(probe=True)['status'] == 'dependency_mismatch'


@pytest.mark.parametrize('candidates,status,ok', [
    ([{'tool_id': 'read_table', 'source': 'service_contract'}], 'candidates_available', False),
    ([{'tool_id': 'filter_sort', 'source': 'svm56', 'decision_score': .8}], 'candidates_available', True),
    ([], 'failed', False),
    ([], 'needs_review', False),
])
def test_probe_requires_real_svm_candidate(bundle, monkeypatch, candidates, status, ok):
    monkeypatch.setattr(tool_recommendation, 'recommend_for_state', lambda state: {
        'ml_status': status, 'ml_candidates': candidates, 'ml_model_sha256': cli.MODEL_SHA256})
    result = cli.check_environment(probe=True)
    assert result['ok'] == ok
    assert result['status'] == ('svm_candidates_verified' if ok else 'probe_failed')
    assert result['probe']['candidates'] == candidates


def test_probe_error_does_not_expose_exception_text(bundle, monkeypatch):
    def fail(state):
        raise RuntimeError('credential-that-must-not-be-printed')
    monkeypatch.setattr(tool_recommendation, 'recommend_for_state', fail)
    result = cli.check_environment(probe=True)
    assert result['error_type'] == 'RuntimeError'
    assert 'credential-that' not in json.dumps(result)


def test_install_copies_only_pinned_files_and_does_not_modify_env(bundle, tmp_path):
    destination = tmp_path / 'installed'
    (bundle / '.env').write_text('secret=do-not-copy')
    before = dict(os.environ)
    result = cli.install_bundle(bundle, destination)
    assert result['ok'] and result['status'] == 'installed'
    assert set(p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()) == {
        cli.MODEL_FILE.as_posix(), cli.CATALOG_FILE.as_posix(), 'bundle_manifest.json'}
    assert dict(os.environ) == before
    assert (destination / cli.MODEL_FILE).read_bytes() == (bundle / cli.MODEL_FILE).read_bytes()
    manifest = json.loads((destination / 'bundle_manifest.json').read_text())
    assert manifest['files'][cli.MODEL_FILE.as_posix()] == cli.MODEL_SHA256


def test_repeat_install_is_idempotent(bundle, tmp_path):
    destination = tmp_path / 'installed'
    assert cli.install_bundle(bundle, destination)['status'] == 'installed'
    before = (destination / cli.MODEL_FILE).stat().st_mtime_ns
    assert cli.install_bundle(bundle, destination)['status'] == 'already_installed'
    assert (destination / cli.MODEL_FILE).stat().st_mtime_ns == before


def test_existing_different_model_is_never_overwritten(bundle, tmp_path):
    destination = tmp_path / 'installed'
    existing = destination / cli.MODEL_FILE
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b'keep-current-candidate')
    assert cli.install_bundle(bundle, destination)['status'] == 'destination_conflict'
    assert existing.read_bytes() == b'keep-current-candidate'
    assert not (destination / cli.CATALOG_FILE).exists()


def test_bad_source_creates_no_destination(bundle, tmp_path):
    (bundle / cli.MODEL_FILE).write_bytes(b'bad')
    destination = tmp_path / 'installed'
    assert cli.install_bundle(bundle, destination)['status'] == 'source_rejected'
    assert not destination.exists()


def test_corruption_during_copy_does_not_publish_partial_install(bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.shutil, 'copyfile', lambda source, target: Path(target).write_bytes(b'changed'))
    destination = tmp_path / 'installed'
    assert cli.install_bundle(bundle, destination)['status'] == 'copy_hash_mismatch'
    assert not destination.exists()
    assert not list(tmp_path.glob('.svm-bundle-*'))


def test_cli_reports_failure_via_exit_code(bundle, monkeypatch, capsys):
    monkeypatch.setenv('A4B_TOOL_ADVICE_MODE', 'off')
    assert cli.main(['check', '--probe']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'disabled'
