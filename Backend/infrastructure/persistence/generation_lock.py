"""Non-blocking ownership of a design session, released when a worker exits.

PostgreSQL uses a connection-scoped advisory lock across server instances.
The local SQLite adapter uses an OS lock across worker processes.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path


@contextmanager
def generation_session_lock(storage, session_id):
    identity=f'a4b:generation:{storage.workspace_id}:{session_id}'
    digest=hashlib.sha256(identity.encode()).digest()
    if storage.dialect == 'postgres':
        key=int.from_bytes(digest[:8], 'big', signed=True)
        acquired=bool(storage.db.execute('SELECT pg_try_advisory_lock(?::bigint)', (key,)).fetchone()[0])
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    storage.db.rollback()
                    storage.db.execute('SELECT pg_advisory_unlock(?::bigint)', (key,))
                except Exception:
                    # Never return a possibly locked connection to the pool.
                    storage.db.connection.close()
                    raise
        return
    directory=Path(storage.path).parent/'generation-locks'
    directory.mkdir(exist_ok=True)
    # Keep the inode: deleting a released lock file can let two workers lock different files.
    with (directory/(digest.hex()+'.lock')).open('a+b') as handle:
        acquired=False
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0,2)
                if not handle.tell():handle.write(b'0');handle.flush()
                handle.seek(0)
                try:msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1);acquired=True
                except OSError:pass
            else:
                import fcntl
                try:fcntl.flock(handle.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB);acquired=True
                except BlockingIOError:pass
            yield acquired
        finally:
            if acquired:
                if os.name == 'nt':
                    handle.seek(0);msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
