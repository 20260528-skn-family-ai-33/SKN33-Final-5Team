"""Run inside the app container to swap frontend files without restarting Node."""
import ctypes
import errno
import os
import re
import shutil
import sys
from pathlib import Path

root = Path('/app/Frontend')
active = root / 'dist-app'
pending = root / '.a4b-pending'
baseline = root / '.a4b-before-test'
journal = root / '.a4b-swap'
original = root / 'a4b-frontend-original'
release_prefix = 'a4b-frontend-'


def exchange(first, second):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(-100, os.fsencode(first), -100, os.fsencode(second), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def discard(path):
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def prune():
    kept = {path.resolve() for path in (active, baseline) if path.exists()}
    for path in root.glob(release_prefix + '*'):
        if re.fullmatch(release_prefix + r'[0-9a-f]{40}', path.name) and path not in kept:
            discard(path)
    if original not in kept:
        discard(original)


def prepare_active():
    if active.is_symlink():
        return
    if not original.exists():
        staging = original.with_suffix('.tmp')
        discard(staging)
        shutil.copytree(active, staging)
        staging.rename(original)
    # OverlayFS may reject directory renames for files inherited from an image.
    # Keep a complete copy before replacing the image directory with a link.
    discard(active)
    active.symlink_to(original.name)


def recover():
    if not active.exists() and original.is_dir():
        active.symlink_to(original.name)
    if journal.exists():
        previous, operation = journal.read_text().split()
        if pending.exists():
            if active.lstat().st_ino != int(previous):
                exchange(active, pending)
            if operation == 'restore':
                pending.rename(baseline)
            else:
                discard(pending)
        journal.unlink()
    elif pending.exists():
        discard(pending)  # Committed swap interrupted during cleanup.


def begin(operation):
    temporary = journal.with_suffix('.tmp')
    temporary.write_text(f'{active.lstat().st_ino} {operation}\n')
    temporary.replace(journal)


operation = sys.argv[1]
if operation == 'recover':
    recover()
elif operation == 'probe-baseline':
    recover()
    print('yes' if baseline.exists() else 'no')
elif operation == 'activate':
    recover()
    release = root / (release_prefix + sys.argv[2])
    if not re.fullmatch(release_prefix + r'[0-9a-f]{40}', release.name):
        raise ValueError('Invalid frontend commit')
    if not (release / 'index.html').is_file() or any(
        path.is_symlink() or not (path.is_file() or path.is_dir()) for path in release.rglob('*')
    ):
        raise ValueError('Invalid frontend build artifact')
    if active.resolve() == release:
        sys.exit(0)
    if not active.is_symlink():
        # Try the atomic path first on filesystems which support it.
        begin('activate')
        pending.symlink_to(release.name)
        try:
            exchange(active, pending)
            sys.exit(0)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            recover()
            prepare_active()
    begin('activate')
    pending.symlink_to(release.name)
    exchange(active, pending)
elif operation == 'restore':
    recover()
    if baseline.exists():
        begin('restore')
        baseline.rename(pending)
        exchange(active, pending)
elif operation in {'finish-test', 'finish-production', 'finish-restore'}:
    if operation == 'finish-test' and pending.exists() and not baseline.exists():
        pending.rename(baseline)
    journal.unlink(missing_ok=True)
    discard(pending)
    if operation == 'finish-production':
        discard(baseline)
    prune()
else:
    raise ValueError('Invalid frontend release operation')
