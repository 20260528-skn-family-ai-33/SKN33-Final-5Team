"""Choose deployable areas by comparing each live version to the requested commit."""
import re
import subprocess
import sys


BACKEND_SCRIPTS = {
    b'scripts/dev/import-preprocessing.py',
    b'scripts/setup/prepare_parser_models.py',
    b'scripts/setup/setup-agent-demo.sh',
    b'scripts/setup/setup-document-analysis.sh',
    b'scripts/setup/setup-document-demo.sh',
    b'scripts/setup/setup-ollama-model.sh',
    b'scripts/setup/setup-python.mjs',
    b'scripts/verify/frontend/seed-frontend-review.py',
    b'scripts/verify/verify_document_runtime.py',
    b'scripts/run/run-deferred-verifications.py',
    b'scripts/run-deferred-verifications.py',
    b'scripts/deploy/apply-builder-migrations.py',
    b'scripts/deploy/migrate-sqlite-to-postgres.py',
}


def areas(path):
    if path.startswith((b'Backend/', b'scripts/verify/api/', b'scripts/setup/ollama/', b'infrastructure/db/')) or path in BACKEND_SCRIPTS:
        return {'backend'}
    if path.startswith((b'Frontend/', b'scripts/verify/frontend/')):
        return {'frontend'}
    if path.startswith(b'shared/') or path in {
        b'infrastructure/docker/Dockerfile', b'infrastructure/docker/Dockerfile.dockerignore',
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
