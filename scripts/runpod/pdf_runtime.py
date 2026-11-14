"""Transactional PDF runtime preparation. No GPU imports in the recovery path."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import uuid

SCRIPTS = Path(__file__).resolve().parent


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def atomic_text(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('w') as output:
        output.write(value)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def exists(path):
    return path.exists() or path.is_symlink()


class Runtime:
    def __init__(self, env=None):
        self.env = dict(os.environ if env is None else env)
        self.alias = Path(self.env['A4B_PDF_VENV'])
        if not self.alias.is_absolute():
            raise ValueError('PDF runtime requires an absolute path')
        if self.alias.resolve().is_relative_to('/workspace'):
            raise ValueError('PDF runtime must be on local disk, outside /workspace')
        for key in ('A4B_PDF_ROUTER_PYTHON', 'EASYFLOW_DOCLING_PYTHON'):
            if self.env[key] != str(self.alias / 'bin/python'):
                raise ValueError('PDF worker and Docling must use the same runtime alias')
        self.archives = Path(self.env['A4B_BASE_DIR']) / 'runtime-archives'
        self.versions = self.alias.parent / ('.' + self.alias.name + '-versions')
        self.current = self.archives / 'pdf-current'

    def check_bundle(self, folder):
        metadata = json.loads((folder / 'manifest.json').read_text())
        generation = metadata['generation']
        if len(generation) != 32 or any(c not in '0123456789abcdef' for c in generation):
            raise ValueError('Invalid PDF runtime generation')
        if metadata['alias'] != str(self.alias) or metadata['prefix'] != str(self.versions / generation):
            raise ValueError('PDF runtime prefix mismatch')
        if digest(folder / 'pdf.tar.gz') != metadata['sha256']:
            raise ValueError('PDF runtime archive checksum mismatch')
        return metadata

    def publish(self, folder):
        # Both files are immutable and validated BEFORE the one atomic selector switch.
        self.check_bundle(folder)
        candidate = self.archives / ('.pdf-current-' + uuid.uuid4().hex)
        candidate.symlink_to(folder.relative_to(self.archives))
        os.replace(candidate, self.current)

    def prepare(self):
        generation = uuid.uuid4().hex
        prefix = self.versions / generation
        prefix.parent.mkdir(parents=True, exist_ok=True)
        env = {**self.env, 'A4B_PDF_VENV': str(prefix),
               'A4B_PDF_ROUTER_PYTHON': str(prefix / 'bin/python'),
               'EASYFLOW_DOCLING_PYTHON': str(prefix / 'bin/python')}
        # Never pip-install into the current environment. The version prefix is final,
        # including during setup: console-script shebangs remain valid after restoration.
        subprocess.run(['bash', str(SCRIPTS / 'setup-pdf-router.sh')], env=env, check=True)
        subprocess.run([str(prefix / 'bin/python'), '-c',
                        'import torch; from docling.document_converter import DocumentConverter; assert torch.cuda.is_available()'],
                       env=env, check=True)
        with (prefix / 'requirements.freeze.txt').open('w') as output:
            subprocess.run([str(prefix / 'bin/python'), '-m', 'pip', 'freeze'], env=env, stdout=output, check=True)
        (prefix / '.a4b-runtime-prefix').write_text(str(prefix) + '\n')
        folder = self.archives / 'pdf-versions' / generation
        folder.mkdir(parents=True)
        archive = folder / 'pdf.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            bundle.add(prefix, arcname='pdf')
        with archive.open('rb') as source:
            os.fsync(source.fileno())
        metadata = dict(generation=generation, alias=str(self.alias), prefix=str(prefix), sha256=digest(archive))
        atomic_text(folder / 'manifest.json', json.dumps(metadata, indent=2) + '\n')
        atomic_text(prefix / '.a4b-runtime-complete', metadata['sha256'] + '\n')
        self.publish(folder)
        # The running alias is deliberately unchanged. Startup's restore activates it.
        print(json.dumps({'prepared': str(prefix), 'archive': str(folder), 'activation': 'on restore'}))

    def preserve(self, path):
        if exists(path):
            backup = path.with_name(path.name + '.backup-' + uuid.uuid4().hex)
            os.replace(path, backup)
            print('Preserved previous runtime at', backup)

    def restore_tree(self, archive, prefix, checksum):
        marker = prefix / '.a4b-runtime-complete'
        recorded_prefix = prefix / '.a4b-runtime-prefix'
        if (os.access(prefix / 'bin/python', os.X_OK) and marker.is_file()
                and marker.read_text().strip() == checksum and recorded_prefix.is_file()
                and recorded_prefix.read_text().strip() == str(prefix)):
            return
        prefix.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.pdf-restore-', dir=prefix.parent) as temporary:
            stage = Path(temporary) / 'pdf'
            # These are locally built, checksum-verified venv archives, whose Python
            # executable symlinks intentionally point to the fixed base image.
            with tarfile.open(archive, 'r:gz') as bundle:
                bundle.extractall(temporary, filter='fully_trusted')
            if (stage / '.a4b-runtime-prefix').read_text().strip() != str(prefix):
                raise ValueError('Archived venv prefix mismatch')
            if not os.access(stage / 'bin/python', os.X_OK):
                raise ValueError('Archived Python is not executable')
            atomic_text(stage / '.a4b-runtime-complete', checksum + '\n')
            # A crash between these renames leaves prefix absent, so the next restore
            # retries from the same verified archive. The previous directory is retained.
            self.preserve(prefix)
            os.replace(stage, prefix)

    def restore(self):
        if exists(self.current):
            folder = self.current.resolve(strict=True)
            if folder.parent != (self.archives / 'pdf-versions').resolve():
                raise ValueError('Invalid PDF runtime selector')
            metadata = self.check_bundle(folder)
            prefix = Path(metadata['prefix'])
            self.restore_tree(folder / 'pdf.tar.gz', prefix, metadata['sha256'])
            if self.alias.is_symlink() and self.alias.resolve() == prefix:
                return
            self.alias.parent.mkdir(parents=True, exist_ok=True)
            candidate = self.alias.with_name('.' + self.alias.name + '-next-' + uuid.uuid4().hex)
            candidate.symlink_to(prefix)
            # Migrating the original fixed directory requires two renames. If interrupted
            # after preservation, the durable selector lets the next restore finish.
            if exists(self.alias) and not self.alias.is_symlink():
                self.preserve(self.alias)
            os.replace(candidate, self.alias)
        else:
            # Backward-compatible recovery for the original deployed fixed-prefix bundle.
            archive = self.archives / 'pdf.tar.gz'
            checksum, filename = (self.archives / 'pdf.tar.gz.sha256').read_text().split()
            if filename != 'pdf.tar.gz' or digest(archive) != checksum:
                raise ValueError('Legacy PDF runtime archive checksum mismatch')
            self.restore_tree(archive, self.alias, checksum)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('prepare', 'restore'):
        raise SystemExit('Usage: pdf-runtime.sh prepare|restore')
    runtime = Runtime()
    runtime.archives.mkdir(parents=True, exist_ok=True)
    with (runtime.archives / 'pdf-runtime.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        getattr(runtime, sys.argv[1])()


if __name__ == '__main__':
    main()
