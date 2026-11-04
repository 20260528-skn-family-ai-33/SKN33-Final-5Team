"""Choose deployable areas by comparing each live version to the requested commit."""
import re
import subprocess
import sys


def areas(path):
    if path.startswith(b'Frontend/'):
        return {'frontend'}
    if path.startswith(b'Backend/') or path == b'scripts/run-deferred-verifications.py':
        return {'backend'}
    if path.startswith(b'shared/') or path in {
        b'Dockerfile', b'.dockerignore', b'package.json', b'package-lock.json', b'tsconfig.json',
    }:
        return {'frontend', 'backend'}
    return set()


def changed(area, deployed, requested):
    if not re.fullmatch(r'[0-9a-f]{40}', deployed):
        return True
    if deployed == requested:
        return False
    result = subprocess.run(
        ['git', 'diff', '--name-only', '-z', '--no-renames', deployed, requested, '--'],
        capture_output=True, check=False,
    )
    return result.returncode != 0 or any(area in areas(path) for path in result.stdout.split(b'\0'))


commit, frontend, backend = sys.argv[1:]
selected = [area for area, deployed in [('frontend', frontend), ('backend', backend)] if changed(area, deployed, commit)]
print('all' if len(selected) == 2 else next(iter(selected), 'none'))
