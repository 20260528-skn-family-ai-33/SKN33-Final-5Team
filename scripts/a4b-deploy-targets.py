#!/usr/bin/env python3
"""Select components by comparing their actual deployed commits to the requested commit."""
import json
import re
import subprocess
import sys
from pathlib import Path

COMPONENTS = ('frontend', 'backend', 'ocr')
OCR_FILES = {
    'Backend/documents/analysis/engine_worker.py',
    'Backend/documents/analysis/tables.py',
    'Backend/requirements/paddle-cpu.txt',
    'scripts/runpod/gpu_parser_server.py',
}


def areas(path):
    if path.startswith('Frontend/'):
        return {'frontend'}
    if path.startswith('ocr_service/') or path in OCR_FILES:
        return {'backend', 'ocr'} if path.startswith('Backend/documents/') else {'ocr'}
    if path.startswith('Backend/'):
        return {'backend'}
    if path.startswith('shared/') or path in {'package.json', 'package-lock.json', 'tsconfig.json'}:
        return {'frontend', 'backend'}
    if path.startswith('.github/workflows/') or path in {'Dockerfile', '.dockerignore', 'docker-compose.production.yml'}:
        return set(COMPONENTS)
    if path == 'scripts/run-deferred-verifications.py':
        return {'backend'}
    return set()


def changed(component, previous, commit):
    if not re.fullmatch('[0-9a-f]{40}', previous):
        return True
    if previous == commit:
        return False
    try:
        paths = subprocess.check_output(['git', 'diff', '--name-only', '-z', '--no-renames', previous, commit, '--'])
    except subprocess.CalledProcessError:
        return True  # A deleted branch may no longer be fetchable.
    return any(component in areas(path.decode()) for path in paths.split(b'\0') if path)


def main():
    state_path, target, commit, mode = sys.argv[1:]
    deployed = json.loads(Path(state_path).read_text())
    if not deployed and (mode != 'production' or target not in ('all', 'auto')):
        sys.exit('Run the first split deployment with branch=main and target=all.')
    if not deployed or target == 'all':
        selected = COMPONENTS
    elif target == 'auto':
        selected = [name for name in COMPONENTS if changed(name, deployed.get(name, {}).get('commit', ''), commit)]
    elif target in COMPONENTS:
        selected = [target]
    else:
        sys.exit('Invalid deployment target')
    print(json.dumps(selected, separators=(',', ':')))


if __name__ == '__main__':
    main()
