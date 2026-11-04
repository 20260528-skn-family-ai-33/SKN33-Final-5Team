"""Offline SVM installation/diagnostics. Never changes service configuration."""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from .svm56_service_hook import CATALOG_SHA256, DEFAULT_BUNDLE, MODEL_SHA256, _sha256

MODEL_FILE = Path('model/recommender_char_svm_5431.joblib')
CATALOG_FILE = Path('catalog/catalog_56.json')
REQUIREMENTS = Path(__file__).resolve().parents[2] / 'requirements/ml-svm56.txt'
MODULES = {'scikit-learn': 'sklearn'}
PROBE_STATE = {
    'messages': [{'role': 'user', 'content': '첨부한 CSV에서 금액이 10000 이상인 행만 골라 CSV로 저장해줘'}],
    'resources': [{'kind': 'table', 'columns': ['금액']}],
}


def _file_check(path: Path, expected: str) -> dict:
    result = {'path': str(path.absolute()), 'expected_sha256': expected}
    try:
        if not path.is_file():
            return {**result, 'status': 'missing'}
        actual = _sha256(path)
        return {**result, 'sha256': actual, 'status': 'ok' if actual == expected else 'hash_mismatch'}
    except OSError as error:
        return {**result, 'status': 'unreadable', 'error_type': type(error).__name__}


def _dependency_checks() -> list[dict]:
    result = []
    for line in REQUIREMENTS.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, expected = line.split('==', 1)
        try:
            actual = importlib.metadata.version(name)
            available = importlib.util.find_spec(MODULES.get(name, name)) is not None
            status = 'missing' if not available else 'ok' if actual == expected else 'version_mismatch'
        except (importlib.metadata.PackageNotFoundError, ImportError, ValueError):
            actual, status = None, 'missing'
        result.append({'package': name, 'expected': expected, 'installed': actual, 'status': status})
    return result


def check_environment(*, probe: bool = False) -> dict:
    """Inspect the caller's effective env; loading occurs only after hash checks."""
    mode = os.getenv('A4B_TOOL_ADVICE_MODE', 'hint')
    backend = os.getenv('A4B_TOOL_ADVICE_BACKEND', 'svm56')
    expected = os.getenv('A4B_SVM_EXPECTED_MODEL_SHA256') or MODEL_SHA256
    result = {
        'ok': False, 'mode': mode, 'backend': backend, 'python': sys.executable,
        'scope': 'current_process_only; no Qwen, workflow execution or deployed-server check',
        'model_variant': 'pinned_baseline' if expected == MODEL_SHA256 else 'explicit_candidate',
    }
    if mode == 'off':
        return {**result, 'status': 'disabled', 'next_action': '추천을 사용하려면 실제 서비스 환경의 A4B_TOOL_ADVICE_MODE를 hint로 설정하세요.'}
    if mode != 'hint':
        return {**result, 'status': 'invalid_mode', 'next_action': 'A4B_TOOL_ADVICE_MODE는 off 또는 hint입니다.'}
    if backend != 'svm56':
        return {**result, 'status': 'different_backend', 'next_action': 'SVM56을 사용하려면 실제 서비스의 A4B_TOOL_ADVICE_BACKEND를 svm56으로 설정하세요.'}
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        return {**result, 'status': 'invalid_expected_hash', 'next_action': '검증된 모델의 정확한 SHA256을 사용하세요.'}
    model_path = Path(os.getenv('A4B_SVM_MODEL_PATH') or DEFAULT_BUNDLE / MODEL_FILE)
    catalog_path = Path(os.getenv('A4B_SVM_CATALOG_PATH') or DEFAULT_BUNDLE / CATALOG_FILE)
    files = {'model': _file_check(model_path, expected), 'catalog': _file_check(catalog_path, CATALOG_SHA256)}
    result['files'] = files
    bad_files = [f['status'] for f in files.values() if f['status'] != 'ok']
    if bad_files:
        status = 'hash_mismatch' if 'hash_mismatch' in bad_files else 'unreadable_files' if 'unreadable' in bad_files else 'missing_files'
        return {**result, 'status': status, 'next_action': '팀 공유 모델·카탈로그와 경로/컨테이너 마운트를 확인하세요. 이 파일은 아직 역직렬화하지 않았습니다.'}
    result['dependencies'] = _dependency_checks()
    if any(d['status'] != 'ok' for d in result['dependencies']):
        return {**result, 'status': 'dependency_mismatch', 'next_action': '실제 서비스 Python 환경에 Backend/requirements/ml-svm56.txt의 버전을 설치하세요.'}
    if not probe:
        return {**result, 'ok': True, 'status': 'ready_for_probe', 'next_action': '--probe를 추가하면 실제 모델 로딩과 기존 진단 요청 1건의 추천을 확인합니다.'}
    try:
        # Same entry point used by both planners; preserve all mode/path settings.
        from ml.advice.tool_recommendation import recommend_for_state
        advice = recommend_for_state(PROBE_STATE)
        if not isinstance(advice, dict):
            return {**result, 'status': 'probe_failed', 'error_type': 'MissingAdvice'}
        candidates = [{k: c[k] for k in ('tool_id', 'source', 'decision_score', 'selection_threshold') if k in c}
                      for c in advice.get('ml_candidates', [])]
        result['probe'] = {
            'ml_status': advice.get('ml_status'), 'model_sha256': advice.get('ml_model_sha256'),
            'candidates': candidates,
            'error_type': advice.get('ml_error_type'),
            'note': '기존 진단 요청 1건의 연결 확인이며 정확도 평가·Qwen 전달·워크플로 성공 검증은 아닙니다.',
        }
        if advice.get('ml_status') != 'candidates_available' or not any(c.get('source') == 'svm56' for c in candidates):
            return {**result, 'status': 'probe_failed', 'next_action': 'probe.ml_status와 error_type을 확인하세요. 규칙 보완 후보만으로 SVM 성공을 판정하지 않습니다.'}
        return {**result, 'ok': True, 'status': 'svm_candidates_verified'}
    except Exception as error:
        return {**result, 'status': 'probe_failed', 'error_type': type(error).__name__,
                'next_action': '모델 로딩·카탈로그·서비스 의존성을 확인하세요.'}


def install_bundle(source: Path, destination: Path) -> dict:
    """Copy only the pinned pair. Refuse different existing files/config changes."""
    source, destination = source.absolute(), destination.absolute()
    result = {'ok': False, 'source': str(source), 'destination': str(destination)}
    expected = {MODEL_FILE: MODEL_SHA256, CATALOG_FILE: CATALOG_SHA256}
    source_checks = {str(p): _file_check(source / p, h) for p, h in expected.items()}
    result['files'] = source_checks
    if any(c['status'] != 'ok' for c in source_checks.values()):
        return {**result, 'status': 'source_rejected', 'next_action': 'main 고정 기준 모델의 model/ 및 catalog/ 폴더를 지정하세요. 설치 과정은 joblib를 실행하지 않습니다.'}
    if destination.is_symlink():
        return {**result, 'status': 'destination_conflict'}
    if destination.exists():
        matches = destination.is_dir() and all(_file_check(destination / p, h)['status'] == 'ok' for p, h in expected.items())
        return {**result, 'ok': matches, 'status': 'already_installed' if matches else 'destination_conflict',
                'next_action': '기존 파일은 변경하지 않았습니다. 다른 모델이 있으면 새 경로를 선택하세요.'}
    stage = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.svm-bundle-', dir=destination.parent))
        for path, digest in expected.items():
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / path, target)
            if _sha256(target) != digest:
                return {**result, 'status': 'copy_hash_mismatch'}
        manifest = {
            'schema_version': 1, 'bundle_id': 'svm56-baseline-20261031-df14a313',
            'files': {p.as_posix(): h for p, h in expected.items()},
            'label_columns': 56, 'trained_heads': 52,
            'untrained_tool_ids': ['reply_review', 'review_summary', 'validate_document_format', 'email.attachment.get'],
            'score_type': 'uncalibrated SVM margin, not probability',
            'label_grade': 'AI-assisted development labels; not human gold',
        }
        (stage / 'bundle_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if destination.exists() or destination.is_symlink():
            return {**result, 'status': 'destination_conflict'}
        stage.rename(destination)
        stage = None
        return {**result, 'ok': True, 'status': 'installed',
                'next_action': '파일 설치만 완료했습니다. 서비스 설정은 변경하지 않았습니다. 해당 환경에서 check --probe를 실행하세요.'}
    except OSError as error:
        return {**result, 'status': 'install_failed', 'error_type': type(error).__name__}
    finally:
        if stage is not None:
            # This directory was created by this invocation, never an existing bundle.
            shutil.rmtree(stage)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='Check current process config, files and dependencies')
    check.add_argument('--probe', action='store_true', help='Load the hash-verified model and run one existing diagnostic request')
    install = commands.add_parser('install', help='Install pinned baseline files without overwriting a different bundle')
    install.add_argument('--source', required=True, type=Path, help='Extracted team package containing model/ and catalog/')
    install.add_argument('--destination', type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args(argv)
    result = check_environment(probe=args.probe) if args.command == 'check' else install_bundle(args.source, args.destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
