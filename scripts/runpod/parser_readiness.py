"""Filesystem prerequisites for PDF admission and supervisor readiness (stdlib only)."""
import os
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
WORKER = Path(__file__).with_name('pod_pdf_worker.py')


def executable(value):
    return bool(value and Path(value).is_file() and os.access(value, os.X_OK))


def populated_directory(value):
    if not value:
        return False
    try:
        return Path(value).is_dir() and any(Path(value).iterdir())
    except OSError:
        return False


def pdf_readiness(python=None):
    checks = {
        'python': executable(python or os.environ.get('A4B_PDF_ROUTER_PYTHON')),
        'worker': WORKER.is_file(),
        'docling_models': populated_directory(os.environ.get('A4B_PDF_ARTIFACTS_DIR')),
        'ocr_models': populated_directory(os.environ.get('A4B_PDF_OCR_MODEL_DIR')),
    }
    if os.environ.get('A4B_MINERU_PYTHON'):
        checks['mineru_python'] = executable(os.environ['A4B_MINERU_PYTHON'])
    return {'ready': all(checks.values()), 'checks': checks, 'source_root': str(SOURCE_ROOT)}
