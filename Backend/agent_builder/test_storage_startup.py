"""Real SQLite contention during first service/worker connections."""
import concurrent.futures
import sqlite3
import threading
import time
from .storage import Storage, enable_wal


def test_wal_initialization_waits_for_existing_writer(tmp_path):
    path=tmp_path/'agent-builder.sqlite'
    writer=sqlite3.connect(path)
    writer.execute('CREATE TABLE existing(value TEXT)')
    writer.execute('INSERT INTO existing VALUES (?)',('원본 값',))
    # A zero busy timeout reproduces SQLite's immediate BUSY at journal setup.
    reader=sqlite3.connect(path,timeout=0,check_same_thread=False)
    started=threading.Event()
    def initialize():
        started.set()
        enable_wal(reader,timeout=2)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(initialize)
            assert started.wait(1)
            time.sleep(.1)
            try:
                assert not future.done()
            finally:
                writer.commit()
            future.result(timeout=3)
        assert reader.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        assert reader.execute('SELECT value FROM existing').fetchone()[0]=='원본 값'
    finally:
        writer.close()
        reader.close()


def test_parallel_workers_can_initialize_the_same_new_database(tmp_path):
    for trial in range(3):
        folder=tmp_path/str(trial)
        barrier=threading.Barrier(8)
        def initialize(index):
            barrier.wait(timeout=5)
            storage=Storage(folder)
            try:return storage.new_session('local-test','local-model')['id']
            finally:storage.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            ids=list(executor.map(initialize,range(8)))
        assert len(set(ids))==8
        with sqlite3.connect(folder/'agent-builder.sqlite') as database:
            assert database.execute('SELECT count(*) FROM builder_sessions').fetchone()[0]==8
